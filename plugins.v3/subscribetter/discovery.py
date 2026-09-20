"""Bounded Douban work discovery. RSS observations never bypass durable ownership."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import asyncio
import ipaddress
import json
import math
import random
import re
from typing import Annotated, Callable, Literal
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .repository import Target, utcnow


async def _drainable_to_thread(function, *args):
    """Do not leave provider or ownership work running after scheduler cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        await asyncio.wait({task})
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                continue
        try:
            task.result()
        except BaseException:
            pass
        raise
    return task.result()


ROUTES = {
    "movie_showing": ("影院热映", "电影", True),
    "movie_real_time_hotest": ("实时热门电影", "电影", True),
    "movie_weekly_best": ("一周口碑电影榜", "电影", True),
    "tv_real_time_hotest": ("实时热门电视", "电视剧", True),
    "tv_chinese_best_weekly": ("华语口碑剧集榜", "电视剧", True),
    "tv_global_best_weekly": ("全球口碑剧集榜", "电视剧", True),
    "show_chinese_best_weekly": ("国内口碑综艺榜", "电视剧", True),
    "show_global_best_weekly": ("国外口碑综艺榜", "电视剧", True),
    "tv_domestic": ("热播新剧国产剧", "电视剧", False),
    "tv_american": ("热播新剧欧美剧", "电视剧", False),
    "tv_japanese": ("热播新剧日剧", "电视剧", False),
    "tv_korean": ("热播新剧韩剧", "电视剧", False),
    "tv_animation": ("热播新剧动画", "电视剧", False),
}
ROUTE_PROVENANCE = "deployed RSSHub list-COiEyPon.mjs ca633ed2908b0684f156f15c8c575897ecea76ab58d6fcbb2fe4681617c401e7"
LEGACY_RANKS = {"movie-weekly": "movie_weekly_best", "movie-real-time": "movie_real_time_hotest"}
SECRET_QUERY = re.compile(r"(?:token|key|secret|password|passwd|cookie|authorization|signature)", re.I)
DOUBAN_SUBJECT = re.compile(r"^/subject/(\d+)/?$")


def _digest(value, *, default=None) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=default).encode()).hexdigest()


def _provider_value(value):
    if isinstance(value, Enum):
        return value.value
    raise TypeError("UNSUPPORTED_PROVIDER_VALUE")


def _url(value: str, *, base=False) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("INVALID_URL")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("INVALID_URL")
    if base and parts.query:
        raise ValueError("BASE_QUERY_NOT_ALLOWED")
    decoded = unquote(parts.path)
    if any(segment in {".", ".."} for segment in decoded.split("/")) or "//" in decoded:
        raise ValueError("URL_PATH_ESCAPE")
    if not base and any(SECRET_QUERY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise ValueError("SECRET_QUERY_NOT_ALLOWED")
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path.rstrip("/") if base else parts.path,
                       parts.query, ""))


class RequestBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timeout: float = Field(default=12.0, ge=1, le=60)
    response_bytes: int = Field(default=524288, ge=1024, le=2097152)
    items: int = Field(default=50, ge=1, le=200)
    requests_per_run: int = Field(default=20, ge=1, le=100)
    interval_min_seconds: float = Field(default=3, ge=0, le=86400)
    interval_max_seconds: float = Field(default=10, ge=0, le=86400)
    retry_limit: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def interval(self):
        if self.interval_max_seconds < self.interval_min_seconds:
            raise ValueError("INVALID_INTERVAL")
        return self


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    enabled: bool = True
    kind: Literal["rsshub", "custom"]
    route_key: str | None = None
    url: str | None = None
    source_type_hint: Literal["TV", "Movie"] | None = None
    media_type_allowlist: list[Literal["电影", "电视剧"]] = Field(default_factory=list, max_length=2)
    proxy: bool = False
    minimum_release_year: int | None = Field(default=None, ge=1870, le=2999)
    minimum_rating: float | None = Field(default=None, ge=0, le=10)
    rating_source: Literal["recognized_provider"] = "recognized_provider"
    season_scope: Literal["identified", "all_known"] | None = None
    existing_media_action: Literal["record_only", "manage_authorized"] | None = None
    destination_templates: dict[Literal["movie", "tv", "anime"], str] = Field(default_factory=dict)
    destination_category_bindings: dict[str, Literal["movie", "tv", "anime"]] = Field(default_factory=dict)
    media_category_id: int | None = Field(default=None, gt=0)
    legacy_rank_key: str | None = None
    legacy_original_text: str | None = None
    import_diagnostics: list[str] = Field(default_factory=list)
    request_budget: RequestBudget | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "rsshub":
            if self.route_key not in ROUTES or self.url is not None:
                raise ValueError("INVALID_RSSHUB_ROUTE")
        elif self.url is None or self.route_key is not None:
            raise ValueError("CUSTOM_URL_REQUIRED")
        if self.url is not None:
            self.url = _url(self.url)
        if (len(self.destination_category_bindings) > 100 or
                any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)
                    for key in self.destination_category_bindings)):
            raise ValueError("INVALID_DESTINATION_CATEGORY_BINDING")
        if any(destination not in self.destination_templates
               for destination in self.destination_category_bindings.values()):
            raise ValueError("DESTINATION_TEMPLATE_UNBOUND")
        hinted = "电视剧" if self.source_type_hint == "TV" else "电影" if self.source_type_hint == "Movie" else None
        if hinted and self.media_type_allowlist and hinted not in self.media_type_allowlist:
            raise ValueError("TYPE_HINT_CONFLICT")
        return self


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    rsshub_base_url: str | None = None
    allowed_private_ranges: list[str] = Field(default_factory=list, max_length=8)
    cron: str = "0 8 * * *"
    request_budget: RequestBudget = Field(default_factory=RequestBudget)
    media_type_allowlist: list[Literal["电影", "电视剧"]] = Field(default_factory=list, max_length=2)
    minimum_release_year: int | None = Field(default=None, ge=1870, le=2999)
    minimum_rating: float | None = Field(default=None, ge=0, le=10)
    rating_source: Literal["recognized_provider"] = "recognized_provider"
    season_scope: Literal["identified", "all_known"] = "identified"
    existing_media_action: Literal["record_only", "manage_authorized"] = "record_only"
    sources: list[SourceConfig] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def values(self):
        if self.rsshub_base_url is not None:
            self.rsshub_base_url = _url(self.rsshub_base_url, base=True)
        try:
            self.allowed_private_ranges = [str(ipaddress.ip_network(value, strict=False))
                                           for value in self.allowed_private_ranges]
        except ValueError as error:
            raise ValueError("INVALID_PRIVATE_RANGE") from error
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("DUPLICATE_SOURCE_ID")
        if any(source.kind == "rsshub" for source in self.sources) and not self.rsshub_base_url:
            # Disabled configuration may be imported before the user supplies a base.
            if self.enabled:
                raise ValueError("RSSHUB_BASE_REQUIRED")
        return self


