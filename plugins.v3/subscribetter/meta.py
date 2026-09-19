"""Bounded corrections after native parsing; no recognition, network or hot-path storage."""
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


FIELDS = ("title", "org_string", "subtitle", "isfile", "cn_name", "en_name", "original_name", "year", "type",
          "begin_season", "end_season", "total_season", "begin_episode", "end_episode", "total_episode",
          "media_source", "media_id", "episode_group", "apply_words", "part", "resource_type",
          "resource_effect", "resource_pix", "resource_team", "customization", "web_source",
          "video_encode", "video_bit", "audio_encode", "fps")
LOCKS = frozenset(("name", "year", "type", "season", "episode", "identity"))
SEASON = re.compile(r"(?<![A-Za-z0-9])S(\d{1,3})(?:\s*-\s*S?(\d{1,3}))?(?!\d)", re.I)
EPISODE = re.compile(r"(?<![A-Za-z0-9])(?:S\d{1,3})?E(?:P)?(\d{1,4})(?:\s*-\s*(?:E(?:P)?)?(\d{1,4}))?(?![A-Za-z0-9])", re.I)
CN_SEASON = re.compile(r"第\s*(\d{1,3})(?:\s*-\s*(\d{1,3}))?\s*季")
CN_EPISODE = re.compile(r"第\s*(\d{1,4})(?:\s*-\s*(\d{1,4}))?\s*[集话話]")
MOVIE = re.compile(r"剧场版|劇場版|电影版|電影版|(?<![A-Za-z])(?:The Movie|Movie Version)(?![A-Za-z])", re.I)
BRACKET = re.compile(r"\[([^\[\]]{1,160})\]|【([^【】]{1,160})】")
ROLE = re.compile(r"字幕|简体|繁体|音轨|中字|国粤|国英|特效|内封|外挂|制作组|发布组|字幕组|汉化|1080|2160|720|[xh][. ]?26[45]|HEVC|AVC|HDR|DV|DTS|AAC|WEB|Blu.?Ray|REMUX", re.I)
RELEASE_LABEL = re.compile(r"(?:国语|粤语|台语|国粤|国英|双语|多语|配音)+|(?:已)?完结|全\d{1,4}集|更新至\d{1,4}集|[无未]删减|导演剪辑版")
NUMERIC_WORD = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,}E\d{2,4})(?![A-Za-z0-9])", re.I)
NAME_TAIL = re.compile(r"^(?:$|[. _-]+(?:(?:19|20)\d{2}|\d{3,4}[pi]|S\d{1,3}(?:E\d{1,4})?|WEB(?:-DL)?|BluRay|REMUX|mkv|mp4|flac|mka|aac|dts)(?=$|[. _-]))", re.I)
NAMED_BRACKET = re.compile(r"(?:片名|中文名|译名|又名|别名)\s*[:：]\s*(.{1,140})")
AUXILIARY_STEM = re.compile(r"(?:双语|字幕|特效|内封|外挂|官译|简体|繁体|繁中|简中|中英|简英|多语|国英|台粤|音轨|评论|国配|台配|粤语|韩语|日语|杜比|全景声|无损|中字|国语|原声)+")


def _tag_locks(text):
    return {group for tag in re.findall(r"\{\[([^\]\n]{1,512})\]\}", text)
            for key, group in (("type", "type"), ("s", "season"), ("e", "episode"))
            if re.search(r"(?:^|;)\s*" + key + r"\s*=", tag, re.I)}


