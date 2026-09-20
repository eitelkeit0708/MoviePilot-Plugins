"""W09 unit evidence: bounded work discovery feeding the existing ownership intent path."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins.v3/subscribetter"


def load_modules():
    package = types.ModuleType("w09_subscribetter")
    package.__path__ = [str(PLUGIN)]
    sys.modules[package.__name__] = package
    loaded = []
    for name in ("repository", "ownership", "discovery"):
        spec = importlib.util.spec_from_file_location(f"w09_subscribetter.{name}", PLUGIN / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


class Clock:
    def __init__(self, value=1_800_000_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Correction:
    def __init__(self, meta, status="OK", reasons=()):
        self.meta, self.status, self.reasons = meta, status, reasons

    def record(self):
        return {"status": self.status, "reasons": list(self.reasons), "revision": "meta-r1"}


class Meta:
    def __init__(self, title, season=None):
        self.title = title
        self.cn_name = title
        self.en_name = None
        self.year = None
        self.begin_season = season
        self.type = None


class MetaService:
    def parse(self, key, title, **_):
        return Correction(Meta(title))


class Recognizer:
    def __init__(self, media=None):
        self.media = media
        self.calls = []

    def recognize(self, meta, declared, *, media_type=None):
        self.calls.append((declared, media_type, getattr(meta, "year", None)))
        return self.media

    @staticmethod
    def identity(media):
        return media.identity

    @staticmethod
    def classify(media):
        return {"state": "classified", "effective": getattr(media, "category", None), "policy_revision": 7}


class Owner:
    def __init__(self, states=None):
        self.states = iter(states or ())
        self.calls = []

    def submit(self, intent_key, target, snapshot, actor):
        self.calls.append((intent_key, target, snapshot, actor))
        state = next(self.states, "ACTIVE")
        return {"id": len(self.calls), "state": state, "native_id": 40 + len(self.calls),
                "generation": 1, "snapshot": snapshot}


class Host:
    def __init__(self):
        self.rows, self.creates = {}, 0

    def find(self, target):
        return [row for row in self.rows.values() if row["media_source"] == target.media_source and row["media_id"] == target.media_id]

    def create(self, target, snapshot):
        self.creates += 1
        sid = 100 + self.creates
        self.rows[sid] = {"id": sid, "type": target.media_type, "media_source": target.media_source,
                          "media_id": target.media_id, "season": target.season,
                          "episode_group": target.episode_group, "state": "S", "name": snapshot.get("name")}
        return sid

    def get(self, sid):
        return self.rows.get(sid)

    def pause(self, sid):
        self.rows[sid]["state"] = "S"

    def set_state(self, sid, state):
        self.rows[sid]["state"] = state


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_mod, cls.ownership_mod, cls.d = load_modules()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.repo_mod.Repository(Path(self.tmp.name) / "state.sqlite3")
        self.clock = Clock()

    def config(self, **changes):
        source = dict(id="weekly", kind="rsshub", route_key="movie_weekly_best")
        data = dict(enabled=True, rsshub_base_url="http://rss.internal:1200/proxy/rsshub",
                    sources=[source], request_budget={"items": 10, "response_bytes": 16384,
                    "interval_min_seconds": 10, "interval_max_seconds": 10})
        data.update(changes)
        return self.d.DiscoveryConfig.model_validate(data)

    def service(self, config=None, *, fetch=None, media=None, owner=None, inventory=None,
                authorized=None, excluded=None, inventory_refresh=None, recognizer=None,
                meta_service=None, accepted=None):
        return self.d.DiscoveryService(
            self.repo, owner or Owner(), meta_service or MetaService(), recognizer or Recognizer(media), config or self.config(),
            fetch=fetch or (lambda *_: self.d.FetchResult(SYNTHETIC_RSS)), clock=self.clock,
            inventory=inventory or (lambda _target: {"state": "MISSING", "evidence_ref": "fixture:missing"}),
            inventory_refresh=inventory_refresh,
            authorized=authorized or (lambda *_: True), excluded=excluded or (lambda _target: False),
            current=lambda: True, owner_check=owner_receipt, accepted=accepted,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")

    def test_catalog_basepath_and_no_public_fallback(self):
        self.assertEqual(13, len(self.d.ROUTES))
        config = self.config()
        source = config.sources[0]
        self.assertEqual("http://rss.internal:1200/proxy/rsshub/douban/list/movie_weekly_best?limit=10",
                         self.d.source_url(config, source))
        for bad in ("https://user:pass@rss.local", "https://rss.local/base?x=1",
                    "https://rss.local/base#frag", "https://rss.local/%2e%2e/admin", "//rss.local"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, ValidationError)):
                self.config(rsshub_base_url=bad)
        with self.assertRaises(ValueError):
            self.d.source_url(self.d.DiscoveryConfig(enabled=True, sources=[source]), source)
        self.assertNotIn("rsshub.app", repr(self.d.ROUTES))

    def test_proposed_custom_source_test_is_fetch_only_and_catalog_lists_custom(self):
        config = self.config(sources=[{"id": "custom", "kind": "custom", "url": "https://feed.invalid/rss?limit=2"}])
        service = self.service(config, fetch=lambda *_: self.d.FetchResult(b"<rss><channel/></rss>"))
        catalog = service.catalog()
        custom = next(row for row in catalog if row.get("configured_id") == "custom")
        self.assertEqual("https://feed.invalid/rss?limit=2", custom["full_url"])
        proposed = self.d.SourceConfig(id="preview", kind="custom", url="https://preview.invalid/rss")
        tested = service.test_source(proposed=proposed)
        self.assertEqual("preview", tested["source_id"])
        self.assertTrue(tested["fetch_only"])

    def test_legacy_source_syntaxes_and_conflicts_are_explicit(self):
        cases = {
            "https://feed.local/a@@TV": ("TV", None, None),
            "https://feed.local/a;/media": (None, "/media", "/media"),
            "https://feed.local/a;/movie#/tv#/anime": (None, "/movie", "/tv"),
            "https://feed.local/a;;@movies@": (None, "", ""),
            "https://feed.local/a;/tv;@tv@": (None, "/tv", "/tv"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                imported = self.d.import_legacy({"rss_addrs": raw})
                source = imported["sources"][0]
                self.assertEqual(raw, source["legacy_original_text"])
                self.assertEqual(expected[0], source.get("source_type_hint"))
                self.assertEqual(expected[1], source.get("destination_templates", {}).get("movie"))
                self.assertEqual(expected[2], source.get("destination_templates", {}).get("tv"))
                self.assertEqual([], imported["diagnostics"])
        for raw in ("https://feed.local/a@@TV@@Movie", "https://feed.local/a;;@tv@;extra",
                    "https://feed.local/a@@TV;;@movies@"):
            result = self.d.import_legacy({"rss_addrs": raw})
            self.assertEqual([], result["sources"])
            self.assertTrue(result["diagnostics"])
        ranks = self.d.import_legacy({"ranks": ["movie-weekly", "movie-top250"]})
        self.assertEqual("movie_weekly_best", ranks["sources"][0]["route_key"])
        self.assertIn("LEGACY_RANK_RESELECT_REQUIRED:movie-top250", ranks["diagnostics"])
        self.assertFalse(ranks["actions"]["run_once"])
        migrated = self.d.import_legacy({"rss_addrs": "https://feed.local/a", "proxy": True,
                                         "sleep_time": 4, "is_exit_ip_rate_limit": True})
        self.assertTrue(migrated["sources"][0]["proxy"])
        self.assertEqual(4, migrated["config"]["request_budget"]["interval_min_seconds"])

    def test_parser_is_bounded_entity_safe_and_keeps_claims_untrusted(self):
        parsed = self.d.parse_rss(SYNTHETIC_RSS, max_bytes=16384, max_items=2, max_text=2000, max_depth=12)
        self.assertEqual(2, len(parsed))
        self.assertEqual("24", parsed[0].title)
        self.assertEqual("35322132", parsed[0].douban_subject_id)
        self.assertIsNone(parsed[0].rating)
        self.assertIsNone(parsed[0].year)
        self.assertIsNone(parsed[1].douban_subject_id)
        self.assertEqual([], self.d.parse_rss(b"<rss><channel/></rss>", max_bytes=1000))
        attacks = [b"<rss><channel>", b"<!DOCTYPE rss><rss/>",
                   "<?xml version='1.0' encoding='utf-16'?><!DOCTYPE rss><rss/>".encode("utf-16")]
        for body in attacks:
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                self.d.parse_rss(body, max_bytes=16384)
        with self.assertRaises(ValueError):
            self.d.parse_rss(SYNTHETIC_RSS, max_bytes=40)

    def test_host_fetcher_enforces_total_deadline_identity_encoding_and_close(self):
        clock = Clock(0)
        class Response:
            status_code = 200
            headers = {"Content-Encoding": "identity"}
            closed = False
            def iter_content(self, _):
                yield b"<rss>"
                clock.advance(6)
                yield b"</rss>"
            def close(self): self.closed = True
        response = Response()
        request = types.SimpleNamespace(call=None)
        class Requests:
            def __init__(self, **kwargs): request.init = kwargs
            def request(self, **kwargs): request.call = kwargs; return response
        class Security:
            @staticmethod
            def evaluate_url_safety(url, allowed_domains, strict=False, block_private=False,
                                    allowed_private_ranges=None):
                request.security = (url, allowed_domains, strict, block_private, allowed_private_ranges)
                return types.SimpleNamespace(allowed=url == "https://feed.invalid/rss"
                                              and allowed_domains == ["feed.invalid"]
                                              and strict and block_private)
        modules = {
            "app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
            "app.sdk.network": types.ModuleType("app.sdk.network"),
            "app.sdk.config": types.ModuleType("app.sdk.config")}
        modules["app.sdk.network"].RequestUtils = Requests
        modules["app.sdk.network"].SecurityUtils = Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid/rss")
        budget = self.d.RequestBudget(timeout=5.0, response_bytes=1024, items=1)
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
            self.d.HostRSSFetcher(source.url, clock=clock)(source.url, source, budget)
        self.assertEqual("identity", request.call["headers"]["Accept-Encoding"])
        self.assertFalse(request.call["allow_redirects"])
        self.assertTrue(response.closed)
        self.assertEqual(("https://feed.invalid/rss", ["feed.invalid"], True, True, None), request.security)

    def test_host_fetcher_exactly_allows_configured_private_ip(self):
        captured = []
        class Security:
            @staticmethod
            def evaluate_url_safety(url, allowed_domains, strict=False, block_private=False,
                                    allowed_private_ranges=None):
                captured.append((allowed_domains, strict, block_private, allowed_private_ranges))
                return types.SimpleNamespace(allowed=True)
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network")}
        modules["app.sdk.network"].SecurityUtils = Security
        fetcher = self.d.HostRSSFetcher("http://192.168.50.6:1200/proxy/rsshub")
        with patch.dict(sys.modules, modules):
            fetcher._safe("http://192.168.50.6:1200/proxy/rsshub/douban/list/movie_weekly_best",
                          "http://192.168.50.6:1200/proxy/rsshub")
        self.assertEqual([(["192.168.50.6:1200"], True, True, ["192.168.50.6/32"])], captured)

    def test_source_failures_are_isolated_and_retry_after_persists_by_origin(self):
        config = self.config(sources=[
            {"id": "a", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "b", "kind": "custom", "url": "https://other.invalid/feed"},
            {"id": "c", "kind": "rsshub", "route_key": "movie_showing"},
        ])
        calls = []
        def fetch(url, *_):
            calls.append(url)
            if "weekly" in url:
                raise self.d.FetchError("RATE_LIMITED", retry_after=120)
            return self.d.FetchResult(b"<rss><channel/></rss>")
        result = self.service(config, fetch=fetch).run()
        self.assertEqual("FAILED", result["sources"]["a"]["state"])
        self.assertEqual("SUCCESS", result["sources"]["b"]["state"])
        self.assertEqual("RATELIMITED", result["sources"]["c"]["state"])
        self.assertEqual(2, len(calls))
        restarted = self.service(config, fetch=fetch).run(["c"])
        self.assertEqual("RATELIMITED", restarted["sources"]["c"]["state"])
        self.clock.advance(121)
        self.assertEqual("SUCCESS", self.service(config, fetch=fetch).run(["c"])["sources"]["c"]["state"])

    def test_retry_limit_stops_repeated_fetch_until_explicit_action(self):
        config = self.config(request_budget={"items": 1, "response_bytes": 16384,
                                             "interval_min_seconds": 1, "interval_max_seconds": 1,
                                             "retry_limit": 1})
        calls = []
        def fetch(*_):
            calls.append(1)
            raise self.d.FetchError("NETWORK_ERROR")
        service = self.service(config, fetch=fetch)
        self.assertEqual("FAILED", service.run()["sources"]["weekly"]["state"])
        self.clock.advance(2)
        self.assertEqual("FAILED", service.run()["sources"]["weekly"]["state"])
        self.clock.advance(2)
        third = service.run()["sources"]["weekly"]
        self.assertEqual({"state": "DEFERRED", "reason": "RETRY_EXHAUSTED"}, third)
        self.assertEqual(2, len(calls))
        self.assertEqual(1, service.retry_sources(["weekly"]))
        self.assertEqual("FAILED", service.run()["sources"]["weekly"]["state"])
        self.assertEqual(3, len(calls))

    def test_record_retry_is_bounded_and_policy_revision_reconsiders(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "42"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        config = self.config(minimum_rating=7.0, request_budget={"items": 1, "response_bytes": 16384,
                             "interval_min_seconds": 1, "interval_max_seconds": 1, "retry_limit": 1})
        service = self.service(config, media=media)
        service.run()
        self.assertEqual(1, service.records()[0]["retry_count"])
        self.clock.advance(2)
        service.run()
        self.assertEqual(2, service.records()[0]["retry_count"])
        self.clock.advance(2)
        service.run()
        self.assertEqual(2, service.records()[0]["retry_count"])
        changed = self.service(config.model_copy(update={"minimum_rating": None}), media=media)
        self.clock.advance(2)
        changed.run()
        self.assertEqual("SUBMITTED", changed.records()[0]["state"])
        revision = changed.records()[0]["filter_revision"]
        self.clock.advance(2)
        changed.run()
        self.assertEqual(revision, changed.records()[0]["filter_revision"])
        self.assertEqual("SUBMITTED", changed.records()[0]["state"])

    def test_provider_rating_known_seasons_and_partial_receipts(self):
        media = types.SimpleNamespace(
            identity=("themoviedb", "1396"), type=types.SimpleNamespace(value="电视剧"),
            title="Fixture Series", year="2008", category="tv",
            tmdb_info={"vote_average": 8.951, "seasons": [
                {"season_number": 0, "episode_count": 2, "air_date": "2008-01-01"},
                {"season_number": 1, "episode_count": 7, "air_date": "2008-01-20"},
                {"season_number": 3, "episode_count": 4, "air_date": "2010-01-01"},
                {"season_number": 5, "episode_count": 8, "air_date": "2999-01-01"},
            ]})
        owner = Owner(["ACTIVE", "PENDING"])
        config = self.config(minimum_rating=8.0, rating_source="recognized_provider",
                             season_scope="all_known", media_type_allowlist=["电视剧"],
                             request_budget=ONE_BUDGET, sources=[
                                 {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}])
        result = self.service(config, media=media, owner=owner).run()
        records = self.service(config).records()
        self.assertEqual("PARTIAL", result["sources"]["weekly"]["state"])
        self.assertEqual([1, 3], [call[1].season for call in owner.calls])
        self.assertEqual(["SUBMITTED", "DEFERRED", "DEFERRED"], [row["state"] for row in records[0]["targets"]])
        self.assertEqual({"provider": "themoviedb", "field": "tmdb_info.vote_average", "value": 8.951},
                         {key: records[0]["rating"][key] for key in ("provider", "field", "value")})
        self.assertTrue(records[0]["rating"]["evidence_ref"].startswith("provider-payload:"))
        self.assertNotIn(0, [call[1].season for call in owner.calls])
        self.assertIn(5, [row["season"] for row in records[0]["targets"]])
        future = next(row for row in records[0]["targets"] if row["season"] == 5)
        self.assertEqual(("DEFERRED", "SEASON_NOT_AIRED"), (future["state"], future["reason"]))

    def test_unknown_score_type_conflict_and_inventory_uncertainty_defer(self):
        base = dict(identity=("themoviedb", "42"), title="24", year="2001", category="tv",
                    tmdb_info={"seasons": [{"season_number": 1, "episode_count": 24, "air_date": "2001-01-01"}]})
        for label, media, config, reason in (
            ("score", types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), **base),
             self.config(minimum_rating=7.0, season_scope="all_known", request_budget=ONE_BUDGET,
                         sources=[{"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), "RATING_UNKNOWN"),
            ("type", types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), **base),
             self.config(media_type_allowlist=["电影"], season_scope="all_known", request_budget=ONE_BUDGET,
                         sources=[{"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), "TYPE_NOT_ALLOWED"),
        ):
            with self.subTest(label=label):
                service = self.service(config, media=media)
                service.run()
                record = service.records()[0]
                self.assertEqual("DEFERRED" if label == "score" else "REJECTED", record["state"])
                self.assertEqual(reason, record["reason"])
                self.clock.advance(11)
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "99"),
                                      title="Deadpool 2", year="2018", category="movie", tmdb_info={})
        service = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media,
                               inventory=lambda _: {"state": "UNKNOWN", "evidence_ref": None})
        service.run()
        self.assertEqual("LIBRARY_STATE_UNKNOWN", service.records()[0]["reason"])

    def test_existing_record_only_stopped_precedence_cleanup_and_reprocess(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "7"),
                                      title="Fixture", year="2026", category="movie",
                                      tmdb_info={"vote_average": 8.0})
        service = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media,
                               inventory=lambda _: {"state": "PRESENT", "evidence_ref": "archive:v1"})
        service.run()
        record = service.records()[0]
        self.assertEqual("EXISTING", record["state"])
        self.assertEqual("EXISTING", record["targets"][0]["state"])
        self.assertEqual("archive:v1", record["targets"][0]["receipt_ref"])
        ids = [row["id"] for row in service.records()]
        self.assertEqual(1, service.cleanup(ids))
        self.assertEqual([], service.records())
        self.assertEqual(1, service.reprocess(ids))
        self.assertEqual("DEFERRED", service.records()[0]["state"])
        self.assertEqual("REPROCESS_REQUESTED", service.records()[0]["reason"])
        self.assertEqual({"recognition", "intent_ack", "download_acceptance", "delivery_completion", "ingest"},
                         set(service.statistics()["stages"]))

    def test_partial_tv_archive_is_linked_without_submission_in_record_only_mode(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("themoviedb", "1396"),
                                      title="Fixture", year="2008", category="tv",
                                      tmdb_info={"seasons": [{"season_number": 1, "air_date": "2008-01-01"}]})
        owner = Owner()
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), media=media, owner=owner,
                               inventory=lambda _: {"state": "PARTIAL", "evidence_ref": "archive-season:test"},
                               authorized=lambda *_: True)
        service.run()
        record = service.records()[0]
        self.assertEqual("EXISTING", record["state"])
        self.assertEqual("PARTIAL_RECORD_ONLY", record["targets"][0]["reason"])
        self.assertEqual("archive-season:test", record["targets"][0]["receipt_ref"])
        self.assertEqual([], owner.calls)

    def test_unknown_inventory_uses_bounded_refresh_seam_before_deferring(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "253774"),
                                      title="Caminandes: Gran Dillama", year="2013", category="movie", tmdb_info={})
        probes = []
        def refresh(target, source):
            probes.append((target.key, source.id))
            return {"state": "MISSING", "evidence_ref": "archive-probe:fixture", "diagnostics": []}
        owner = Owner()
        service = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET),
                               media=media, owner=owner,
                               inventory=lambda _: {"state": "UNKNOWN", "evidence_ref": None},
                               inventory_refresh=refresh)
        service.run()
        self.assertEqual([('["电影","themoviedb","253774",null,""]', "weekly")], probes)
        self.assertEqual("SUBMITTED", service.records()[0]["state"])

    def test_history_views_separate_identity_evidence(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "8"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        recognized = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media)
        recognized.run()
        other = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                            sources=[{"id": "other", "kind": "custom", "url": "https://other.invalid/rss"}])
        self.service(other, media=None).run()
        self.assertEqual(1, len(recognized.records(view="recognized")))
        self.assertEqual(1, len(recognized.records(view="unrecognized")))
        self.assertLessEqual(len(recognized.records(view="latest12", limit=100)), 12)

    def test_owner_receipt_is_exact_and_unbound_owner_cannot_submit(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "8"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        service = self.d.DiscoveryService(
            self.repo, Owner(), MetaService(), Recognizer(media), self.config(media_type_allowlist=["电影"]),
            fetch=lambda *_: self.d.FetchResult(SYNTHETIC_RSS), clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref": "fixture"}, authorized=lambda *_: True,
            excluded=lambda _: False, current=lambda: True, owner_check=lambda *_: None,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")
        result = service.run()
        self.assertEqual("OWNER_UNBOUND", result["sources"]["weekly"]["reason"])
        self.assertEqual([], service.owner.calls)

    def test_cross_source_dedup_stopped_precedence_and_common_exclusion_gate(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "700"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET, sources=[
            {"id": "one", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "two", "kind": "rsshub", "route_key": "movie_showing"},
        ])
        service = self.d.DiscoveryService(
            self.repo, owner, MetaService(), Recognizer(media), config,
            fetch=lambda *_: self.d.FetchResult(SYNTHETIC_RSS), clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref": "fixture"}, authorized=lambda *_: True,
            excluded=lambda _: False, current=lambda: True, owner_check=owner_receipt,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")
        service.run()
        self.clock.advance(10)
        service.run()
        self.assertEqual(1, host.creates)
        self.assertEqual(1, len(self.repo.list_tasks()))
        self.assertEqual({"SUBMITTED", "ALREADY_MANAGED"}, {row["targets"][0]["state"] for row in service.records()})
        task = self.repo.list_tasks()[0]
        self.repo.set_state(task["id"], "STOPPED", "admin")
        self.clock.advance(11)
        config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                             sources=[{"id": "three", "kind": "rsshub", "route_key": "movie_real_time_hotest",
                                       "destination_templates": {"movie": "/changed"}}])
        stopped = self.d.DiscoveryService(
            self.repo, owner, MetaService(), Recognizer(media), config,
            fetch=lambda *_: self.d.FetchResult(SYNTHETIC_RSS), clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref":"fixture"}, authorized=lambda *_: True, excluded=lambda _: False,
            current=lambda: True, owner_check=owner_receipt, owner_snapshot=lambda *_: OWNER_SNAPSHOT,
            instance_id="SubscriBetter")
        stopped.run()
        self.assertEqual("STOPPED", stopped.records(source_id="three")[0]["targets"][0]["state"])
        self.assertEqual(1, host.creates)
        target = self.repo_mod.Target("电影", "themoviedb", "blocked")
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO exclusions VALUES(?,?,?,NULL,1)",
                       ("deny-manual", '{"targets":["' + target.key.replace('"', '\\"') + '"]}', "fixture"))
        with self.assertRaisesRegex(ValueError, "EXCLUDED"):
            owner.submit("manual-blocked", target, {"name": "Blocked"}, "admin")
        tv = self.repo_mod.Target("电视剧", "themoviedb", "blocked-tv", 1)
        unit = '["电视剧","themoviedb","blocked-tv",1,"",7]'
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO exclusions VALUES(?,?,?,NULL,1)",
                       ("deny-tv-unit", json.dumps({"targets": [unit]}), "fixture"))
        with self.assertRaisesRegex(ValueError, "EXCLUDED"):
            owner.submit("manual-tv-blocked", tv, {"name": "Blocked TV"}, "admin")

    def test_legacy_history_is_raw_and_never_promoted_to_submitted(self):
        imported = self.d.import_legacy({}, [{"title": "旧片", "year": "2020", "tmdbid": "0",
                                              "status": "已添加订阅", "time_full": "2020-01-02 03:04:05"}])
        self.assertEqual("LEGACY_UNVERIFIED", imported["history"][0]["state"])
        self.assertIsNone(imported["history"][0]["identity"])
        self.assertEqual("旧片", imported["history"][0]["raw"]["title"])
        self.assertEqual(["TIMEZONE_UNKNOWN"], imported["history"][0]["diagnostics"])

    def test_schema8_to_9_keeps_tasks_and_adds_empty_discovery_relations(self):
        target = self.repo_mod.Target("电影", "themoviedb", "schema-fixture")
        task = self.repo.submit("schema-fixture", target, {"name": "Fixture"}, "admin")
        with self.repo.connection(write=True) as db:
            for table in ("discovery_targets", "discovery_records", "discovery_sources"):
                db.execute("DROP TABLE " + table)
            db.execute("DROP INDEX archive_target_identity")
            db.execute("PRAGMA user_version=8")
        migrated = self.repo_mod.Repository(self.repo.path)
        self.assertEqual(task["id"], migrated.get_task(task["id"])["id"])
        db = sqlite3.connect(self.repo.path)
        try:
            self.assertEqual(9, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM discovery_records").fetchone()[0])
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='archive_target_identity'").fetchone())
        finally:
            db.close()

    def test_custom_url_secret_rejection_and_encoded_delimiter_preservation(self):
        with self.assertRaises((ValueError, ValidationError)):
            self.d.DiscoveryConfig(enabled=True, sources=[{"id": "secret", "kind": "custom",
                                                           "url": "https://feed.invalid/rss?api_token=plain"}])
        imported = self.d.import_legacy({"rss_addrs": "https://feed.invalid/a%3Bpart@@TV"})
        self.assertEqual("https://feed.invalid/a%3Bpart", imported["sources"][0]["url"])
        self.assertEqual("TV", imported["sources"][0]["source_type_hint"])

    def test_fix1_inventory_is_fail_closed_for_error_and_empty_evidence(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "42"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        for source_id, inventory in (("error", {"state": "ERROR", "evidence_ref": "archive:error"}),
                                     ("empty", {"state": "MISSING", "evidence_ref": ""})):
            with self.subTest(source_id=source_id):
                owner = Owner()
                config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                                     sources=[{"id": source_id, "kind": "custom", "url": f"https://{source_id}.invalid/rss"}])
                service = self.service(config, media=media, owner=owner, inventory=lambda _, value=inventory: value,
                                       inventory_refresh=lambda *_: {"state":"MISSING", "evidence_ref":"should-not-rescue-error"})
                service.run()
                self.assertEqual("LIBRARY_STATE_UNKNOWN", service.records(source_id=source_id)[0]["reason"])
                self.assertEqual([], owner.calls)

    def test_fix1_shared_origin_spacing_rotates_without_starvation(self):
        config = self.config(sources=[
            {"id": "a", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "b", "kind": "rsshub", "route_key": "movie_showing"}], request_budget=ONE_BUDGET)
        calls = []
        service = self.service(config, fetch=lambda url, *_: (calls.append((url, self.clock())),
                                                               self.d.FetchResult(b"<rss><channel/></rss>"))[1])
        first = service.run()
        self.assertEqual(("SUCCESS", "RATELIMITED"),
                         (first["sources"]["a"]["state"], first["sources"]["b"]["state"]))
        self.clock.advance(10)
        second = service.run()
        self.assertEqual(("RATELIMITED", "SUCCESS"),
                         (second["sources"]["a"]["state"], second["sources"]["b"]["state"]))
        self.assertEqual([0, 10], [int(at - 1_800_000_000) for _, at in calls])

    def test_fix1_total_deadline_covers_redirect_and_delayed_empty_response(self):
        clock, timeouts = Clock(0), []
        class Response:
            def __init__(self, status, headers): self.status_code, self.headers, self.closed = status, headers, False
            def iter_content(self, _): return iter(())
            def close(self): self.closed = True
        responses = [Response(302, {"Location": "/final"}), Response(200, {"Content-Encoding": "identity"})]
        class Requests:
            def __init__(self, **kwargs): timeouts.append(kwargs["timeout"])
            def request(self, **_): clock.advance(3); return responses.pop(0)
        class Security:
            @staticmethod
            def evaluate_url_safety(url, allowed_domains, **kwargs):
                return types.SimpleNamespace(allowed=allowed_domains == ["feed.invalid:8443"] and
                                              urlsplit(url).netloc == "feed.invalid:8443")
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network"),
                   "app.sdk.config": types.ModuleType("app.sdk.config")}
        modules["app.sdk.network"].RequestUtils, modules["app.sdk.network"].SecurityUtils = Requests, Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid:8443/rss")
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
            self.d.HostRSSFetcher(source.url, clock=clock)(source.url, source, self.d.RequestBudget(timeout=5))
        self.assertEqual([5, 2], timeouts)
        self.assertTrue(all(response.closed for response in responses) if responses else True)

    def test_fix1_ipv6_allowlist_preserves_brackets_and_port(self):
        captured = []
        class Security:
            @staticmethod
            def evaluate_url_safety(url, allowed_domains, strict=False, block_private=False, allowed_private_ranges=None):
                captured.append((allowed_domains, allowed_private_ranges))
                return types.SimpleNamespace(allowed=allowed_domains == ["[fd00::6]:1200"])
        network = types.ModuleType("app.sdk.network"); network.SecurityUtils = Security
        with patch.dict(sys.modules, {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                                      "app.sdk.network": network}):
            self.d.HostRSSFetcher("http://[fd00::6]:1200/rss")._safe("http://[fd00::6]:1200/rss", "http://[fd00::6]:1200/rss")
        self.assertEqual([(["[fd00::6]:1200"], ["fd00::6/128"])], captured)

    def test_fix1_known_future_and_missing_date_seasons_remain_deferred_targets(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("themoviedb", "9"),
                                      title="Series", year="2020", category="tv",
                                      tmdb_info={"seasons": [{"season_number": 1, "air_date": "2020-01-01"},
                                                             {"season_number": 2, "air_date": "2999-01-01"},
                                                             {"season_number": 3}]})
        owner = Owner()
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), media=media, owner=owner)
        service.run(); rows = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual([1, 2, 3], sorted(rows))
        self.assertEqual("SUBMITTED", rows[1]["state"])
        self.assertEqual("SEASON_NOT_AIRED", rows[2]["reason"])
        self.assertEqual("SEASON_AIR_DATE_UNKNOWN", rows[3]["reason"])
        self.assertEqual([1], [call[1].season for call in owner.calls])
        for _ in range(5):
            self.clock.advance(86400)
            service.run()
        media.tmdb_info["seasons"][1]["air_date"] = "2020-01-01"
        self.clock.advance(86400)
        service.run()
        refreshed = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertIn(refreshed[2]["state"], {"SUBMITTED", "ALREADY_MANAGED"})

    def test_fix1_identity_constraints_defer_source_type_and_year_conflicts(self):
        item = self.d.RSSItem("Same Name (1990)", "https://movie.douban.com/subject/123/", "g", "", "i", "r", "123")
        class YearMeta(MetaService):
            def parse(self, *args, **kwargs):
                meta = Meta("Same Name"); meta.year = 1990
                return Correction(meta)
        cases = [
            ("source", types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("douban", "999"), title="Same Name", year="1990", category="movie", tmdb_info={}), "SOURCE_ID_CONFLICT"),
            ("type", types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("douban", "123"), title="Same Name", year="1990", category="tv", tmdb_info={}), "TYPE_HINT_CONFLICT"),
            ("year", types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("douban", "123"), title="Same Name", year="2020", category="movie", tmdb_info={}), "YEAR_CONFLICT")]
        for index, (label, media, reason) in enumerate(cases):
            recognizer = Recognizer(media)
            config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                                 sources=[{"id": label, "kind": "custom", "url": f"https://{label}.invalid/rss",
                                           "source_type_hint": "Movie"}])
            service = self.service(config, recognizer=recognizer, meta_service=YearMeta())
            service._observe(config.sources[0], item.__class__(**{**item.__dict__, "item_key": label, "raw_revision": f"r{index}"}))
            self.assertEqual(reason, service.records(source_id=label)[0]["reason"])
            self.assertEqual(("douban", "123"), recognizer.calls[0][0])
            self.assertEqual("电影", recognizer.calls[0][1])

    def test_fix1_target_exception_is_receipted_and_sibling_continues(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("themoviedb", "9"),
                                      title="Series", year="2020", category="tv", tmdb_info={"seasons": [
                                          {"season_number": 1, "air_date": "2020-01-01"},
                                          {"season_number": 2, "air_date": "2020-01-01"}]})
        def inventory(target):
            if target.season == 1: raise RuntimeError("offline")
            return {"state": "MISSING", "evidence_ref": "archive:missing"}
        owner = Owner()
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), media=media, owner=owner, inventory=inventory)
        service.run(); rows = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual(("DEFERRED", "INVENTORY_FAILED"), (rows[1]["state"], rows[1]["reason"]))
        self.assertEqual("SUBMITTED", rows[2]["state"])
        self.assertEqual([2], [call[1].season for call in owner.calls])

    def test_fix1_scheduler_exception_keeps_native_ack_and_continues(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("themoviedb", "10"),
                                      title="Series", year="2020", category="tv", tmdb_info={"seasons": [
                                          {"season_number": 1, "air_date": "2020-01-01"},
                                          {"season_number": 2, "air_date": "2020-01-01"}]})
        owner = self.ownership_mod.Ownership(self.repo, Host())
        def accepted(row, target, *_):
            if target.season == 1: raise RuntimeError("scheduler offline")
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]),
                               media=media, owner=owner, accepted=accepted)
        service.run(); rows = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual(("DEFERRED", "SCHEDULE_SCOPE_FAILED"), (rows[1]["state"], rows[1]["reason"]))
        self.assertIsNotNone(rows[1]["task_id"])
        self.assertEqual("SUBMITTED", rows[2]["state"])

    def test_fix1_ingest_stat_requires_all_real_target_units(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"), identity=("themoviedb", "9"),
                                      title="Series", year="2020", category="tv",
                                      tmdb_info={"seasons": [{"season_number": 1, "air_date": "2020-01-01"}]})
        owner = self.ownership_mod.Ownership(self.repo, Host())
        unit_keys = []
        def accepted(row, target, *_):
            for episode in (1, 2):
                key = json.dumps([*json.loads(target.key), episode], separators=(",", ":"), ensure_ascii=False)
                unit_keys.append(key)
                with self.repo.connection(write=True) as db:
                    db.execute("INSERT INTO target_units(target_key,task_id,identity) VALUES(?,?,?)", (key, row["id"], key))
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), media=media, owner=owner, accepted=accepted)
        service.run()
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO ingest_receipts VALUES(?,NULL,?,0,?,?,?)", ("r1", unit_keys[0], "v1", "{}", "2026-01-01"))
        self.assertEqual(0, service.statistics()["stages"]["ingest"]["numerator"])
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO ingest_receipts VALUES(?,NULL,?,0,?,?,?)", ("r2", unit_keys[1], "v1", "{}", "2026-01-01"))
        self.assertEqual(1, service.statistics()["stages"]["ingest"]["numerator"])

    def test_host_contract_is_fetch_only_until_submission_is_explicit(self):
        spec = importlib.util.spec_from_file_location("w09_subscribetter.host_discovery_contract",
                                                      PLUGIN / "host_discovery_contract.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        runtime = types.SimpleNamespace(test_source=lambda source: {"source_id": source, "fetch_only": True})
        plugin = types.SimpleNamespace(discovery=runtime, generation=9,
                                       discovery_tick=lambda generation, source_ids: {"generation": generation, "source_ids": source_ids})
        fetched = module.run_host_contract(plugin, phase="fetch", source_id="controlled")
        self.assertTrue(fetched["fetch_only"])
        self.assertFalse(fetched["submission"])
        with self.assertRaises(ValueError):
            module.run_host_contract(plugin, phase="run", source_id="controlled")
        submitted = module.run_host_contract(plugin, phase="run", source_id="controlled", confirm_submission=True)
        self.assertEqual(["controlled"], submitted["source_ids"])
        self.assertTrue(submitted["submission"])


SYNTHETIC_RSS = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss version='2.0'><channel><item><title>24</title>
<description>&lt;p&gt;1&lt;/p&gt;&lt;p&gt;24&lt;/p&gt;&lt;p&gt;8.6&lt;/p&gt;&lt;p&gt;2026 / Fiction&lt;/p&gt;</description>
<link>https://movie.douban.com/subject/35322132/</link><guid>x</guid></item>
<item><title>\xe6\xa8\xa1\xe8\x8c\x83\xe5\x87\xba\xe7\xa7\x9f\xe8\xbd\xa63</title><description>&lt;p&gt;7.2&lt;/p&gt;</description><link/><guid>name</guid></item>
</channel></rss>"""

OWNER_SNAPSHOT = {"fingerprint": "a" * 64, "overlaps": [], "unclassified": []}
ONE_BUDGET = {"items": 1, "response_bytes": 16384, "interval_min_seconds": 10, "interval_max_seconds": 10}


def owner_receipt(module, instance_id, config_digest, route_scope):
    return {"receipt_id": "fixture-receipt", "status": "ACTIVE", "selected_old_disable_receipts": ["old-disabled"],
            "expected_new_feature_set": {"module": module, "instance_id": instance_id,
                                         "config_digest": config_digest, "route_scope": route_scope},
            "fresh_handler_config_fingerprint": "a" * 64}


if __name__ == "__main__":
    unittest.main()
