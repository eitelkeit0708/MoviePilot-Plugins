"""Independent quality policy. No host rule engine, ordinal scores or IO decisions.

Callers supply verified identity/scope gates and the current public classification
snapshot. Archive facts and policy inputs remain the source of truth; rank tuples
are disposable. Evidence receipts must be persisted atomically with the final
version association by the repository integration, never when a plan is created.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import operator
from pathlib import Path
import time
from types import MappingProxyType
from typing import Mapping

import regex


MAX_TEXT = 16384
MAX_RULES = 64
MAX_NODES = 512
MAX_DEPTH = 16
REGEX_TIMEOUT = 0.025
EVALUATION_TIMEOUT = 0.25
FIELDS = frozenset({"text", "title", "description", "labels", "original_language", "production_countries",
                    "origin_country", "genre_ids", "media_type", "size", "seeders", "downloadvolumefactor",
                    "publish_minutes", "subtitle_description"})
COMPARISONS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt,
               "ge": operator.ge, "lt": operator.lt, "le": operator.le}

# These names are migration bindings, never a runtime country classifier.
# Resolution is retained as the leading dimension even in a single-resolution policy
# so a legacy current version can still be compared without candidate admission.
CATEGORIES = {
    "华语电影": ((2160,), "any", "movie", ("resolution", "picture", "special", "source", "hq", "audio")),
    "外语电影": ((2160,), "any", "movie", ("resolution", "picture", "special", "source", "hq", "audio")),
    "动画电影": ((2160, 1080), "any", "movie", ("resolution", "picture", "special", "source", "hq", "audio")),
    "国产剧": ((2160,), "official", "web", ("resolution", "picture", "hq", "audio")),
    "欧美剧": ((2160, 1080), "official", "movie", ("resolution", "picture", "special", "source", "hq", "audio")),
    "日韩剧": ((2160, 1080), "official", "movie", ("resolution", "picture", "source", "hq", "audio")),
    "港台剧": ((2160, 1080), "official", "movie", ("resolution", "picture", "source", "hq", "audio")),
    "纪录片": ((2160, 1080), "any", "movie", ("resolution", "picture", "source", "hq", "audio")),
    "国漫": ((2160,), "official", "any", ("resolution", "picture", "audio")),
    "日番": ((2160, 1080), "anime", "any", ("anime", "audio")),
    "欧美漫": ((1080,), "official", "any", ("resolution", "audio")),
    "综艺": ((2160, 1080), "hhweb", "any", ("resolution",)),
    "现场": ((2160, 1080), "any", "any", ("resolution", "picture", "audio", "hq")),
}


def category_templates(overrides=None):
    """Freeze the editable defaults; existing comparison semantics stay intact."""
    values=_bounded_copy(overrides or {})
    if not isinstance(values,dict) or set(values)-set(CATEGORIES):raise ValueError('UNKNOWN_POLICY_TEMPLATE')
    result=dict(CATEGORIES)
    for name,value in values.items():
        if not isinstance(value,dict) or set(value)!={'resolutions','group','source','dimensions'}:raise ValueError('INVALID_POLICY_TEMPLATE')
        resolutions=value['resolutions'];dimensions=value['dimensions']
        if (not isinstance(resolutions,list) or not resolutions or len(resolutions)>2
                or any(type(v)is not int or v not in (1080,2160) for v in resolutions)
                or len(set(resolutions))!=len(resolutions)):raise ValueError('INVALID_POLICY_RESOLUTIONS')
        if (not isinstance(dimensions,list) or not dimensions or len(dimensions)>7
                or any(v not in ('resolution','picture','special','source','hq','audio','anime') for v in dimensions)
                or len(set(dimensions))!=len(dimensions)):raise ValueError('INVALID_POLICY_DIMENSIONS')
        if value['group'] not in ('any','official','anime','hhweb') or value['source'] not in ('any','movie','web'):raise ValueError('INVALID_POLICY_ADMISSION')
        result[name]=(tuple(resolutions),value['group'],value['source'],tuple(dimensions))
    return result


class MissingEvidence(ValueError):
    pass


def _bounded_copy(value):
    """Only JSON data crosses the predicate boundary, with bounded size/depth."""
    count = 0

    def check(item, depth=0):
        nonlocal count
        count += 1
        if count > 4096 or depth > MAX_DEPTH:
            raise ValueError("INPUT_LIMIT")
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            if not float("-inf") < item < float("inf"):
                raise ValueError("NONFINITE_NUMBER")
        elif isinstance(item, str):
            if len(item) > MAX_TEXT:
                raise ValueError("TEXT_LIMIT")
        elif isinstance(item, dict):
            for key, val in item.items():
                if not isinstance(key, str):
                    raise ValueError("INVALID_KEY")
                check(key, depth + 1)
                check(val, depth + 1)
        elif isinstance(item, (list, tuple)):
            for val in item:
                check(val, depth + 1)
        else:
            raise ValueError("NON_JSON_INPUT")

    check(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 262144:
        raise ValueError("INPUT_LIMIT")
    return json.loads(encoded)


def _canonical(value):
    if isinstance(value, dict):
        result = {key: _canonical(val) for key, val in value.items()}
        for key in ("all", "any"):
            if key in result:
                result[key] = sorted(result[key], key=lambda x: json.dumps(x, sort_keys=True))
        for key in ("in", "intersects"):
            if key in result:
                result[key][1] = sorted(result[key][1], key=lambda x: json.dumps(x, sort_keys=True))
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _join(expressions, op="all"):
    return {op: expressions} if expressions else {"literal": True}


def _migrate_rule(rule):
    allowed = {"id", "name", "include", "exclude", "tmdb", "match"}
    if set(rule) - allowed:
        raise ValueError("UNSUPPORTED_LEGACY_FIELDS: use explicit structured predicates")
    matches = rule.get("match") or ["text"]
    if matches != ["text"]:
        # Native match combines fields with fallback. Reject rather than silently
        # weakening that meaning; migration UI can submit an explicit equivalent AST.
        raise ValueError("LEGACY_MATCH_REQUIRES_EXPLICIT_PREDICATE")
    clauses = []
    for key in ("include", "exclude"):
        items = rule.get(key) or []
        items = [items] if isinstance(items, str) else items
        if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
            raise ValueError("INVALID_LEGACY_PATTERN")
        if items:
            expr = {"any": [{"regex": ["text", pattern]} for pattern in items]}
            clauses.append({"not": expr} if key == "exclude" else expr)
    text = _join(clauses)
    metadata = rule.get("tmdb") or {}
    if not isinstance(metadata, dict):
        raise ValueError("INVALID_LEGACY_METADATA")
    conditions = []
    for key, values in metadata.items():
        if not isinstance(values, str):
            raise ValueError("INVALID_LEGACY_METADATA")
        if values:
            # Host metadata is case-insensitive, same-field OR, cross-field AND.
            conditions.append({"intersects": [key, [v.upper() for v in values.split(",") if v]]})
    return {"any": [text, _join(conditions)]} if conditions else text


_SNAPSHOT = json.loads(Path(__file__).with_name("policy-data.json").read_text(encoding="utf-8"))
_DEFAULT_RULES = {name: _migrate_rule(rule) for name, rule in _SNAPSHOT.items()}


def import_predicates(overrides: Mapping) -> dict:
    """Validate explicit per-instance AST overrides without touching host rules.

    This returns a detached JSON document for storage/preview. Unknown references,
    recursion, malformed regex, excessive rule/input size and operators are errors.
    `registered` only references validated data predicates, never Python callbacks.
    """
    overrides = _bounded_copy(dict(overrides))
    rules = {**_DEFAULT_RULES, **overrides}
    if len(rules) > MAX_RULES:
        raise ValueError("RULE_LIMIT")
    count = 0

    def validate(node, stack=(), depth=0):
        nonlocal count
        count += 1
        if count > MAX_NODES * MAX_RULES or depth > MAX_DEPTH:
            raise ValueError("RULE_COMPLEXITY")
        if not isinstance(node, dict) or len(node) != 1:
            raise ValueError("INVALID_PREDICATE")
        op, args = next(iter(node.items()))
        if op == "registered":
            if not isinstance(args, str) or args not in rules or args in stack:
                raise ValueError("INVALID_PREDICATE_REFERENCE")
            validate(rules[args], (*stack, args), depth + 1)
        elif op in {"all", "any"}:
            if not isinstance(args, list) or not args or len(args) > MAX_NODES:
                raise ValueError("INVALID_BOOLEAN_PREDICATE")
            for child in args:
                validate(child, stack, depth + 1)
        elif op == "not":
            validate(args, stack, depth + 1)
        elif op == "literal":
            if type(args) is not bool:
                raise ValueError("INVALID_LITERAL")
        elif op in {*COMPARISONS, "in", "intersects", "regex"}:
            if (not isinstance(args, list) or len(args) != 2
                    or not isinstance(args[0], str) or args[0] not in FIELDS):
                raise ValueError("INVALID_FIELD_PREDICATE")
            if op in {"in", "intersects"} and (not isinstance(args[1], list) or len(args[1]) > 256):
                raise ValueError("INVALID_SET")
            if op == "regex":
                if not isinstance(args[1], str) or len(args[1]) > MAX_TEXT:
                    raise ValueError("INVALID_REGEX")
                try:
                    regex.compile(args[1], regex.I)
                except regex.error as exc:
                    raise ValueError("INVALID_REGEX") from exc
        else:
            raise ValueError("INVALID_OPERATOR")

    for name, expr in rules.items():
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("INVALID_PREDICATE_NAME")
        validate(expr, (name,))
    return overrides


def import_legacy_overrides(records: list[dict]) -> dict:
    """Explicit migration only. Unsupported fields error; never silently discard."""
    records = _bounded_copy(records)
    if not isinstance(records, list) or len(records) > MAX_RULES:
        raise ValueError("RULE_LIMIT")
    result = {}
    for record in records:
        if not isinstance(record, dict) or not record.get("id") or record["id"] in result:
            raise ValueError("INVALID_LEGACY_ID")
        result[record["id"]] = _migrate_rule(record)
    return import_predicates(result)


class _Evaluator:
    def __init__(self, rules, data):
        self.rules, self.data = rules, data
        self.missing = set(data.get("missing_fields", ()))
        if self.missing & {"title", "description", "labels", "subtitle_description"}:
            self.missing.add("text")
        self.deadline = time.monotonic() + EVALUATION_TIMEOUT
        self.cache = {}
        self.nodes = 0

    def evaluate(self, node):
        self.nodes += 1
        if self.nodes > MAX_NODES or time.monotonic() >= self.deadline:
            raise TimeoutError("PREDICATE_BUDGET")
        op, args = next(iter(node.items()))
        if op == "registered":
            if args not in self.cache:
                self.cache[args] = self.evaluate(self.rules[args])
            return self.cache[args]
        if op in {"all", "any"}:
            unknown = False
            for child in args:
                try:
                    value = self.evaluate(child)
                except MissingEvidence:
                    unknown = True
                    continue
                if value == (op == "any"):
                    return value
            if unknown:
                raise MissingEvidence("PREDICATE_EVIDENCE_MISSING")
            return op == "all"
        if op == "not":
            return not self.evaluate(args)
        if op == "literal":
            return args
        name, expected = args
        actual = self.data.get(name)
        if actual is None or name in self.missing:
            raise MissingEvidence("FIELD_MISSING:" + name)
        if op == "regex":
            if isinstance(actual, (list, tuple)):
                actual = " ".join(str(x) for x in actual)
            if not isinstance(actual, str) or len(actual) > MAX_TEXT:
                raise ValueError("INVALID_REGEX_INPUT")
            return bool(regex.search(expected, actual, regex.I,
                                     timeout=min(REGEX_TIMEOUT, max(0.0001, self.deadline - time.monotonic()))))
        if op == "in":
            return actual in expected
        if op == "intersects":
            values = actual if isinstance(actual, list) else [actual]
            values = [str(v.get("iso_3166_1", "") if isinstance(v, dict) else v).upper() for v in values]
            return bool(set(values) & {str(v).upper() for v in expected})
        return COMPARISONS[op](actual, expected)


@dataclass(frozen=True)
class Facts:
    resolution: int | None = None
    picture: int | None = None
    source: str | None = None
    hq: bool | None = None
    audio: int | None = None
    special_zh_subtitles: bool | None = None
    evidence: str = "unknown"
    official: bool | None = None
    hhweb: bool | None = None
    vcb: bool | None = None
    bglobal: bool | None = None
    anime_platform: bool | None = None
    group: str | None = None
    platform: str | None = None
    language: bool | None = None
    base: bool | None = None
    current: bool = False
    errors: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    raw: Mapping = field(default_factory=dict, repr=False, compare=False)
    predicate_hash: str = ""
    source_admission: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Version:
    version_id: str
    facts: Facts
    active: bool = True
    reliable: bool = True


def quality_facts(facts):
    """Display the facts used in comparison, without raw titles or a second score."""
    fields=('resolution','picture','source','hq','audio','special_zh_subtitles','evidence','group','platform')
    values={key:getattr(facts,key) for key in fields}
    technical=facts.raw.get('technical',{})
    # A comparison's fallback tier is not a measured SDR/basic-audio assertion.
    for key in ('picture','audio'):
        if values[key]==0 and technical.get(key) is None:values[key]=None
    values['basis']={key:'measured' if technical.get(key) is not None else 'release' for key in ('resolution','picture','audio')}
    return values


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    policy_hash: str
    category: Mapping = field(default_factory=dict)
    rank: tuple = ()
    action: str = "NONE"
    evidence_keys: tuple[str, ...] = ()
    comparisons: tuple[dict, ...] = ()


def confirm_evidence(decision: Decision, *, final_association_verified: bool) -> tuple[str, ...]:
    """Receipt projection only; caller must also enforce plan ownership/generation.

    Verification means actual final video AND every required asset is associated
    with the explicit release. Transfer completion alone is insufficient.
    """
    if final_association_verified is True and decision.status == "ALLOW" and decision.reason == "EVIDENCE_UPGRADE":
        return decision.evidence_keys
    return ()


def _lock_value(name, value):
    if name in {"group", "platform"}:
        if not isinstance(value, str) or not value or len(value) > 100:
            raise ValueError("INVALID_LOCK")
        value = value.casefold().replace("-", "").replace("_", "").replace(" ", "")
        if name == "platform":
            return {"amazon": "amzn", "netflix": "nf", "crunchyroll": "cr"}.get(value, value)
        return value
    allowed = {"resolution": {1080, 2160}, "picture": {0, 1, 2}, "audio": {0, 1, 2, 3},
               "source": {"remux", "web", "bluray"}, "hq": {False, True}}[name]
    expected_type = str if name == "source" else bool if name == "hq" else int
    if type(value) is not expected_type or value not in allowed:
        raise ValueError("INVALID_LOCK")
    return value


def _first_known(choices, default=None):
    """Unknown higher-precedence evidence cannot silently become a lower tier."""
    for matches, value in choices:
        if matches is None:
            return None
        if matches:
            return value
    return default


class Policy:
    def __init__(self, bindings: Mapping[str, str], classification_revision: int, *, overrides=None, admission=None,templates=None):
        self.bindings = _bounded_copy(dict(bindings))
        self.categories=category_templates(templates)
        if (type(classification_revision) is not int or classification_revision < 1 or not self.bindings
                or any(not key or val not in CATEGORIES for key, val in self.bindings.items())):
            raise ValueError("INVALID_POLICY_BINDINGS")
        self.classification_revision = classification_revision
        custom = import_predicates(overrides or {})
        if admission is not None:
            import_predicates({**custom, "__admission__": admission})
        self.rules = {**_DEFAULT_RULES, **custom}
        self.admission = _bounded_copy(admission) if admission is not None else {"literal": True}
        self.predicate_hash = _hash({"normalization": 2, "rules": self.rules})
        self.semantic_hash = _hash({"semantics": 1, "categories": self.categories, "bindings": self.bindings,
                                    "rules": self.rules, "admission": self.admission})

    def normalize(self, raw: Mapping, current=False) -> Facts:
        """Normalize release claims; optional `technical` fields come from archive probes.

        Technical resolution/picture/audio override release claims. HQ/source/group
        remain publication semantics. Missing description/labels are explicitly
        marked by callers (RSS), distinct from a complete description with no tag.
        """
        try:
            data = _bounded_copy(dict(raw))
            errors = data.get("provider_errors", [])
            missing = data.get("missing_fields", [])
            if (not isinstance(errors, list) or not isinstance(missing, list)
                    or any(not isinstance(x, str) for x in [*errors, *missing])):
                raise ValueError("INVALID_EVIDENCE_STATUS")
            for key in ("title", "description", "subtitle_description", "original_language"):
                data.setdefault(key, "")
                if not isinstance(data[key], str):
                    raise ValueError("INVALID_TEXT_FIELD")
            data.setdefault("labels", [])
            if not isinstance(data["labels"], list) or any(not isinstance(x, str) for x in data["labels"]):
                raise ValueError("INVALID_LABELS")
            data["text"] = " ".join([data["title"], data["description"], *data["labels"], data["subtitle_description"]])
            if len(data["text"]) > MAX_TEXT:
                raise ValueError("TEXT_LIMIT")
            evaluator = _Evaluator(self.rules, data)

            def evaluate(node):
                try:
                    return evaluator.evaluate(node)
                except MissingEvidence:
                    # Missing admission-only facts must not erase known quality.
                    name = node.get("registered", "composite")
                    marker = "predicate:" + name
                    if marker not in missing:
                        missing.append(marker)
                    return None

            p = lambda name: evaluate({"registered": name})
            resolution4k, resolution1080 = p("Resolution4K"), p("Resolution1080")
            resolution = _first_known(((resolution4k, 2160), (resolution1080, 1080)))
            if resolution4k is False and resolution1080 is False and regex.search(
                    r"(?<![A-Za-z0-9])(?:720[pi]|480[pi]|576[pi]|4320p|8k|1280[x×]720|x720)(?![A-Za-z0-9])",
                    data["text"], regex.I, timeout=REGEX_TIMEOUT):
                resolution = 0  # Explicit unsupported resolution, distinct from missing evidence.
            picture = _first_known(((p("DolbyVision"), 2), (p("HDRVideo"), 1)), 0)
            audio = _first_known(((p("LosslessAudio"), 3), (p("ImmersiveAudio"), 2), (p("DolbyPlus"), 1)), 0)
            remux, web, movie = p("RemuxSource"), p("WEBDL"), p("MovieSource")
            source = _first_known(((remux, "remux"), (web, "web"), (movie, "bluray")))
            source_admission = {"web": web, "movie": evaluate({"any": [
                {"registered": "RemuxSource"}, {"registered": "MovieSource"}]})}
            hq = p("HighBitrate")
            special = evaluate({"all": [{"registered": "ChineseSubtitles"}, {"registered": "SpecialSubtitles"}]})
            evidence = "explicit" if special else "unknown" if special is None else "none"
            if current and not special and data.get("chinese_pgs") is True:
                special, evidence = True, "inferred_pgs"
            language = evaluate({"any": [{"registered": "MandarinAudio"}, {"registered": "ChineseSubtitles"},
                                         {"all": [{"registered": "NativeLanguageGuard"}, {"registered": "CNSUB"}]}]})
            official, hhweb, vcb = p("OfficialGroup"), p("HHWEBGroup"), p("VCBGroup")
            bglobal, anime_platform = p("BGlobal"), p("AnimePlatform")
            # Do not turn an absent release group in an existing file into a low anime tier.
            if current and not (official or vcb) and not data.get("group_known", False):
                official = hhweb = vcb = None
            group = data.get("group")
            platform = data.get("platform")
            if group is None:
                match = regex.search(_SNAPSHOT["OfficialGroup"]["include"], data["text"], regex.I,
                                     timeout=REGEX_TIMEOUT)
                group = match.group().lstrip("-@") if match else "VCB-Studio" if vcb else None
            if platform is None:
                match = regex.search(_SNAPSHOT["AnimePlatform"]["include"], data["text"], regex.I,
                                     timeout=REGEX_TIMEOUT)
                platform = match.group() if match else "B-Global" if bglobal else None
            group = _lock_value("group", group) if group is not None else None
            platform = _lock_value("platform", platform) if platform is not None else None
            technical = data.get("technical", {})
            if not isinstance(technical, dict) or set(technical) - {"resolution", "picture", "audio"}:
                raise ValueError("INVALID_TECHNICAL_FIELDS")
            for key, val in technical.items():
                allowed = {"resolution": {480, 576, 720, 1080, 2160, 4320},
                           "picture": {0, 1, 2}, "audio": {0, 1, 2, 3}}[key]
                if val is not None and (type(val) is not int or val not in allowed):
                    raise ValueError("INVALID_TECHNICAL_VALUE")
            resolution = technical.get("resolution", resolution)
            picture = technical.get("picture", picture)
            audio = technical.get("audio", audio)
            result = Facts(resolution, picture, source, hq, audio, special, evidence, official, hhweb,
                           vcb, bglobal, anime_platform, group, platform, language, p("GeneralFilter"),
                           bool(current), tuple(errors), tuple(missing), MappingProxyType(data), self.predicate_hash,
                           MappingProxyType(source_admission))
            dimensions = {"resolution", "picture", "source", "hq", "audio", "special_zh_subtitles",
                          "official", "hhweb", "vcb", "bglobal", "anime_platform", "group", "platform", "language", "base"}
            changes = {key: None for key in set(missing) & dimensions}
            if "group" in missing:
                changes.update(official=None, hhweb=None, vcb=None)
            if "platform" in missing:
                changes.update(bglobal=None, anime_platform=None)
            if "special_zh_subtitles" in changes:
                changes["evidence"] = "unknown"
            result = replace(result, **changes)
            for key in set(missing) & FIELDS:
                data[key] = None
            return result
        except TimeoutError:
            return Facts(current=bool(current), errors=("PREDICATE_TIMEOUT",), predicate_hash=self.predicate_hash)
        except (ValueError, TypeError, KeyError, regex.error):
            return Facts(current=bool(current), errors=("INVALID_FACTS",), predicate_hash=self.predicate_hash)

    def _decision(self, status, reason, category=None, **kwargs):
        return Decision(status, reason, self.semantic_hash, category or {}, **kwargs)

    def _category(self, classification):
        try:
            snapshot = _bounded_copy(dict(classification))
            if snapshot.get("state") == "invalid_policy":
                return self._decision("ERROR", "CLASSIFICATION_INVALID")
            if snapshot.get("state") != "complete":
                return self._decision("DEFER", "CLASSIFICATION_INCOMPLETE")
            if snapshot.get("policy_revision") != self.classification_revision:
                return self._decision("DEFER", "CLASSIFICATION_STALE")
            effective = snapshot.get("effective") or {}
            cid = effective.get("category_id")
            if not isinstance(cid, str) or cid not in self.bindings:
                return self._decision("ERROR", "CATEGORY_UNBOUND")
            return {"category_id": cid, "policy_revision": snapshot["policy_revision"],
                    "category_path": effective.get("category_path", []), "rule_id": effective.get("rule_id"),
                    "source": effective.get("source"), "policy": self.bindings[cid]}
        except (ValueError, TypeError, AttributeError):
            return self._decision("ERROR", "CLASSIFICATION_INVALID")

    @staticmethod
    def _anime(facts):
        if facts.resolution is None:
            return None
        if facts.resolution == 1080:
            if facts.vcb is True:
                return 5
            if facts.vcb is None:
                return None
            if facts.official is True and facts.anime_platform is True:
                return 3
            if facts.official is None or facts.anime_platform is None:
                return None
            if facts.bglobal is True:
                return 2
            if facts.bglobal is None:
                return None
            return 1 if facts.official else 0
        if facts.resolution == 2160:
            return None if facts.bglobal is None else 4 if facts.bglobal else 0
        return 0

    def rank(self, facts, policy_name):
        if policy_name not in self.categories:
            raise ValueError("UNKNOWN_POLICY")
        is4k = facts.resolution == 2160
        values = {"resolution": facts.resolution, "picture": facts.picture if is4k else 0,
                  "special": facts.special_zh_subtitles,
                  "source": None if facts.source is None else int(facts.source == "remux"),
                  "hq": facts.hq if is4k and (policy_name == "现场" or facts.source != "remux") else False,
                  "audio": facts.audio, "anime": self._anime(facts)}
        return tuple(values[key] for key in self.categories[policy_name][3])

    def describe(self, name):
        """Read-only display of the saved template and the same rank used by comparison."""
        resolutions, group, source, dimensions = self.categories[name]
        options = {
            'resolution': [('2160p', dict(resolution=2160)), ('1080p', dict(resolution=1080))],
            'picture': [('Dolby Vision', dict(picture=2)), ('HDR', dict(picture=1)), ('普通画面', dict(picture=0))],
            'special': [('有中文特效字幕', dict(special_zh_subtitles=True)), ('无中文特效字幕', dict(special_zh_subtitles=False))],
            'source': [('REMUX', dict(source='remux')), ('其他已准入片源', dict(source='web'))],
            'hq': [('高码率', dict(hq=True)), ('普通码率', dict(hq=False))],
            'audio': [('无损音轨', dict(audio=3)), ('沉浸音轨', dict(audio=2)), ('Dolby Digital Plus', dict(audio=1)), ('其他音轨', dict(audio=0))],
            'anime': [('1080p VCB', dict(resolution=1080,vcb=True)), ('2160p B-Global', dict(bglobal=True)),
                      ('1080p 官方动画平台', dict(resolution=1080,official=True,anime_platform=True)),
                      ('1080p B-Global', dict(resolution=1080,bglobal=True)), ('1080p 其他官方组', dict(resolution=1080,official=True)), ('其他已准入资源', {})],
        }
        notes = {'picture':'仅 2160p 比较画面类型', 'hq':'仅 2160p 比较；REMUX 不比较此项' if name!='现场' else '仅 2160p 比较'}
        base = Facts(resolution=2160,source='web',vcb=False,bglobal=False,official=False,anime_platform=False)
        rows=[]
        for index,dimension in enumerate(dimensions):
            ordered=sorted(options[dimension],key=lambda item:self.rank(replace(base,**item[1]),name)[index],reverse=True)
            rows.append(dict(dimension=dimension,order=[item[0] for item in ordered],note=notes.get(dimension,'')))
        return dict(resolutions=sorted(resolutions,reverse=True),
                    group={'any':'不限制发布组','official':'需符合官方发布组规则','anime':'需符合动画发布组规则','hhweb':'需符合 HHWEB 规则'}[group],
                    source={'any':'不额外限制片源','movie':'需符合影视片源规则','web':'仅 WEB 片源'}[source],
                    comparison=rows)

    def describe_change(self, candidate, current_versions, decision):
        """Display every policy dimension; never replace the lexicographic decision."""
        name = decision.category.get('policy')
        if name not in self.categories:
            return None
        versions = sorted((v for v in current_versions if v.active), key=lambda v: v.version_id)
        target = quality_facts(candidate)
        new_rank = self.rank(candidate, name)
        fields = {'special': 'special_zh_subtitles'}
        comparisons = []
        for version in versions[:20]:
            current = quality_facts(version.facts)
            decisive = next((v.get('dimension') for v in decision.comparisons if v.get('version_id') == version.version_id), None)
            changes = []
            for dimension, new, old in zip(self.categories[name][3], new_rank, self.rank(version.facts, name)):
                field = fields.get(dimension, dimension)
                # Rank uses zero for inapplicable dimensions (e.g. HDR at 1080p).
                # That sentinel is not a measured SDR/non-HQ value for display.
                if dimension in ('picture', 'hq'):
                    new, old = target.get(field), current.get(field)
                unknown = (not version.reliable or bool(version.facts.errors) or
                           version.facts.predicate_hash != self.predicate_hash or new is None or old is None or
                           (dimension != 'anime' and (target.get(field) is None or current.get(field) is None)))
                order = None if unknown else (new > old) - (new < old)
                evidence = (dimension == 'special' and order == 0 and target.get(field) is True and current.get(field) is True
                            and version.facts.evidence == 'inferred_pgs' and candidate.evidence == 'explicit')
                changes.append(dict(dimension=dimension, order=order, evidence=evidence, decisive=dimension == decisive))
            comparisons.append(dict(version_id=version.version_id, current=current, changes=changes))
        return dict(kind={'MISSING':'acquire','QUALITY_UPGRADE':'quality','EVIDENCE_UPGRADE':'evidence',
                          'EQUIVALENT':'none'}.get(decision.reason,'unknown'), reason=decision.reason,
                    policy_revision=self.semantic_hash, versions=comparisons, version_count=len(versions),
                    truncated=len(versions)>len(comparisons))

    def admit(self, facts, classification, *, locked=None, excluded=False, identity_ok=None, scope_ok=None):
        # These explicit constraints always precede any quality/evidence exception.
        if type(excluded) is not bool:
            return self._decision("ERROR", "INVALID_EXCLUSION")
        if excluded is True:
            return self._decision("REJECT", "EXCLUDED")
        for value, name in ((identity_ok, "IDENTITY"), (scope_ok, "SCOPE")):
            if value is not True:
                return self._decision("REJECT" if value is False else "DEFER", name + "_UNCONFIRMED")
        category = self._category(classification)
        if isinstance(category, Decision):
            return category
        if facts.errors:
            return self._decision("ERROR", facts.errors[0], category)
        if facts.predicate_hash != self.predicate_hash:
            return self._decision("DEFER", "FACTS_REQUIRE_RENORMALIZATION", category)
        if facts.current:
            return self._decision("ERROR", "CURRENT_PROFILE_NOT_CANDIDATE", category)
        locked = locked or {}
        if not isinstance(locked, Mapping) or set(locked) - {"resolution", "picture", "source", "platform", "group", "audio", "hq"}:
            return self._decision("ERROR", "INVALID_LOCK", category)
        for name, expected in locked.items():
            try:
                expected = _lock_value(name, expected)
            except ValueError:
                return self._decision("ERROR", "INVALID_LOCK", category)
            actual = getattr(facts, name)
            if actual is None:
                return self._decision("DEFER", "LOCK_EVIDENCE_MISSING:" + name, category)
            if actual != expected:
                return self._decision("REJECT", "LOCK_MISMATCH:" + name, category)
        for value, name in ((facts.base, "BASE_FILTER"), (facts.language, "CHINESE_LANGUAGE")):
            if value is not True:
                return self._decision("DEFER" if value is None else "REJECT", name, category)
        policy_name = category["policy"]
        resolutions, group, source, dimensions = self.categories[policy_name]
        if facts.resolution is None:
            return self._decision("DEFER", "RESOLUTION_EVIDENCE_MISSING", category)
        if facts.resolution not in resolutions:
            return self._decision("REJECT", "RESOLUTION_NOT_ALLOWED", category)
        if source != "any" and facts.source_admission.get(source) is not True:
            allowed = facts.source_admission.get(source)
            return self._decision("DEFER" if allowed is None else "REJECT", "SOURCE_PREDICATE", category)
        if source != "any" and facts.source is None:
            return self._decision("DEFER", "SOURCE_EVIDENCE_MISSING", category)
        if source == "web" and facts.source != "web":
            return self._decision("REJECT", "SOURCE_NOT_ALLOWED", category)
        if group in {"official", "hhweb"} and getattr(facts, group) is not True:
            return self._decision("DEFER" if getattr(facts, group) is None else "REJECT", "GROUP_NOT_ALLOWED", category)
        rank = self.rank(facts, policy_name)
        if group == "anime" and rank[0] == 0:
            return self._decision("REJECT", "ANIME_TIER_NOT_ALLOWED", category)
        if any(val is None for val in rank):
            return self._decision("DEFER", "QUALITY_EVIDENCE_MISSING", category, rank=rank)
        try:
            if not _Evaluator(self.rules, facts.raw).evaluate(self.admission):
                return self._decision("REJECT", "CUSTOM_ADMISSION", category, rank=rank)
        except MissingEvidence:
            return self._decision("DEFER", "CUSTOM_EVIDENCE_MISSING", category)
        except (ValueError, TypeError, TimeoutError, regex.error):
            return self._decision("ERROR", "CUSTOM_PREDICATE_ERROR", category)
        return self._decision("ALLOW", "ADMITTED", category, rank=rank)

    def compare(self, candidate, current_versions, classification, *, consumed=frozenset(),
                same_assets_verified=frozenset(), **gates):
        decision = self.admit(candidate, classification, **gates)
        if decision.status != "ALLOW":
            return decision
        versions = [v for v in current_versions if v.active]
        if not versions:
            return replace(decision, reason="MISSING", action="TRANSFER")
        if any(not v.reliable for v in versions):
            return replace(decision, status="DEFER", reason="CURRENT_ASSOCIATION_UNRELIABLE")
        if any(not isinstance(v.version_id, str) or not v.version_id for v in versions):
            return replace(decision, status="ERROR", reason="CURRENT_VERSION_ID_REQUIRED")
        policy_name = decision.category["policy"]
        comparisons, equal, improved = [], [], False
        for version in sorted(versions, key=lambda v: v.version_id):
            current = version.facts
            if current.errors:
                return replace(decision, status="ERROR", reason="CURRENT_PROVIDER_ERROR")
            if current.predicate_hash != self.predicate_hash:
                return replace(decision, status="DEFER", reason="CURRENT_REQUIRES_RENORMALIZATION")
            old_rank = self.rank(current, policy_name)
            difference = 0
            for name, new, old in zip(self.categories[policy_name][3], decision.rank, old_rank):
                if new is None or old is None:
                    return replace(decision, status="DEFER", reason="CURRENT_EVIDENCE_MISSING:" + name,
                                   comparisons=tuple(comparisons))
                if new != old:
                    difference = 1 if new > old else -1
                    comparisons.append({"version_id": version.version_id, "dimension": name,
                                        "candidate": new, "current": old, "order": difference})
                    break
            if difference < 0:
                return replace(decision, status="REJECT", reason="CURRENT_BETTER", comparisons=tuple(comparisons))
            if difference == 0:
                equal.append(version)
                comparisons.append({"version_id": version.version_id, "dimension": "equal", "order": 0})
            else:
                improved = True
        # Compare against the highest current quality: improving a lower duplicate
        # does not justify another transfer while an equal current version exists.
        if not equal and improved:
            return replace(decision, reason="QUALITY_UPGRADE", action="TRANSFER", comparisons=tuple(comparisons))
        keys = []
        if "special" in self.categories[policy_name][3] and candidate.evidence == "explicit":
            for version in equal:
                if version.facts.evidence != "inferred_pgs":
                    return replace(decision, status="REJECT", reason="EQUIVALENT", comparisons=tuple(comparisons))
                key = _hash({"old_version": version.version_id, "policy": self.semantic_hash})
                if key in consumed:
                    return replace(decision, status="REJECT", reason="EQUIVALENT", comparisons=tuple(comparisons))
                keys.append(key)
        if keys:
            action = "ENRICH_EVIDENCE" if all(v.version_id in same_assets_verified for v in equal) else "TRANSFER"
            return replace(decision, reason="EVIDENCE_UPGRADE", action=action, evidence_keys=tuple(keys),
                           comparisons=tuple(comparisons))
        return replace(decision, status="REJECT", reason="EQUIVALENT", comparisons=tuple(comparisons))

    def compare_sidecar(self, candidate, current_versions, classification, *, consumed=frozenset(),
                        same_assets_verified=frozenset(), **gates):
        """Allow a proven same-video sidecar when every known current rank dimension is unchanged."""
        decision = self.compare(candidate, current_versions, classification, consumed=consumed,
                                same_assets_verified=same_assets_verified, **gates)
        if not decision.reason.startswith("CURRENT_EVIDENCE_MISSING:"):
            return decision
        admitted = self.admit(candidate, classification, **gates)
        if admitted.status != "ALLOW":
            return decision
        versions = [version for version in current_versions if version.active]
        for version in versions:
            if not version.reliable or version.facts.errors or version.facts.predicate_hash != self.predicate_hash:
                return decision
            old = self.rank(version.facts, admitted.category["policy"])
            if all(value is not None for value in old) or any(value is not None and value != admitted.rank[index]
                                                              for index, value in enumerate(old)):
                return decision
        return replace(admitted, status="REJECT", reason="EQUIVALENT", action="NONE")