def source_url(config: DiscoveryConfig, source: SourceConfig) -> str:
    if source.kind == "custom":
        return _url(source.url or "")
    if not config.rsshub_base_url:
        raise ValueError("RSSHUB_BASE_REQUIRED")
    base = config.rsshub_base_url.rstrip("/") + "/"
    route = f"douban/list/{quote(source.route_key or '', safe='')}"
    return _url(urljoin(base, route) + f"?limit={(source.request_budget or config.request_budget).items}")


@dataclass(frozen=True)
class RSSItem:
    title: str
    link: str
    guid: str
    description: str
    item_key: str
    raw_revision: str
    douban_subject_id: str | None = None
    year: int | None = None
    rating: float | None = None


def _decode_xml(body: bytes) -> str:
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return body.decode("utf-16")
    return body.decode("utf-8-sig")


def parse_rss(body: bytes, *, max_bytes=524288, max_items=50, max_text=8192, max_depth=24) -> list[RSSItem]:
    if not isinstance(body, bytes) or len(body) > max_bytes:
        raise ValueError("RSS_TOO_LARGE")
    try:
        text = _decode_xml(body)
    except UnicodeError as error:
        raise ValueError("RSS_ENCODING_INVALID") from error
    folded = text.casefold()
    if "<!doctype" in folded or "<!entity" in folded:
        raise ValueError("RSS_ENTITY_DECLARATION")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError("RSS_MALFORMED") from error
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise ValueError("RSS_TOO_DEEP")
        stack.extend((child, depth + 1) for child in node)
    items = list(root.iter("item"))
    if len(items) > max_items:
        items = items[:max_items]
    result = []
    for item in items:
        def field(name):
            node = item.find(name)
            value = "" if node is None or node.text is None else " ".join(node.text.split())
            if len(value) > max_text:
                raise ValueError("RSS_TEXT_TOO_LARGE")
            return value
        title, link, guid, description = field("title"), field("link"), field("guid"), field("description")
        if not title:
            continue
        subject = None
        if link:
            parts = urlsplit(link)
            match = DOUBAN_SUBJECT.fullmatch(parts.path)
            if parts.scheme == "https" and parts.hostname in {"movie.douban.com", "www.douban.com"} and match:
                subject = match.group(1)
        raw = dict(title=title, link=link, guid=guid, description=description)
        revision = _digest(raw)
        local = guid or link or _digest([title, description])
        result.append(RSSItem(title, link, guid, description, _digest(local), revision, subject))
    return result