def snapshot(meta):
    def plain(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (tuple, list)):
            return [plain(v) for v in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("unsupported Meta field")
    return {key: plain(getattr(meta, key, None)) for key in FIELDS}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class Correction:
    meta: object
    status: str
    reasons: tuple[str, ...]
    native: dict
    diff: dict
    revision: str

    def record(self):
        return {"status": self.status, "reasons": list(self.reasons), "native": self.native,
                "diff": self.diff, "revision": self.revision,
                "corrected": self.native if self.status == "ERROR" else snapshot(self.meta)}


class MetaCorrector:
    def __init__(self, protected_names=()):
        if (not isinstance(protected_names, (list, tuple)) or len(protected_names) > 100
                or any(not isinstance(n, str) or not 1 <= len(n.strip()) <= 160 for n in protected_names)):
            raise ValueError("invalid protected names")
        self.rules = {"core": 2, "protected_names": sorted(set(n.strip() for n in protected_names))}
        self.revision = _digest(self.rules)

    def correct(self, native, title, subtitle=None, custom_words=None, locks=(), *, context_known=True):
        before = {}
        try:
            if (not isinstance(title, str) or len(title) > 8192 or
                    (subtitle is not None and (not isinstance(subtitle, str) or len(subtitle) > 8192)) or
                    not isinstance(locks, (list, tuple)) or not set(locks) <= LOCKS or
                    (custom_words is not None and (not isinstance(custom_words, (list, tuple)) or
                     len(custom_words) > 100 or any(not isinstance(w, str) or len(w) > 2048 for w in custom_words)))):
                raise ValueError("invalid parse input")
            before = snapshot(native)
            if before["type"] == "音乐":
                return Correction(native, "SKIP", ("MUSIC_UNCHANGED",), before, {}, self.revision)
            if not hasattr(native, "cn_name") or not hasattr(native, "begin_episode"):
                raise ValueError("incompatible Meta result")
            signature = _digest([self.revision, title, subtitle, custom_words, locks, context_known])
            previous = getattr(native, "_subscribetter_parse", None)
            if previous and previous[0] == signature and previous[1] == before:
                return Correction(native, previous[2], previous[3], previous[4], previous[5], self.revision)
            result = deepcopy(native)
            locked = set(locks)
            reasons = []
            # Native has already applied user replacements/offsets. Never run them twice.
            if before["apply_words"]:
                locked.update(LOCKS)
                reasons.append("USER_WORDS_PRESERVED")
            locked.update(_tag_locks(title))
            text = re.sub(r"\{\[[^\]\n]{1,512}\]\}", "", title)
            if re.search(r"(?<![A-Za-z0-9])(?:S\d{1,3})?EP?\d{1,4}(?:EP?\d{1,4})+", text, re.I) and "episode" not in locked:
                return Correction(native, "DEFER", ("NON_RANGE_MULTI_EPISODE",), before, {}, self.revision)
            def set_type(value):
                current = getattr(result, "type", None)
                result.type = type(current)(value) if isinstance(current, Enum) else value
            def clear_scope():
                for group in ("season", "episode"):
                    if group not in locked:
                        setattr(result, "begin_" + group, None)
                        setattr(result, "end_" + group, None)
                        setattr(result, "total_" + group, 0)
            def set_name(value):
                if "name" not in locked:
                    result.cn_name = value if re.search(r"[\u3400-\u9fff]", value) else None
                    result.en_name = None if result.cn_name else value
            def ranges(patterns, value):
                values = set()
                for pattern in patterns:
                    for match in pattern.finditer(value or ""):
                        start, end = int(match[1]), int(match[2]) if match[2] else None
                        if end is not None and end < start:
                            raise ValueError("reversed explicit range")
                        values.add((start, end))
                return values
            season = ranges((SEASON, CN_SEASON), text) | ranges((SEASON, CN_SEASON), subtitle)
            episode = ranges((EPISODE, CN_EPISODE), text) | ranges((EPISODE, CN_EPISODE), subtitle)
            conflict = ((len(season) > 1 and "season" not in locked) or
                        (len(episode) > 1 and "episode" not in locked))
            movie = bool(MOVIE.search(text) or MOVIE.search(subtitle or ""))
            if movie and (season or episode) and "type" not in locked:
                conflict = True
            if conflict:
                return Correction(native, "DEFER", ("EXPLICIT_SCOPE_CONFLICT",), before, {}, self.revision)
            protected = None
            for name in self.rules["protected_names"]:
                if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
                    protected = name
                    break
            for match in NUMERIC_WORD.finditer(text):
                if protected is not None:
                    break
                word = match[1]
                truncated = re.sub(r"E\d+$", "", word, flags=re.I)
                current_name = before["en_name"] or before["cn_name"] or ""
                complete_head = not text[:match.start()].strip(" ._") and NAME_TAIL.match(text[match.end():])
                if current_name.casefold() in (truncated.casefold(), word.casefold()) or complete_head:
                    protected = word
                    year = re.match(r"^[. _-]+((?:19|20)\d{2})(?=$|[. _-])", text[match.end():])
                    if year and "year" not in locked:
                        result.year = year[1]
                    break
            numeric = re.match(r"^\s*(\d{4})(?=$|[. _])(?:[. _]+((?:19|20)\d{2})(?=$|[. _]))?", text)
            if numeric and text[numeric.end():].strip(" ._") and not re.match(
                    r"^(?:\d{3,4}[pi]\b|S\d{1,3}E\d|WEB\b|BluRay\b|REMUX\b|[xh]26[45]\b)",
                    text[numeric.end():].strip(" ._"), re.I):
                numeric = None
            if numeric:
                protected = numeric[1]
                if "year" not in locked:
                    result.year = numeric[2]
            if protected:
                set_name(protected)
                reasons.append("NUMERIC_NAME_PROTECTED")
                trailing = re.search(r"(\d+)$", protected)
                false_episode = trailing and before["begin_episode"] == int(trailing[1])
                if not season and not episode and false_episode:
                    clear_scope()
                    if "type" not in locked and not ({"season", "episode"} & locked):
                        set_type("电影" if movie else "未知")
            titles, ambiguous_blocks = [], []
            for match in BRACKET.finditer(text):
                block = (match[1] or match[2]).strip()
                named = NAMED_BRACKET.fullmatch(block)
                if named:
                    titles.append(named[1].strip())
                elif (re.search(r"[\u3400-\u9fff]{2}", block) and not ROLE.search(block)
                        and not RELEASE_LABEL.fullmatch(block)
                        and not CN_SEASON.search(block) and not CN_EPISODE.search(block)
                        and block != before["resource_team"]):
                    if block in (before["cn_name"], before["en_name"], *self.rules["protected_names"]):
                        titles.append(block)
                    else:
                        ambiguous_blocks.append(block)
            if len(set(titles)) == 1:
                set_name(titles[0])
                reasons.append("BRACKET_TITLE")
            elif (len(set(titles)) > 1 or ambiguous_blocks) and "name" not in locked:
                return Correction(native, "DEFER", ("BRACKET_NAME_AMBIGUOUS",), before, {}, self.revision)
            for group, values in (("season", season), ("episode", episode)):
                if values and group not in locked:
                    start, end = next(iter(values))
                    setattr(result, "begin_" + group, start)
                    setattr(result, "end_" + group, end)
                    setattr(result, "total_" + group, 1 if end is None else end - start + 1)
            if season or episode:
                if "type" not in locked:
                    set_type("电视剧")
                elif before["type"] == "电影" and ("season" not in locked or "episode" not in locked):
                    clear_scope()
            elif movie and "type" not in locked and not ({"season", "episode"} & locked):
                clear_scope()
                set_type("电影")
                reasons.append("EXPLICIT_MOVIE")
            status = "OK"
            if before["apply_words"]:
                for match in NUMERIC_WORD.finditer(before["org_string"] or ""):
                    short = re.sub(r"E\d+$", "", match[1], flags=re.I)
                    if (before["en_name"] or "").casefold() == short.casefold():
                        status = "DEFER"
                        reasons.append("USER_WORDS_CORRECTION_CONFLICT")
            if (not protected and not season and not episode and not movie and "episode" not in locked
                    and before["begin_episode"] is not None):
                dash = re.search(r"\s-\s(\d{1,4})(?:v\d)?(?:\s|$)", text, re.I)
                if not dash or int(dash[1]) != before["begin_episode"]:
                    status = "DEFER"
                    reasons.append("BARE_EPISODE_AMBIGUOUS")
            after = snapshot(result)
            diff = {k: {"before": before[k], "after": v} for k, v in after.items() if before[k] != v}
            if diff and not context_known:
                return Correction(native, "DEFER", ("RUST_LOCK_CONTEXT_UNKNOWN",), before, {}, self.revision)
            if not (after["cn_name"] or after["en_name"]):
                status = "DEFER"
                reasons.append("NAME_UNKNOWN")
            result._subscribetter_parse = signature, after, status, tuple(reasons), before, diff
            return Correction(result, status, tuple(reasons), before, diff, self.revision)
        except Exception:
            return Correction(native, "ERROR", ("META_CORRECTION_UNAVAILABLE",), before, {}, self.revision)

    def correct_path(self, native, path, custom_words=None, locks=()):
        """Managed final-path correction; caller supplies the actual path and words.

        No parent parser calls and no third hook. Filename evidence is primary;
        parent explicit tags remain constraints, while an auxiliary-only filename
        takes its title evidence from the immediate parent, like the host merge.
        """
        if not isinstance(path, (str, Path)) or not 1 <= len(str(path)) <= 8192:
            return Correction(native, "ERROR", ("INVALID_PARSE_PATH",), {}, {}, self.revision)
        item = PurePosixPath(str(path).replace("\\", "/"))
        locked = set(locks)
        for part in (item.name, item.parent.name, item.parent.parent.name):
            locked.update(_tag_locks(part))
        title = item.parent.name if len(item.stem) <= 16 and AUXILIARY_STEM.fullmatch(item.stem) else item.name
        return self.correct(native, title, custom_words=custom_words, locks=sorted(locked))


def _stored(value):
    """Keep source text except credentials/URLs, which are not replayable evidence."""
    if isinstance(value, str):
        value = re.sub(r"https?://[^\s<>]+", "[REDACTED_URL]", value, flags=re.I)
        value = re.sub(r"(?im)\b(cookie|authorization)\s*[:=][^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*", r"\1=[REDACTED]", value)
        return re.sub(r"(?i)\b(passkey|token|password|UID|CID|SEID)\s*[:=]\s*[^\s;]+", r"\1=[REDACTED]", value)
    if isinstance(value, (list, tuple)):
        return [_stored(v) for v in value]
    if isinstance(value, dict):
        return {k: _stored(v) for k, v in value.items()}
    return value


class MetaService:
    """Explicit managed entry, separate from the synchronous host wrappers."""
    def __init__(self, repository, corrector, parser=None):
        self.repository, self.corrector, self.parser = repository, corrector, parser

    def parse(self, key, title, subtitle=None, custom_words=None, locks=(), *, native=None, task_id=None,
              is_path=False, force_video=False):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,255}", key):
            raise ValueError("invalid sample key")
        if type(is_path) is not bool or type(force_video) is not bool or (is_path and subtitle is not None):
            raise ValueError("invalid parse path options")
        if native is None:
            parser = self.parser
            if parser is None:
                from app.sdk.media import MetaInfo, MetaInfoPath
                parser = MetaInfoPath if is_path else MetaInfo
            try:
                native = (parser(Path(title), custom_words=custom_words, force_video=force_video) if is_path else
                          parser(title, subtitle=subtitle, custom_words=custom_words, force_video=force_video))
            except Exception:
                native = None
        if native is None:
            correction = Correction(None, "ERROR", ("NATIVE_PARSE_FAILED",), {}, {}, self.corrector.revision)
        elif is_path:
            correction = self.corrector.correct_path(native, title, custom_words, locks)
        else:
            correction = self.corrector.correct(native, title, subtitle, custom_words, locks)
        inputs = dict(title=title, subtitle=subtitle, custom_words=custom_words, locks=list(locks),
                      is_path=is_path, force_video=force_video)
        record = correction.record()
        sanitized = _stored(dict(inputs=inputs, native=correction.native, result=record))
        self.repository.save_parse_sample(key, sanitized, self.corrector.rules, task_id,
                                          replay_allowed=sanitized["inputs"] == inputs)
        return correction

    def parse_path(self, key, path, custom_words=None, locks=(), *, native=None, task_id=None, force_video=False):
        return self.parse(key, str(path), custom_words=custom_words, locks=locks, native=native,
                          task_id=task_id, is_path=True, force_video=force_video)

    def replay(self, keys):
        if not isinstance(keys, (list, tuple)) or not 1 <= len(keys) <= 100:
            raise ValueError("replay requires 1..100 selected sample keys")
        results = []
        for row in self.repository.parse_samples(keys):
            if not row["replay_allowed"] or row["task_state"] in ("STOPPED", "RELEASED_NATIVE"):
                continue
            results.append(self.parse(row["sample_key"], **row["inputs"], task_id=row["task_id"]).record())
        return results
