"""Notification metadata comes from the exact MP transfer records, without scraping."""

from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from .ownership import SUBTITLES


SEASON_IN_PATH = re.compile(r"(?:^|/)(?:season[\s._-]*|s)(\d{1,3})(?=/|$)", re.I)
EPISODE_IN_PATH = re.compile(r"(?:^|[/_.\s-])(?:episode|ep|e)[\s._-]*(\d{1,4})(?=[\s._-]|$)", re.I)


def _scalar(value):
    return value.strip() if isinstance(value, str) else ""


def _subtitle(*values):
    for value in values:
        if isinstance(value, str) and PurePosixPath(value.strip()).suffix.casefold() in SUBTITLES:
            return True
    return False


def _season_label(value):
    value = _scalar(value)
    if not value:
        return ""
    if value.isdigit() or re.fullmatch(r"[sS]\d{1,3}", value):
        return "S" + value.lstrip("sS").zfill(2)
    return value


def _episode_label(value):
    value = _scalar(value)
    if not value:
        return ""
    if value.isdigit() or re.fullmatch(r"[eE]\d{1,4}", value):
        return "E" + value.lstrip("eE").zfill(2)
    return value


def _video_members(job):
    """Video rows only; subtitle rows must never add a season/episode scope."""
    members = job.get("media_files")
    if isinstance(members, list):
        videos = [m for m in members if isinstance(m, dict)
                  and not _subtitle(m.get("file"), m.get("relative"), m.get("local"))
                  and _scalar(m.get("type")).casefold() not in ("subtitle", "字幕")]
        if videos:
            return videos
    files = job.get("files")
    if isinstance(files, list):
        return [entry for entry in files if isinstance(entry, dict)
                and not _subtitle(entry.get("relative"), entry.get("local"))]
    return []


def scope_context(job):
    """Season/episode context from actual video metadata or video paths only."""
    seasons, episodes = [], []
    for member in _video_members(job):
        season = _season_label(member.get("seasons"))
        if not season:
            for key in ("file", "relative", "local"):
                match = SEASON_IN_PATH.search(_scalar(member.get(key)))
                if match:
                    season = _season_label(match.group(1))
                    break
        episode = _episode_label(member.get("episodes"))
        if not episode:
            for key in ("file", "relative", "local"):
                match = EPISODE_IN_PATH.search(_scalar(member.get(key)))
                if match:
                    episode = _episode_label(match.group(1))
                    break
        if season and season.casefold() not in {item.casefold() for item in seasons}:
            seasons.append(season)
        if episode and episode.casefold() not in {item.casefold() for item in episodes}:
            episodes.append(episode)
    if not seasons and not episodes:
        return ""
    if not seasons:
        return "、".join(episodes[:8])
    label = "、".join(seasons[:6])
    if len(seasons) == 1 and episodes:
        label += " · " + "、".join(episodes[:8])
    return label


def history_media(row):
    result = {}
    for key in ("title", "type", "year", "seasons", "episodes", "image", "tmdbid"):
        value = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            result[key] = str(value).strip()
    return result


def poster_url(value):
    try:
        parts = urlsplit(value or "")
        if parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def notification_media(job, item):
    members = job.get("media_files", [])
    if item.get("file"):
        members = [member for member in members if member["file"] == item["file"]]
    groups = {}
    for member in members:
        if not member.get("title"):
            continue
        identity = tuple(member.get(key, "") for key in ("title", "year", "type", "tmdbid"))
        groups.setdefault(identity, []).append(member)
    if not groups:
        # Before a complete manifest exists, the first event identifies the work,
        # not the batch's episode range. Never report its single episode as all episodes.
        media = job.get("media", {})
        label = media.get("title") or job["title"]
        if media.get("year"):
            label += " (" + media["year"] + ")"
        if media.get("type"):
            label += " · " + media["type"]
        return [label], poster_url(media.get("image"))
    lines, image = [], None
    for (title, year, kind, _), rows in groups.items():
        label = title + (" (" + year + ")" if year else "")
        if kind:
            label += " · " + kind
        # Keep MP's per-file ranges and gaps; subtitles cannot inflate episode counts.
        videos = [r for r in rows if not _subtitle(r.get("file"), r.get("relative"), r.get("local"))]
        scopes = sorted({_scalar(r.get("seasons")) + _scalar(r.get("episodes"))
                         for r in videos} - {""}) if videos else []
        if scopes:
            label += " · " + "、".join(scopes[:12])
            if len(scopes) > 12:
                label += f" 等 {len(scopes)} 项"
        lines.append(label)
        if len(groups) == 1:
            image = next((poster_url(r.get("image")) for r in rows if poster_url(r.get("image"))), None)
    return lines, image
