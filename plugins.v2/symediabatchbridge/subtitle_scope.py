"""Conservative subtitle-to-video attribution.

The single rule is that a subtitle is kept only when there is positive evidence
that it belongs to at least one of the provided video candidates.  A matching
parent directory alone is never evidence: torrents routinely contain a stray
"next season" subtitle beside this season's videos.

Exports (frozen for the migration controller):

``partition(candidates) -> (kept, unmatched)``
    Classify candidate dicts.  Videos/non-subtitles are always kept.  A
    subtitle goes to ``unmatched`` only when it definitively cannot belong to
    any video candidate (including the case where there is no evidence).
    Both lists contain the original dictionaries in their original order; the
    function never mutates them.

``matches(subtitle, videos) -> bool``
    True only for positive, conservative attribution evidence.

``reason(subtitle, videos) -> str | None``
    ``None`` when matched, otherwise a stable reason code suitable for a
    journal/excluded_attachments entry.

``evidence(subtitle, videos) -> dict``
    JSON-safe explanation (matched flag, basis or reason, chosen video id/path,
    parsed scopes).

Metadata preference
-------------------
Authenticated per-file MP media (``tmdbid``, ``type``, ``title``, ``seasons``,
``episodes``) takes precedence over filename tokens.  An explicit conflict
between the two is treated as ambiguous and rejected.  When media is absent or
incomplete, conservative ``S01E01`` / ``Season 1`` / episode-range tokens and
the legacy same-directory filename stem are used.  Title/basename similarity is
never sufficient across different parent directories unless the work identity
is authenticated by an equal ``tmdbid`` and the episode scope overlaps.
"""

from pathlib import PurePosixPath
import re

SUBTITLES = {'.srt', '.ass', '.ssa', '.sub', '.idx', '.sup', '.vtt'}

_TV_TYPES = {'tv', 'series', 'show', '电视剧', '剧集', '电视', '动漫', '动画',
             'anime', '综艺', 'documentary', '纪录片'}
_MOVIE_TYPES = {'movie', 'film', '电影', '影片'}
_SUBTITLE_DIRS = {'sub', 'subs', 'subtitle', 'subtitles', '字幕', '字幕组'}
_LANGUAGE_TAGS = {
    'zh', 'zho', 'chi', 'chs', 'cht', 'sc', 'tc', 'cn', 'tw', 'hk',
    'en', 'eng', 'jpn', 'jp', 'ja', 'kor', 'kr', 'ko', 'fre', 'fra', 'fr',
    'ger', 'deu', 'de', 'spa', 'es', 'ita', 'it', 'rus', 'ru', 'por', 'pt',
    '中字', '中文', '简体', '繁体', '简中', '繁中', '双语', '中英', '英字',
    '字幕', 'forced', 'sdh', 'cc', 'default', 'commentary', 'signs', 'songs',
}
_LANG_SUFFIX = re.compile(r'^[a-z]{2,3}(?:[-_][a-z0-9]+)*$')
_RANGE = r'\s*(?:-|~|–|—|至|到|to)\s*'


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _path(value):
    return PurePosixPath(str(value or '').replace('\\', '/'))


def _is_subtitle(candidate):
    return _path(_get(candidate, 'local') or _get(candidate, 'source')).suffix.lower() in SUBTITLES


def _media(candidate):
    media = _get(candidate, 'media', {})
    if isinstance(media, dict):
        return media
    if hasattr(media, '__dict__'):
        return vars(media)
    return {}


def _text(value):
    return str(value or '').strip()


def _normal_title(value):
    return re.sub(r'[\W_]+', '', _text(value).casefold())


def _tmdbid(value):
    raw = _text(value)
    if raw.isdigit():
        number = int(raw)
        return str(number) if number else ''
    return raw.casefold()


def _kind(value):
    text = _text(value).casefold()
    if text in _TV_TYPES:
        return 'tv'
    if text in _MOVIE_TYPES:
        return 'movie'
    return None


def _expand(start, end, limit=500):
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return set()
    if start <= 0 or end < start or end - start > limit:
        return set()
    return set(range(start, end + 1))