class FetchError(RuntimeError):
    def __init__(self, code, *, retry_after=None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    status: int = 200
    final_url: str | None = None


class HostRSSFetcher:
    """Public SDK transport with same-origin redirects and a compressed-byte refusal."""
    def __init__(self, base_url, *, proxy=False, redirects=2, clock=None, allowed_private_ranges=()):
        import time
        self.base_url, self.proxy, self.redirects = base_url, proxy, redirects
        self.clock = clock or time.monotonic
        self.allowed_private_ranges = list(allowed_private_ranges)

    async def _safe(self, url, configured):
        from app.sdk.network import SecurityUtils
        configured_parts = urlsplit(configured)
        host = configured_parts.hostname
        ranges = list(self.allowed_private_ranges)
        try:
            address = ipaddress.ip_address(host)
            ranges.append(f"{address}/{address.max_prefixlen}")
        except ValueError:
            pass
        verdict = await SecurityUtils.evaluate_url_safety_async(
            url, allowed_domains=[configured_parts.netloc], strict=True, block_private=True,
            allowed_private_ranges=sorted(set(ranges)) or None)
        if not getattr(verdict, "allowed", False):
            raise FetchError("UNSAFE_URL")

    async def __call__(self, url, source, budget):
        return await self._fetch(url, source, budget)

    async def _fetch(self, url, source, budget):
        import httpx2
        from app.sdk.network import AsyncRequestUtils
        from app.sdk.config import settings
        configured = self.base_url if source.kind == "rsshub" else url
        origin = urlsplit(configured)
        current = url
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget.timeout
        def remaining():
            value = deadline - loop.time()
            if value <= 0:
                raise FetchError("TIMEOUT")
            return value
        configured_proxies = settings.PROXY if self.proxy else None
        if isinstance(configured_proxies, str):
            proxy = configured_proxies.strip() or None
        elif isinstance(configured_proxies, dict):
            proxy = configured_proxies.get("https") or configured_proxies.get("http")
            proxy = proxy.strip() if isinstance(proxy, str) else None
        else:
            proxy = None
        try:
            client = httpx2.AsyncClient(http2=True, proxy=proxy, timeout=httpx2.Timeout(budget.timeout),
                                        verify=True, trust_env=False, follow_redirects=False)
        except Exception as error:
            raise FetchError("NETWORK_ERROR") from error
        try:
            async with asyncio.timeout(budget.timeout):
                async with client:
                    for hop in range(self.redirects + 1):
                        current_parts = urlsplit(_url(current))
                        if (current_parts.scheme, current_parts.hostname, current_parts.port) != (origin.scheme, origin.hostname, origin.port):
                            raise FetchError("REDIRECT_ORIGIN_CHANGED")
                        await self._safe(current, configured)
                        timeout = remaining()
                        try:
                            transport = AsyncRequestUtils(client=client, timeout=timeout, follow_redirects=False)
                            async with transport.get_stream(
                                    current, raise_exception=True,
                                    headers={"Accept-Encoding": "identity"}) as response:
                                if response is None:
                                    raise FetchError("NETWORK_ERROR")
                                if response.status_code in {301, 302, 303, 307, 308}:
                                    if hop == self.redirects or not response.headers.get("Location"):
                                        raise FetchError("REDIRECT_LIMIT")
                                    current = urljoin(current, response.headers["Location"])
                                    continue
                                if response.status_code == 429:
                                    value = response.headers.get("Retry-After", "")
                                    raise FetchError("RATE_LIMITED", retry_after=int(value) if value.isdigit() else None)
                                if response.status_code != 200:
                                    raise FetchError("HTTP_ERROR")
                                if response.headers.get("Content-Encoding", "identity").casefold() not in {"", "identity"}:
                                    raise FetchError("CONTENT_ENCODING_UNSUPPORTED")
                                chunks, size = [], 0
                                async for chunk in response.aiter_bytes(8192):
                                    size += len(chunk)
                                    if size > budget.response_bytes:
                                        raise FetchError("RSS_TOO_LARGE")
                                    chunks.append(chunk)
                                remaining()
                                return FetchResult(b"".join(chunks), final_url=current)
                        except FetchError:
                            raise
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            raise FetchError("NETWORK_ERROR") from error
                    raise FetchError("REDIRECT_LIMIT")
        except TimeoutError as error:
            raise FetchError("TIMEOUT") from error
        except FetchError:
            raise
        except Exception as error:
            raise FetchError("NETWORK_ERROR") from error


def _legacy_line(raw: str, index: int):
    original = raw
    if raw.count("@@") > 1:
        raise ValueError("AMBIGUOUS_TYPE_HINT")
    hint = None
    if "@@" in raw:
        raw, hint = raw.split("@@", 1)
        if ";" in hint:
            hint, tail = hint.split(";", 1)
            raw += ";" + tail
        hint = hint.strip().casefold()
        hint = "TV" if hint == "tv" else "Movie" if hint == "movie" else None
        if hint is None:
            raise ValueError("INVALID_TYPE_HINT")
    parts = raw.split(";")
    if len(parts) > 3:
        raise ValueError("AMBIGUOUS_LEGACY_DELIMITER")
    url = _url(parts[0].strip())
    destination = parts[1] if len(parts) > 1 else None
    allow = parts[2].strip() if len(parts) > 2 else ""
    allowed = []
    if allow:
        if allow == "@movies@": allowed = ["电影"]
        elif allow == "@tv@": allowed = ["电视剧"]
        else: raise ValueError("INVALID_LEGACY_ALLOWLIST")
    hinted = "电视剧" if hint == "TV" else "电影" if hint == "Movie" else None
    if hinted and allowed and hinted not in allowed:
        raise ValueError("TYPE_HINT_CONFLICT")
    paths = {}
    if destination is not None:
        split = destination.split("#")
        if len(split) > 3:
            raise ValueError("INVALID_DESTINATION_TEMPLATE")
        paths = {"movie": split[0], "tv": split[1] if len(split) > 1 else split[0],
                 "anime": split[2] if len(split) > 2 else split[1] if len(split) > 1 else split[0]}
    return dict(id=f"legacy-{index}", kind="custom", url=url, source_type_hint=hint,
                media_type_allowlist=allowed, destination_templates=paths,
                legacy_original_text=original)


def import_legacy(config: dict, history=()) -> dict:
    sources, diagnostics = [], []
    raw_lines = config.get("rss_addrs") or ""
    if isinstance(raw_lines, str):
        for index, raw in enumerate(raw_lines.splitlines(), 1):
            if not raw.strip(): continue
            try: sources.append(_legacy_line(raw.strip(), index))
            except ValueError as error: diagnostics.append(f"LEGACY_SOURCE_ERROR:{index}:{error}")
    for rank in config.get("ranks") or []:
        route = LEGACY_RANKS.get(rank)
        if route:
            sources.append(dict(id=f"legacy-rank-{len(sources)+1}", kind="rsshub", route_key=route,
                                legacy_rank_key=rank, legacy_original_text=rank))
        else: diagnostics.append(f"LEGACY_RANK_RESELECT_REQUIRED:{rank}")
    for source in sources:
        source["proxy"] = bool(config.get("proxy"))
        category = config.get("media_category_id")
        if type(category) is int and category > 0:
            source["media_category_id"] = category
    imported_history = []
    for row in history or ():
        raw = dict(row)
        source = str(row.get("media_source") or "").strip().casefold()
        mid = str(row.get("tmdbid") or "").strip()
        identity = [source or "themoviedb", mid] if mid not in {"", "0", "None", "none", "null"} else None
        old_time = row.get("time_full") or row.get("time")
        time_diagnostics = []
        if old_time:
            try:
                if datetime.fromisoformat(str(old_time)).tzinfo is None:
                    time_diagnostics.append("TIMEZONE_UNKNOWN")
            except ValueError:
                time_diagnostics.append("TIME_INVALID")
        imported_history.append(dict(raw=raw, title=row.get("title"), year=row.get("year"), identity=identity,
                                     old_status=row.get("status"), old_time=row.get("time_full") or row.get("time"),
                                     state="LEGACY_UNVERIFIED", diagnostics=time_diagnostics))
    interval = config.get("sleep_time")
    request_budget = ({"interval_min_seconds": interval, "interval_max_seconds": interval}
                      if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval >= 0 else {})
    return {"sources": sources, "history": imported_history, "diagnostics": diagnostics,
            "config": {"cron": config.get("cron") or "0 8 * * *", "proxy": config.get("proxy"),
                       "minimum_release_year": config.get("release_year"), "minimum_rating": config.get("vote"),
                       "season_scope": "all_known" if config.get("is_seasons_all", True) else "identified",
                       "media_type_allowlist": ["电影"] if config.get("is_only_movies") else [],
                       "request_budget": request_budget,
                       "rate_limit_scope": "origin" if config.get("is_exit_ip_rate_limit") else "source"},
            "actions": {"run_once": False, "clear": False, "clear_unrecognized": False}}


class DiscoveryService:
    def __init__(self, repository, owner, meta_service, recognizer, config: DiscoveryConfig, *, fetch,
                 clock=None, inventory=None, inventory_refresh=None, authorized=None, excluded=None, current=None,
                 owner_check=None, owner_snapshot=None, accepted=None, instance_id="SubscriBetter", ai=None):
        self.repository, self.owner, self.meta_service, self.recognizer = repository, owner, meta_service, recognizer
        self.config, self.fetch = config.model_copy(deep=True), fetch
        self.clock, self.inventory = clock or __import__("time").time, inventory or (lambda _: {"state": "UNKNOWN"})
        self.inventory_refresh = inventory_refresh
        self.authorized, self.excluded = authorized or (lambda *_: False), excluded or (lambda _: False)
        self.accepted = accepted
        self.current, self.owner_check, self.owner_snapshot = current or (lambda: False), owner_check, owner_snapshot
        self.instance_id, self.ai = instance_id, ai
        self.config_digest = _digest(self.config.model_dump(mode="json"))
        for source in self.config.sources:
            self._save_source(source)

    def _save_source(self, source):
        now = utcnow()
        revision = _digest([self.config.rsshub_base_url, source.model_dump(mode="json")])
        with self.repository.connection(write=True) as db:
            prior = db.execute("SELECT config_revision FROM discovery_sources WHERE source_id=?", (source.id,)).fetchone()
            db.execute("INSERT INTO discovery_sources(source_id,config_revision,config,last_state,last_reason,failures,next_due,last_success,last_failure,updated_at) VALUES(?,?,?,'NEVER','',0,0,NULL,NULL,?) ON CONFLICT(source_id) DO UPDATE SET config_revision=excluded.config_revision,config=excluded.config,failures=CASE WHEN discovery_sources.config_revision!=excluded.config_revision THEN 0 ELSE discovery_sources.failures END,next_due=CASE WHEN discovery_sources.config_revision!=excluded.config_revision THEN 0 ELSE discovery_sources.next_due END,last_reason=CASE WHEN discovery_sources.config_revision!=excluded.config_revision THEN 'SOURCE_CONFIG_CHANGED' ELSE discovery_sources.last_reason END,updated_at=excluded.updated_at",
                       (source.id, revision, json.dumps(source.model_dump(mode="json"), ensure_ascii=False), now))
            if prior and prior[0] != revision:
                db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(NULL,?,?,?)",
                           (f"DISCOVERY_SOURCE_CONFIG_RESET:{source.id}", "discovery", now))

    def catalog(self):
        configured = {source.id: source for source in self.config.sources}
        rows = []
        with self.repository.connection() as db:
            states = {row["source_id"]: dict(row) for row in db.execute("SELECT * FROM discovery_sources")}
        for key, (label, media_type, links) in ROUTES.items():
            sources = [source for source in configured.values() if source.route_key == key]
            rows.append(dict(route_key=key, label=label, media_type=media_type, item_links_observed=links,
                             provenance=ROUTE_PROVENANCE, configured_ids=[source.id for source in sources],
                             full_url=source_url(self.config, sources[0]) if sources and self.config.rsshub_base_url else None,
                             states=[states.get(source.id) for source in sources]))
        for source in configured.values():
            if source.kind == "custom":
                rows.append(dict(route_key=None, label=source.id, media_type=None,
                                 item_links_observed=None, provenance="user-configured custom RSS",
                                 configured_id=source.id, full_url=source_url(self.config, source),
                                 state=states.get(source.id)))
        return rows

    def _origin(self, url):
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}".casefold()

    def _reserve(self, source, url, selected):
        now = float(self.clock())
        origin_key = "discovery_origin:" + self._origin(url)
        with self.repository.connection(write=True) as db:
            row = db.execute("SELECT next_due,failures FROM discovery_sources WHERE source_id=?", (source.id,)).fetchone()
            setting = db.execute("SELECT value FROM settings WHERE key=?", (origin_key,)).fetchone()
            origin_state = json.loads(setting[0]) if setting else {}
            origin_due = float(origin_state.get("next_due", 0))
            budget = source.request_budget or self.config.request_budget
            if row["failures"] > budget.retry_limit:
                return "RETRY_EXHAUSTED"
            if now < max(float(row[0] or 0), origin_due):
                return "NOT_DUE"
            peers = [candidate for candidate in self.config.sources
                     if candidate.enabled and candidate.id in selected
                     and self._origin(source_url(self.config, candidate)) == self._origin(url)]
            eligible = []
            for candidate in peers:
                candidate_row = db.execute(
                    "SELECT failures,next_due FROM discovery_sources WHERE source_id=?", (candidate.id,)).fetchone()
                candidate_budget = candidate.request_budget or self.config.request_budget
                if (candidate_row and candidate_row["failures"] <= candidate_budget.retry_limit
                        and float(candidate_row["next_due"] or 0) <= now):
                    eligible.append(candidate.id)
            if eligible:
                cursor = origin_state.get("last_source")
                order = [candidate.id for candidate in peers]
                start = (order.index(cursor) + 1) % len(order) if cursor in order else 0
                rotated = order[start:] + order[:start]
                if source.id != next(candidate_id for candidate_id in rotated if candidate_id in eligible):
                    return "ORIGIN_FAIRNESS"
            low, high = budget.interval_min_seconds, budget.interval_max_seconds
            wait = low if low == high else random.SystemRandom().uniform(low, high)
            db.execute("UPDATE discovery_sources SET next_due=?,last_state='FETCHING',last_reason='',updated_at=? WHERE source_id=?",
                       (now + wait, utcnow(), source.id))
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (origin_key, json.dumps({"next_due": now + wait, "last_source": source.id,
                                               "reason": "REQUEST_SPACING"})))
            return None

    def _source_state(self, source_id, state, reason="", *, success=False, retry_after=None, origin=None):
        now = float(self.clock())
        with self.repository.connection(write=True) as db:
            if success:
                db.execute("UPDATE discovery_sources SET last_state=?,last_reason=?,failures=0,last_success=?,updated_at=? WHERE source_id=?",
                           (state, reason, utcnow(), utcnow(), source_id))
            else:
                db.execute("UPDATE discovery_sources SET last_state=?,last_reason=?,failures=failures+1,last_failure=?,updated_at=? WHERE source_id=?",
                           (state, reason, utcnow(), utcnow(), source_id))
            if retry_after and origin:
                key = "discovery_origin:" + origin
                prior = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                value = json.loads(prior[0]) if prior else {}
                value.update(next_due=max(float(value.get("next_due", 0)), now + retry_after), reason=reason)
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, json.dumps(value)))

    def retry_sources(self, source_ids):
        ids = sorted(set(source_ids))
        configured = {source.id for source in self.config.sources}
        if not ids or len(ids) > 100 or any(not isinstance(value, str) or value not in configured for value in ids):
            raise ValueError("INVALID_SOURCE_IDS")
        with self.repository.connection(write=True) as db:
            placeholders = ",".join("?" for _ in ids)
            cursor = db.execute(f"UPDATE discovery_sources SET failures=0,next_due=0,last_state='RETRY_REQUESTED',last_reason='EXPLICIT_RETRY',updated_at=? WHERE source_id IN ({placeholders})",
                                (utcnow(), *ids))
            db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(NULL,?,?,?)",
                       ("DISCOVERY_SOURCE_RETRY:" + _digest(ids), "admin", utcnow()))
        return cursor.rowcount

    def _owned(self, source_id):
        if not self.current() or not callable(self.owner_check) or not callable(self.owner_snapshot): return False
        expected = dict(module="discovery", instance_id=self.instance_id, config_digest=self.config_digest, route_scope=source_id)
        try:
            receipt = self.owner_check("discovery", self.instance_id, self.config_digest, source_id)
            fresh = self.owner_snapshot("discovery", self.instance_id, self.config_digest, source_id)
            return (isinstance(receipt, dict) and receipt.get("status") == "ACTIVE"
                    and isinstance(receipt.get("receipt_id"), str) and bool(receipt["receipt_id"])
                    and receipt.get("expected_new_feature_set") == expected
                    and isinstance(receipt.get("selected_old_disable_receipts"), list)
                    and all(isinstance(x, str) and x for x in receipt["selected_old_disable_receipts"])
                    and isinstance(fresh, dict) and re.fullmatch(r"[a-f0-9]{64}", fresh.get("fingerprint", ""))
                    and receipt.get("fresh_handler_config_fingerprint") == fresh["fingerprint"]
                    and fresh.get("overlaps") == [] and fresh.get("unclassified") == [])
        except Exception:
            return False

    async def test_source(self, source_id=None, *, proposed=None):
        if (source_id is None) == (proposed is None):
            raise ValueError("EXACTLY_ONE_SOURCE_REQUIRED")
        source = proposed or next((x for x in self.config.sources if x.id == source_id), None)
        if not source:
            raise ValueError("SOURCE_NOT_FOUND")
        url = source_url(self.config, source)
        budget = source.request_budget or self.config.request_budget
        try:
            result = await self.fetch(url, source, budget)
            items = parse_rss(result.body, max_bytes=budget.response_bytes, max_items=budget.items)
            return dict(source_id=source.id, url=url, items=len(items), fetch_only=True, state="SUCCESS")
        except FetchError as error:
            return dict(source_id=source.id, items=0, fetch_only=True, state="FAILED", reason=error.code)
        except (ValueError, UnicodeError) as error:
            allowed = {"RSS_TOO_LARGE", "RSS_ENCODING_INVALID", "RSS_ENTITY_DECLARATION",
                       "RSS_MALFORMED", "RSS_TOO_DEEP", "RSS_TEXT_TOO_LARGE"}
            code = str(error) if str(error) in allowed else "SOURCE_FAILED"
            return dict(source_id=source.id, items=0, fetch_only=True, state="FAILED", reason=code)

    async def run(self, source_ids=None):
        selected = set(source_ids or [source.id for source in self.config.sources if source.enabled])
        owned = set()
        for source in self.config.sources:
            if (source.enabled and source.id in selected
                    and await _drainable_to_thread(self._owned, source.id)):
                owned.add(source.id)
        result = {"sources": {}}
        requests = 0
        for source in self.config.sources:
            if source.id not in selected or not source.enabled: continue
            if requests >= self.config.request_budget.requests_per_run:
                result["sources"][source.id] = {"state": "RATELIMITED", "reason": "RUN_REQUEST_BUDGET"}; continue
            url, origin = source_url(self.config, source), self._origin(source_url(self.config, source))
            if (source.id not in owned
                    or not await _drainable_to_thread(self._owned, source.id)):
                owned.discard(source.id)
                result["sources"][source.id] = {"state": "OWNER_UNBOUND", "reason": "OWNER_UNBOUND"}; continue
            reserve = self._reserve(source, url, owned)
            if reserve:
                state = "DEFERRED" if reserve == "RETRY_EXHAUSTED" else "RATELIMITED"
                result["sources"][source.id] = {"state": state, "reason": reserve}; continue
            requests += 1
            try:
                budget = source.request_budget or self.config.request_budget
                fetched = await self.fetch(url, source, budget)
                if not self.current():
                    result["sources"][source.id] = {"state": "DEFERRED", "reason": "STALE_GENERATION"}
                    continue
                items = parse_rss(fetched.body, max_bytes=budget.response_bytes, max_items=budget.items)
                states = [await _drainable_to_thread(self._observe, source, item) for item in items]
                complete = {"SUBMITTED", "ALREADY_MANAGED", "EXISTING", "REJECTED"}
                state = "PARTIAL" if "PARTIAL" in states or (any(x not in complete for x in states) and any(x in complete for x in states)) else "SUCCESS"
                self._source_state(source.id, state, success=True)
                result["sources"][source.id] = {"state": state, "reason": "", "items": len(items)}
            except FetchError as error:
                self._source_state(source.id, "FAILED", error.code, retry_after=error.retry_after, origin=origin)
                result["sources"][source.id] = {"state": "FAILED", "reason": error.code}
            except Exception as error:
                code = str(error) if str(error) in {"RSS_TOO_LARGE", "RSS_MALFORMED", "RSS_ENTITY_DECLARATION", "RSS_TOO_DEEP"} else "SOURCE_FAILED"
                self._source_state(source.id, "FAILED", code)
                result["sources"][source.id] = {"state": "FAILED", "reason": code}
        return result

    def _observe(self, source, item):
        now, policy = utcnow(), _digest([self.config_digest, source.model_dump(mode="json")])
        due = float(self.clock())
        budget = source.request_budget or self.config.request_budget
        with self.repository.connection(write=True) as db:
            row = db.execute("SELECT * FROM discovery_records WHERE source_id=? AND item_key=? AND raw_revision=?",
                             (source.id, item.item_key, item.raw_revision)).fetchone()
            if row:
                reuse_resolved = row["filter_revision"] == policy
                db.execute("UPDATE discovery_records SET last_seen=?,visible=1 WHERE id=?", (now, row["id"]))
                metadata_wait = self._metadata_wait(db, row, source)
                if (row["state"] in {"SUBMITTED", "ALREADY_MANAGED", "EXISTING", "INGESTED",
                                     "STOPPED", "RELEASED", "REJECTED"}
                        and row["filter_revision"] == policy
                        and (not metadata_wait or due < float(row["next_due"] or 0))):
                    return row["state"]
                if row["filter_revision"] == policy and row["state"] in {"UNRECOGNIZED", "DEFERRED", "PARTIAL"}:
                    if (row["retry_count"] > budget.retry_limit and not metadata_wait) or due < float(row["next_due"] or 0):
                        return row["state"]
                elif row["filter_revision"] != policy:
                    db.execute("UPDATE discovery_records SET retry_count=0,next_due=0,filter_revision=? WHERE id=?", (policy, row["id"]))
                record_id = row["id"]
            else:
                reuse_resolved = False
                cursor = db.execute("INSERT INTO discovery_records(source_id,item_key,raw_revision,raw,state,reason,retry_count,next_due,filter_revision,data,visible,first_seen,last_seen) VALUES(?,?,?,?, 'UNRECOGNIZED','',0,0,?,'{}',1,?,?)",
                                    (source.id, item.item_key, item.raw_revision, json.dumps(asdict(item), ensure_ascii=False), policy, now, now))
                record_id = cursor.lastrowid
        state = self._process(record_id, source, item, policy, reuse_resolved=reuse_resolved)
        with self.repository.connection(write=True) as db:
            row = db.execute("SELECT * FROM discovery_records WHERE id=?", (record_id,)).fetchone()
            metadata_wait = self._metadata_wait(db, row, source)
            if state in {"UNRECOGNIZED", "DEFERRED", "PARTIAL"} or metadata_wait:
                delay = max(budget.interval_min_seconds, 86400) if metadata_wait else budget.interval_min_seconds
                increment = 1 if state in {"UNRECOGNIZED", "DEFERRED", "PARTIAL"} else 0
                db.execute("UPDATE discovery_records SET retry_count=retry_count+?,next_due=? WHERE id=?",
                           (increment, due + delay, record_id))
        return state

    def _metadata_wait(self, db, row, source):
        if row["reason"] == "SEASON_METADATA_UNKNOWN":
            return True
        if db.execute("SELECT 1 FROM discovery_targets WHERE record_id=? AND reason IN "
                      "('SEASON_NOT_AIRED','SEASON_AIR_DATE_UNKNOWN','SEASON_METADATA_UNKNOWN') LIMIT 1",
                      (row["id"],)).fetchone():
            return True
        if (source.season_scope or self.config.season_scope) == "all_known":
            try:
                return json.loads(row["data"] or "{}").get("identity", {}).get("media_type") == "电视剧"
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _field(value, name, default=None):
        return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)

    def _set_record(self, record_id, state, reason, data):
        with self.repository.connection(write=True) as db:
            db.execute("UPDATE discovery_records SET state=?,reason=?,data=?,last_seen=? WHERE id=?",
                       (state, reason, json.dumps(data, ensure_ascii=False), utcnow(), record_id))
            db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(NULL,?,?,?)",
                       (f"DISCOVERY:{record_id}:{state}:{reason}", "discovery", utcnow()))
        return state

    def _process(self, record_id, source, item, policy, *, reuse_resolved=False):
        correction = self.meta_service.parse("discovery:" + item.raw_revision, item.title)
        if correction.status != "OK" and self.ai is not None:
            correction, _ = self.ai.assist(item.title, "", correction, corrector=self.meta_service.corrector)
        data = {"raw": asdict(item), "correction": correction.record(), "filter_revision": policy}
        if correction.status != "OK":
            return self._set_record(record_id, "DEFERRED", "META_" + correction.status, data)
        requested_type = ("电视剧" if source.source_type_hint == "TV" else "电影"
                          if source.source_type_hint == "Movie" else
                          ROUTES.get(source.route_key, (None, None, None))[1])
        declared = ("douban", item.douban_subject_id) if item.douban_subject_id else None
        try:
            media = self.recognizer.recognize(correction.meta, declared, media_type=requested_type)
            identity = self.recognizer.identity(media) if media is not None else None
        except Exception:
            media, identity = None, None
        if not media or not identity or len(identity) != 2 or not all(identity):
            return self._set_record(record_id, "UNRECOGNIZED", "IDENTITY_UNKNOWN", data)
        media_type = self._field(self._field(media, "type"), "value", self._field(media, "type"))
        source_id, media_id = str(identity[0]).casefold(), str(identity[1])
        if media_type not in {"电影", "电视剧"}:
            return self._set_record(record_id, "DEFERRED", "TYPE_UNKNOWN", data)
        if requested_type and media_type != requested_type:
            return self._set_record(record_id, "DEFERRED", "TYPE_HINT_CONFLICT", data)
        mapping = None
        if declared:
            if source_id == declared[0]:
                if media_id != declared[1]:
                    return self._set_record(record_id, "DEFERRED", "SOURCE_ID_CONFLICT", data)
                try:
                    secondary = self.recognizer.source_identity(media, declared[0])
                except Exception:
                    secondary = None
                if (isinstance(secondary, dict) and
                        (secondary.get("state") == "CONFLICT" or
                         (secondary.get("state") == "VERIFIED"
                          and str(secondary.get("media_id")) != declared[1]))):
                    return self._set_record(record_id, "DEFERRED", "SOURCE_ID_CONFLICT", data)
                mapping = {"state": "CANONICAL", "source": declared[0]}
            else:
                try:
                    mapping = self.recognizer.source_identity(media, declared[0])
                except Exception:
                    mapping = None
                if not isinstance(mapping, dict) or mapping.get("state") == "UNKNOWN":
                    return self._set_record(record_id, "DEFERRED", "SOURCE_MAPPING_UNKNOWN", data)
                if mapping.get("state") != "VERIFIED" or str(mapping.get("media_id")) != declared[1]:
                    return self._set_record(record_id, "DEFERRED", "SOURCE_ID_CONFLICT", data)
        allowed = set(self.config.media_type_allowlist or ["电影", "电视剧"])
        if source.media_type_allowlist: allowed &= set(source.media_type_allowlist)
        if media_type not in allowed:
            return self._set_record(record_id, "REJECTED", "TYPE_NOT_ALLOWED", data)
        year = str(self._field(media, "year", "") or "")
        year_value = int(year) if re.fullmatch(r"\d{4}", year) else None
        requested_year = item.year or self._field(correction.meta, "year")
        requested_year = int(requested_year) if str(requested_year or "").isdigit() else None
        if not declared and requested_year is None:
            return self._set_record(record_id, "DEFERRED", "IDENTITY_EVIDENCE_INSUFFICIENT", data)
        if requested_year and year_value is None:
            return self._set_record(record_id, "DEFERRED", "YEAR_UNKNOWN", data)
        if requested_year and requested_year != year_value:
            return self._set_record(record_id, "DEFERRED", "YEAR_CONFLICT", data)
        minimum_year = source.minimum_release_year or self.config.minimum_release_year
        if minimum_year and year_value is None:
            return self._set_record(record_id, "DEFERRED", "YEAR_UNKNOWN", data)
        if minimum_year and year_value < minimum_year:
            return self._set_record(record_id, "REJECTED", "YEAR_BELOW_MINIMUM", data)
        details = self._field(media, "tmdb_info", {}) or {}
        rating_value = details.get("vote_average") if source_id == "themoviedb" and isinstance(details, dict) else None
        if isinstance(rating_value, bool) or not isinstance(rating_value, (int, float)) or not math.isfinite(rating_value) or not 0 < rating_value <= 10:
            rating = None
        else:
            rating = {"provider": source_id, "field": "tmdb_info.vote_average", "value": rating_value}
        if rating is not None:
            rating.update(observed_at=utcnow(), evidence_ref="provider-payload:" + _digest(details, default=_provider_value))
        try:
            classification = self.recognizer.classify(media)
        except Exception:
            return self._set_record(record_id, "DEFERRED", "CLASSIFICATION_UNAVAILABLE", data)
        data.update(identity={"media_type": media_type, "media_source": source_id, "media_id": media_id, "year": year_value},
                     identity_evidence={"declared_source": declared[0] if declared else None,
                                        "declared_id_digest": _digest(declared) if declared else None,
                                        "requested_type": requested_type, "requested_year": requested_year,
                                        "mapping_state": mapping.get("state") if mapping else None},
                    rating=rating, classification=classification)
        minimum_rating = source.minimum_rating if source.minimum_rating is not None else self.config.minimum_rating
        if minimum_rating is not None and rating is None:
            return self._set_record(record_id, "DEFERRED", "RATING_UNKNOWN", data)
        if minimum_rating is not None and rating["value"] < minimum_rating:
            return self._set_record(record_id, "REJECTED", "RATING_BELOW_MINIMUM", data)
        if media_type == "电影":
            targets = [Target(media_type, source_id, media_id)]
            deferred = {}
        else:
            today = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            known, deferred = [], {}
            for season in details.get("seasons", []) if isinstance(details, dict) else []:
                number, air = season.get("season_number"), season.get("air_date")
                if type(number) is not int or number <= 0:
                    continue
                known.append(number)
                try:
                    aired = datetime.strptime(air, "%Y-%m-%d").date() <= today if isinstance(air, str) else None
                except ValueError:
                    aired = None
                if aired is False:
                    deferred[number] = "SEASON_NOT_AIRED"
                elif aired is None:
                    deferred[number] = "SEASON_AIR_DATE_UNKNOWN"
            known = sorted(set(known))
            scope = source.season_scope or self.config.season_scope
            if scope == "all_known": seasons = known
            else:
                identified = self._field(correction.meta, "begin_season")
                seasons = [identified] if type(identified) is int and identified > 0 else []
                if seasons and identified not in known:
                    deferred[identified] = "SEASON_METADATA_UNKNOWN"
            if not seasons:
                return self._set_record(record_id, "DEFERRED", "SEASON_METADATA_UNKNOWN", data)
            targets = [Target(media_type, source_id, media_id, season) for season in seasons]
        classification_effective = classification.get("effective") if isinstance(classification, dict) else None
        category_id = classification_effective.get("category_id") if isinstance(classification_effective, dict) else None
        save_key = (source.destination_category_bindings.get(category_id)
                    if isinstance(classification, dict) and classification.get("state") == "complete"
                    and isinstance(category_id, str) else None)
        if (not save_key or not source.destination_templates.get(save_key)
                or (media_type == "电影" and save_key != "movie")
                or (media_type == "电视剧" and save_key not in {"tv", "anime"})):
            save_key = None
        states = []
        for target in targets:
            if target.season in deferred:
                states.append(self._link(record_id, target, "DEFERRED", deferred[target.season], data, None)); continue
            if save_key is None:
                states.append(self._link(record_id, target, "DEFERRED", "DESTINATION_CATEGORY_UNBOUND", data, None)); continue
            with self.repository.connection() as db:
                prior = db.execute("SELECT state FROM discovery_targets WHERE record_id=? AND target_key=?",
                                   (record_id, target.key)).fetchone()
            protected = {"STOPPED", "RELEASED"}
            reusable = {"SUBMITTED", "ALREADY_MANAGED", "EXISTING", "INGESTED"}
            if prior and (prior["state"] in protected or
                          (reuse_resolved and prior["state"] in reusable)):
                states.append(prior["state"]); continue
            try:
                state = self._process_target(record_id, target, source, media, year, data, save_key)
            except Exception:
                state = self._link(record_id, target, "DEFERRED", "TARGET_PROCESS_FAILED", data, None)
            states.append(state)
        resolved = {"SUBMITTED", "ALREADY_MANAGED", "EXISTING", "INGESTED"}
        if all(state in resolved for state in states):
            overall = next((state for state in ("SUBMITTED", "ALREADY_MANAGED", "INGESTED", "EXISTING") if state in states), "EXISTING")
        else:
            overall = "PARTIAL" if any(state in resolved for state in states) else states[0] if len(set(states)) == 1 else "PARTIAL"
        reason = ""
        if overall not in resolved:
            with self.repository.connection() as db:
                reasons = [row[0] for row in db.execute("SELECT reason FROM discovery_targets WHERE record_id=? AND reason!=''", (record_id,))]
            reason = reasons[0] if len(set(reasons)) == 1 else overall
        return self._set_record(record_id, overall, reason, data)

    def _process_target(self, record_id, target, source, media, year, data, save_key):
        row, intent, snapshot_digest = None, "", ""
        try:
            inventory = self.inventory(target)
        except Exception:
            return self._link(record_id, target, "DEFERRED", "INVENTORY_FAILED", data, None)
        if not isinstance(inventory, dict) or inventory.get("state") not in {"UNKNOWN", "MISSING", "PRESENT", "PARTIAL", "INGESTED"}:
            return self._link(record_id, target, "DEFERRED", "LIBRARY_STATE_UNKNOWN", data, None)
        if inventory["state"] == "UNKNOWN" and callable(self.inventory_refresh):
            try:
                refreshed = self.inventory_refresh(target, source)
            except Exception:
                return self._link(record_id, target, "DEFERRED", "INVENTORY_REFRESH_FAILED", data, None)
            if isinstance(refreshed, dict) and refreshed.get("state") in {"UNKNOWN", "MISSING", "PRESENT", "PARTIAL", "INGESTED"}:
                inventory = refreshed
            else:
                inventory = {"state": "UNKNOWN"}
        evidence = inventory.get("evidence_ref")
        if inventory["state"] == "UNKNOWN" or not isinstance(evidence, str) or not evidence.strip():
            return self._link(record_id, target, "DEFERRED", "LIBRARY_STATE_UNKNOWN", data, None)
        action = source.existing_media_action or self.config.existing_media_action
        if inventory["state"] in {"PRESENT", "PARTIAL"} and action == "record_only":
            data["archive"] = inventory
            reason = "PARTIAL_RECORD_ONLY" if inventory["state"] == "PARTIAL" else "RECORD_ONLY"
            return self._link(record_id, target, "EXISTING", reason, data, None, receipt_ref=evidence)
        if inventory["state"] == "INGESTED":
            return self._link(record_id, target, "INGESTED", "", data, None, receipt_ref=evidence)
        if not self.authorized(target, source, save_key):
            return self._link(record_id, target, "DEFERRED", "SCOPE_NOT_AUTHORIZED", data, None)
        if self.excluded(target):
            return self._link(record_id, target, "REJECTED", "EXCLUDED", data, None)
        if not self.current() or not self._owned(source.id):
            return self._link(record_id, target, "DEFERRED", "STALE_GENERATION", data, None)
        snapshot = {"name": self._field(media, "title"), "year": year,
                    "username": "subscriBetter discovery", "save_path": source.destination_templates.get(save_key),
                    "media_category_id": source.media_category_id}
        snapshot = {key: value for key, value in snapshot.items() if value not in {None, ""}}
        snapshot_digest = _digest(snapshot)
        intent = "discovery:" + _digest([target.key, snapshot_digest])
        existing = self.repository.by_target(target)
        try:
            row = self.owner.submit(intent, target, snapshot, "discovery")
        except Exception:
            return self._link(record_id, target, "DEFERRED", "HANDOFF_FAILED", data, None, intent, snapshot_digest)
        state = ("STOPPED" if row["state"] == "STOPPED" else "RELEASED" if row["state"] == "RELEASED_NATIVE"
                 else "ALREADY_MANAGED" if existing and row["state"] == "ACTIVE"
                 else "SUBMITTED" if row["state"] == "ACTIVE" and row.get("native_id") else "DEFERRED")
        reason = "" if state in {"SUBMITTED", "ALREADY_MANAGED"} else "HANDOFF_" + row["state"]
        if state in {"SUBMITTED", "ALREADY_MANAGED"} and callable(self.accepted):
            try:
                self.accepted(row, target, source, snapshot)
            except Exception as error:
                state = "DEFERRED"
                reason = str(error) if isinstance(error, ValueError) and str(error) in {"TV_SCOPE_UNBOUND", "TV_SCOPE_INVALID"} else "SCHEDULE_SCOPE_FAILED"
        return self._link(record_id, target, state, reason, data, row, intent, snapshot_digest)

    def _link(self, record_id, target, state, reason, data, row, intent="", snapshot_digest="", receipt_ref=None):
        task_id = row.get("id") if row and self.repository.get_task(row.get("id")) else None
        with self.repository.connection(write=True) as db:
            db.execute("INSERT INTO discovery_targets(record_id,target_key,intent_key,snapshot_digest,task_id,season,episode_group,state,reason,receipt_ref) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(record_id,target_key) DO UPDATE SET intent_key=CASE WHEN excluded.intent_key!='' THEN excluded.intent_key ELSE discovery_targets.intent_key END,snapshot_digest=CASE WHEN excluded.snapshot_digest!='' THEN excluded.snapshot_digest ELSE discovery_targets.snapshot_digest END,task_id=COALESCE(excluded.task_id,discovery_targets.task_id),state=excluded.state,reason=excluded.reason,receipt_ref=COALESCE(excluded.receipt_ref,discovery_targets.receipt_ref)",
                       (record_id, target.key, intent, snapshot_digest, task_id, target.season,
                        target.episode_group, state, reason, receipt_ref or (f"task:{row['id']}" if row and state in {"SUBMITTED", "ALREADY_MANAGED"} else None)))
        return state

    def records(self, *, limit=100, offset=0, state=None, source_id=None, view="all"):
        if not 1 <= limit <= 500 or offset < 0: raise ValueError("INVALID_PAGINATION")
        if view not in {"all", "latest12", "recognized", "unrecognized"}: raise ValueError("INVALID_HISTORY_VIEW")
        if view == "latest12": limit = min(limit, 12)
        clauses, args = ["visible=1"], []
        if state: clauses.append("state=?"); args.append(state)
        if source_id: clauses.append("source_id=?"); args.append(source_id)
        if view == "recognized": clauses.append("json_type(data,'$.identity')='object'")
        elif view == "unrecognized": clauses.append("json_type(data,'$.identity') IS NULL")
        with self.repository.connection() as db:
            rows = db.execute(f"SELECT * FROM discovery_records WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            result = []
            for row in rows:
                value = dict(row); value["raw"] = json.loads(value["raw"]); value.update(json.loads(value.pop("data")))
                value["targets"] = [dict(target) for target in db.execute("SELECT * FROM discovery_targets WHERE record_id=? ORDER BY season,target_key", (row["id"],))]
                result.append(value)
            return result

    def statistics(self, *, source_id=None):
        """Operational cohort includes hidden history; stage evidence is distinct."""
        cohort="(? IS NULL OR source_id=?)"
        args=(source_id,source_id)
        with self.repository.connection() as db:
            records={r[0]:r[1] for r in db.execute(f"SELECT state,count(*) FROM discovery_records WHERE {cohort} GROUP BY state",args)}
            recognized=db.execute(f"SELECT count(*) FROM discovery_records WHERE {cohort} AND json_type(data,'$.identity')='object'",args).fetchone()[0]
            window=db.execute(f"SELECT min(first_seen),max(last_seen),coalesce(max(id),0) FROM discovery_records WHERE {cohort}",args).fetchone()
            revisions=[r[0] for r in db.execute(f"SELECT DISTINCT filter_revision FROM discovery_records WHERE {cohort} ORDER BY filter_revision LIMIT 101",args)]
            cte=f"WITH cohort AS (SELECT * FROM discovery_records WHERE {cohort}), target AS (SELECT dt.* FROM discovery_targets dt JOIN cohort c ON c.id=dt.record_id) "
            targets={r[0]:r[1] for r in db.execute(cte+"SELECT state,count(*) FROM target GROUP BY state",args)}
            ack=sum(targets.get(k,0) for k in ('SUBMITTED','ALREADY_MANAGED'))
            download=db.execute(cte+"""SELECT count(*) FROM target dt WHERE EXISTS(
                SELECT 1 FROM plans p JOIN plan_actions a ON a.plan_id=p.id
                JOIN managed_downloads m ON m.downloader=json_extract(p.snapshot,'$.downloader')
                  AND m.infohash=json_extract(p.snapshot,'$.infohash') AND m.save_path=json_extract(p.snapshot,'$.save_path')
                WHERE p.task_id=dt.task_id AND a.kind='ADD' AND a.state='SUCCEEDED'
                  AND m.add_action=a.plan_id AND m.client_id IS NOT NULL AND m.state NOT IN ('ADD_INTENT','UNKNOWN')
                  AND EXISTS(SELECT 1 FROM action_receipts r WHERE r.action_id=a.id AND r.outcome='SUCCEEDED')
                  AND EXISTS(SELECT 1 FROM json_each(a.targets) t WHERE json_array(
                    json_extract(t.key,'$[0]'),json_extract(t.key,'$[1]'),json_extract(t.key,'$[2]'),
                    json_extract(t.key,'$[3]'),json_extract(t.key,'$[4]'))=dt.target_key))""",args).fetchone()[0]
            delivered=db.execute(cte+"""SELECT count(*) FROM target dt WHERE EXISTS(
                SELECT 1 FROM plans p JOIN plan_actions a ON a.plan_id=p.id
                WHERE p.task_id=dt.task_id AND a.kind='PUBLISH' AND a.state IN ('HANDED_OFF','INGEST_CONFIRMED')
                  AND EXISTS(SELECT 1 FROM action_receipts r WHERE r.action_id=a.id AND r.outcome IN ('HANDED_OFF','INGEST_CONFIRMED'))
                  AND EXISTS(SELECT 1 FROM json_each(a.targets) t WHERE json_array(
                    json_extract(t.key,'$[0]'),json_extract(t.key,'$[1]'),json_extract(t.key,'$[2]'),
                    json_extract(t.key,'$[3]'),json_extract(t.key,'$[4]'))=dt.target_key))""",args).fetchone()[0]
            # Historical units survive reviewed scope reductions. Quantify the current
            # declared scope, including missing units, rather than all retained rows.
            ingested=db.execute(cte+""", required AS (
                SELECT dt.*,coalesce(
                    (SELECT json_extract(value,'$.scope.units') FROM settings WHERE key='runtime-task:'||dt.task_id),
                    (SELECT scope FROM task_lifecycle WHERE task_id=dt.task_id),
                    (SELECT scope FROM opportunities WHERE task_id=dt.task_id ORDER BY created_at DESC,id DESC LIMIT 1)
                ) AS required_scope FROM target dt)
                SELECT count(*) FROM required dt WHERE json_array_length(required_scope)>0
                  AND NOT EXISTS(SELECT 1 FROM json_each(dt.required_scope) unit
                    WHERE NOT EXISTS(SELECT 1 FROM target_units tu JOIN ingest_receipts i
                      ON i.target_key=tu.target_key AND i.generation=tu.generation
                      WHERE tu.task_id=dt.task_id AND tu.target_key=unit.value))""",args).fetchone()[0]
        record_total=sum(records.values());target_total=sum(targets.values())
        stages={name:dict(numerator=n,denominator=d,eligible_denominator=e) for name,n,d,e in (
            ('recognition',recognized,record_total,record_total),('intent_ack',ack,target_total,target_total),
            ('download_acceptance',download,target_total,ack),('delivery_completion',delivered,target_total,download),
            ('ingest',ingested,target_total,delivered))}
        return dict(records=records,targets=targets,record_denominator=record_total,target_denominator=target_total,stages=stages,
            cohort=dict(source_id=source_id,first_seen=window[0],last_seen=window[1],high_watermark=str(window[2]),
                        filter_revisions=revisions[:100],revisions_truncated=len(revisions)>100,includes_hidden=True))

    def cleanup(self, record_ids):
        ids = sorted(set(record_ids))
        if not ids or len(ids) > 500 or any(type(x) is not int or x <= 0 for x in ids): raise ValueError("INVALID_RECORD_IDS")
        with self.repository.connection(write=True) as db:
            placeholders = ",".join("?" for _ in ids)
            cursor = db.execute(f"UPDATE discovery_records SET visible=0 WHERE id IN ({placeholders})", ids)
            db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(NULL,?,?,?)", ("DISCOVERY_HISTORY_CLEANUP:" + _digest(ids), "admin", utcnow()))
            return cursor.rowcount

    def reprocess(self, record_ids):
        ids = sorted(set(record_ids))
        if not ids or len(ids) > 100 or any(type(x) is not int or x <= 0 for x in ids): raise ValueError("INVALID_RECORD_IDS")
        changed = 0
        with self.repository.connection(write=True) as db:
            for record_id in ids:
                blocked = db.execute("SELECT 1 FROM discovery_targets WHERE record_id=? AND state IN ('STOPPED','RELEASED')", (record_id,)).fetchone()
                if blocked: continue
                cursor = db.execute("UPDATE discovery_records SET visible=1,state='DEFERRED',reason='REPROCESS_REQUESTED',retry_count=0,next_due=0 WHERE id=?", (record_id,))
                changed += cursor.rowcount
            db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(NULL,?,?,?)", ("DISCOVERY_REPROCESS:" + _digest(ids), "admin", utcnow()))
        return changed