def _numbers(text, kind):
    """Parse explicit season/episode tokens and ranges from a string."""
    text = _text(text)
    found = set()
    if not text:
        return found
    if kind == 'season':
        patterns = (
            re.compile(r'(?<![A-Za-z0-9])S(?:EASON)?\s*(\d{1,3})(?:' + _RANGE +
                       r'S?(?:EASON)?\s*(\d{1,3}))?', re.IGNORECASE),
            re.compile(r'第\s*(\d{1,3})\s*季'),
        )
    else:
        patterns = (
            # E follows the season number in S01E02, but a word suffix such
            # as Scene2 is not an additional E02 episode.
            re.compile(r'(?<![A-Za-z])(?:EPISODE|EP|E)\s*(\d{1,4})(?:' + _RANGE +
                       r'(?:EPISODE|EP|E)?\s*(\d{1,4}))?', re.IGNORECASE),
            re.compile(r'第\s*(\d{1,4})\s*[集话話]'),
        )
    for pattern in patterns:
        for match in pattern.finditer(text):
            first, last = match.group(1), match.group(2) if match.lastindex and match.lastindex > 1 else None
            if last:
                found |= _expand(first, last)
            else:
                try:
                    found.add(int(first))
                except (TypeError, ValueError):
                    pass
    return found


def _media_numbers(value, kind):
    """Parse a media field; bare numbers are safe because the field is typed."""
    text = _text(value)
    found = _numbers(text, kind)
    if found or not text:
        return found
    # Some legacy MP rows store seasons/episodes without an S/E prefix.
    for part in re.split(r'[,，、/&]+', text):
        part = part.strip()
        match = re.fullmatch(r'(\d{1,4})(?:' + _RANGE + r'(\d{1,4}))?', part)
        if match:
            if match.group(2):
                found |= _expand(match.group(1), match.group(2))
            else:
                found.add(int(match.group(1)))
    return found


def _basename_scope(path):
    name = path.name
    if '.' in name:
        name = name.rsplit('.', 1)[0]
    seasons = _numbers(name, 'season')
    episodes = _numbers(name, 'episode')
    # Common "1x01" form, bounded so 1920x1080 is not mistaken for episode 1080.
    for match in re.finditer(r'(?<![\dA-Za-z])(\d{1,2})x(\d{1,3})(?!\d)', name, re.IGNORECASE):
        seasons.add(int(match.group(1)))
        episodes.add(int(match.group(2)))
    return seasons, episodes


def _filename_scope(candidate):
    """Season/episode tokens from both local output and original source names."""
    names = []
    for value in (_get(candidate, 'local'), _get(candidate, 'source')):
        if value:
            scope = _basename_scope(_path(value))
            if scope not in names:
                names.append(scope)
    seasons, episodes, conflict = set(), set(), False
    for name_seasons, name_episodes in names:
        seasons |= name_seasons
        episodes |= name_episodes
    for index, (seasons_a, episodes_a) in enumerate(names):
        for seasons_b, episodes_b in names[index + 1:]:
            if seasons_a and seasons_b and not seasons_a & seasons_b:
                conflict = True
            if episodes_a and episodes_b and not episodes_a & episodes_b:
                conflict = True
    return seasons, episodes, conflict


def _scope(candidate):
    media = _media(candidate)
    media_seasons = _media_numbers(media.get('seasons'), 'season')
    media_episodes = _media_numbers(media.get('episodes'), 'episode')
    file_seasons, file_episodes, file_conflict = _filename_scope(candidate)
    conflict = file_conflict
    conflict = conflict or bool(media_seasons and file_seasons and not media_seasons & file_seasons)
    conflict = conflict or bool(media_episodes and file_episodes and not media_episodes & file_episodes)
    return {
        'seasons': media_seasons | file_seasons,
        'episodes': media_episodes | file_episodes,
        'media_seasons': media_seasons,
        'media_episodes': media_episodes,
        'file_seasons': file_seasons,
        'file_episodes': file_episodes,
        'conflict': conflict,
    }


def _candidate_kind(candidate, scope):
    media_kind = _kind(_media(candidate).get('type'))
    if media_kind:
        return media_kind
    if scope['seasons'] or scope['episodes']:
        return 'tv'
    return None


def _identity(candidate):
    media = _media(candidate)
    return {
        'tmdbid': _tmdbid(media.get('tmdbid')),
        'title': _normal_title(media.get('title')),
        'year': _text(media.get('year')),
        'kind': _kind(media.get('type')),
    }


def _parent_text(path):
    return str(_path(path).parent).casefold()


def _same_parent_or_subdir(subtitle, video):
    sub_paths = [p for p in (_get(subtitle, 'local'), _get(subtitle, 'source')) if p]
    video_paths = [p for p in (_get(video, 'local'), _get(video, 'source')) if p]
    for sub in sub_paths:
        sub_path = _path(sub)
        for vid in video_paths:
            video_path = _path(vid)
            if str(sub_path.parent).casefold() == str(video_path.parent).casefold():
                return True
            try:
                relative = sub_path.relative_to(video_path.parent)
            except ValueError:
                continue
            if any(part.casefold() in _SUBTITLE_DIRS for part in relative.parts[:-1]):
                return True
    return False


def _trim_subtitle_tags(stem):
    parts = stem.split('.')
    while len(parts) > 1:
        token = parts[-1].strip().casefold()
        if token in _LANGUAGE_TAGS or _LANG_SUFFIX.fullmatch(token):
            parts.pop()
        else:
            break
    return '.'.join(parts)


def _stem_related(subtitle, video):
    sub_paths = [p for p in (_get(subtitle, 'local'), _get(subtitle, 'source')) if p]
    video_paths = [p for p in (_get(video, 'local'), _get(video, 'source')) if p]
    for sub in sub_paths:
        sub_stem = _trim_subtitle_tags(_path(sub).stem).casefold()
        if not sub_stem:
            continue
        for vid in video_paths:
            video_stem = _trim_subtitle_tags(_path(vid).stem).casefold()
            if not video_stem:
                continue
            if sub_stem == video_stem:
                return True
            # The subtitle suffix is often an arbitrary release marker
            # ("video.later"). A delimiter is required, so video2 is never a
            # companion of video.
            if sub_stem.startswith(video_stem + '.') or video_stem.startswith(sub_stem + '.'):
                return True
    return False


def _evaluate(subtitle, video):
    """Return (matched, basis_or_reason) for one subtitle/video pair."""
    s_scope, v_scope = _scope(subtitle), _scope(video)
    if s_scope['conflict'] or v_scope['conflict']:
        return False, 'metadata_filename_conflict'
    s_id, v_id = _identity(subtitle), _identity(video)
    s_kind = _candidate_kind(subtitle, s_scope)
    v_kind = _candidate_kind(video, v_scope)
    if s_kind and v_kind and s_kind != v_kind:
        return False, 'type_conflict'
    if s_id['tmdbid'] and v_id['tmdbid'] and s_id['tmdbid'] != v_id['tmdbid']:
        return False, 'tmdb_conflict'
    same_tmdb = bool(s_id['tmdbid'] and v_id['tmdbid']
                     and s_id['tmdbid'] == v_id['tmdbid'])
    if not same_tmdb and s_id['title'] and v_id['title'] and s_id['title'] != v_id['title']:
        return False, 'title_conflict'
    if not same_tmdb and s_id['year'] and v_id['year'] and s_id['year'] != v_id['year']:
        return False, 'year_conflict'
    if s_scope['seasons'] and v_scope['seasons'] and not s_scope['seasons'] & v_scope['seasons']:
        return False, 'season_conflict'
    if s_scope['episodes'] and v_scope['episodes'] and not s_scope['episodes'] & v_scope['episodes']:
        return False, 'episode_conflict'

    same_parent = _same_parent_or_subdir(subtitle, video)
    stem_related = _stem_related(subtitle, video)
    kind = s_kind or v_kind
    exact_episode = bool(s_scope['episodes'] and v_scope['episodes']
                         and s_scope['episodes'] & v_scope['episodes'])

    if kind == 'movie':
        # Parent directory alone is never enough: unrelated movie subtitles
        # routinely share a folder.  Require work identity and a meaningful
        # filename relation (a Subs/ subdirectory is not enough by itself).
        if same_tmdb and stem_related:
            return True, 'movie_identity'
        if s_id['title'] and v_id['title'] and s_id['title'] == v_id['title'] and same_parent and stem_related:
            return True, 'movie_filename'
        # A media-less legacy companion is still proven by the same directory
        # and filename stem; unrelated movie subtitles share neither.
        if not (s_id['tmdbid'] or s_id['title']) and same_parent and stem_related:
            return True, 'legacy_filename_stem'
        return False, 'no_attribution_evidence'

    # Exact authenticated per-file media may cross parent directories.  Without
    # tmdbid the same title is not strong enough across parents.
    exact_season = bool(s_scope['seasons'] and v_scope['seasons']
                        and s_scope['seasons'] & v_scope['seasons'])
    if same_tmdb and exact_season and exact_episode:
        return True, 'exact_media_scope'
    if same_parent and stem_related:
        return True, 'legacy_filename_stem'
    if same_parent and exact_episode and (same_tmdb or (s_id['title'] and s_id['title'] == v_id['title'])):
        return True, 'same_parent_scope'
    return False, 'no_attribution_evidence'


_REASON_PRIORITY = (
    'metadata_filename_conflict', 'tmdb_conflict', 'type_conflict', 'year_conflict',
    'title_conflict', 'season_conflict', 'episode_conflict',
    'no_attribution_evidence', 'not_subtitle', 'no_video_candidates',
)


def _videos(videos):
    return [video for video in (videos or [])
            if video is not None and not _is_subtitle(video)]


def matches(subtitle, videos):
    """True only when positive evidence ties the subtitle to a video."""
    if not _is_subtitle(subtitle):
        return False
    candidates = _videos(videos)
    if not candidates:
        return False
    for video in candidates:
        if _evaluate(subtitle, video)[0]:
            return True
    return False


def reason(subtitle, videos):
    """Stable reason code when unmatched, or None when matched."""
    if not _is_subtitle(subtitle):
        return 'not_subtitle'
    candidates = _videos(videos)
    if not candidates:
        return 'no_video_candidates'
    reasons = []
    for video in candidates:
        matched, detail = _evaluate(subtitle, video)
        if matched:
            return None
        reasons.append(detail)
    for candidate_reason in _REASON_PRIORITY:
        if candidate_reason in reasons:
            return candidate_reason
    return 'no_attribution_evidence'


def evidence(subtitle, videos):
    """JSON-safe explanation used by reports and journal entries."""
    candidates = _videos(videos)
    scope = _scope(subtitle) if _is_subtitle(subtitle) else {}
    for video in candidates:
        matched, detail = _evaluate(subtitle, video) if _is_subtitle(subtitle) else (False, 'not_subtitle')
        if matched:
            return {
                'matched': True,
                'basis': detail,
                'subtitle': {
                    'local': _text(_get(subtitle, 'local')),
                    'source': _text(_get(subtitle, 'source')),
                    'seasons': sorted(scope.get('seasons', set())),
                    'episodes': sorted(scope.get('episodes', set())),
                },
                'video': {
                    'local': _text(_get(video, 'local')),
                    'source': _text(_get(video, 'source')),
                    'history_id': _get(video, 'history_id'),
                },
            }
    return {
        'matched': False,
        'reason': reason(subtitle, videos),
        'subtitle': {
            'local': _text(_get(subtitle, 'local')),
            'source': _text(_get(subtitle, 'source')),
            'seasons': sorted(scope.get('seasons', set())),
            'episodes': sorted(scope.get('episodes', set())),
        },
    }


def partition(candidates):
    """Return ``(kept, unmatched)``, preserving original dicts and ordering.

    ``candidates`` must include the video candidates, otherwise there is no
    evidence to attribute any subtitle and every subtitle is unmatched.  The
    migration controller passes the complete owned scope for this reason.
    """
    candidates = list(candidates or [])
    videos = [candidate for candidate in candidates if not _is_subtitle(candidate)]
    kept, unmatched = [], []
    for candidate in candidates:
        if not _is_subtitle(candidate):
            kept.append(candidate)
        elif videos and matches(candidate, videos):
            kept.append(candidate)
        else:
            unmatched.append(candidate)
    return kept, unmatched
