"""W09 unit evidence: bounded work discovery feeding the existing ownership intent path."""
from datetime import datetime, timezone
from enum import Enum
from contextlib import asynccontextmanager
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
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


class ProviderMediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"


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
        category = getattr(media, "category", None)
        return {"state": "complete", "effective": {"category_id": f"fixture.{category}"} if category else None,
                "policy_revision": 7}

    @staticmethod
    def source_identity(media, source):
        values = []
        direct = getattr(media, {"douban": "douban_id", "themoviedb": "tmdb_id"}.get(source, ""), None)
        details = getattr(media, {"douban": "douban_info", "themoviedb": "tmdb_info"}.get(source, ""), None)
        if direct not in (None, ""):
            values.append(str(direct))
        if isinstance(details, dict) and details.get("id") not in (None, ""):
            values.append(str(details["id"]))
        values = sorted(set(values))
        return {"state": "VERIFIED", "media_id": values[0]} if len(values) == 1 else {
            "state": "CONFLICT" if len(values) > 1 else "UNKNOWN", "media_id": None}


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

    @staticmethod
    def wait(awaitable):
        return asyncio.run(awaitable)

    def config(self, **changes):
        source = dict(id="weekly", kind="rsshub", route_key="movie_weekly_best")
        data = dict(enabled=True, rsshub_base_url="http://rss.internal:1200/proxy/rsshub",
                    sources=[source], request_budget={"items": 10, "response_bytes": 16384,
                    "interval_min_seconds": 10, "interval_max_seconds": 10})
        data.update(changes)
        for configured in data["sources"]:
            configured.setdefault("destination_templates", {"movie": "/movie", "tv": "/tv", "anime": "/anime"})
            configured.setdefault("destination_category_bindings", {
                f"fixture.{destination}": destination for destination in configured["destination_templates"]})
        return self.d.DiscoveryConfig.model_validate(data)

    def service(self, config=None, *, fetch=None, media=None, owner=None, inventory=None,
                authorized=None, excluded=None, inventory_refresh=None, recognizer=None,
                meta_service=None, accepted=None, owner_check=None):
        if media is not None and not hasattr(media, "douban_id"):
            media.douban_id = "35322132"
        fetch_sync = fetch or (lambda *_: self.d.FetchResult(SYNTHETIC_RSS))
        async def fetch_async(*args):
            result = fetch_sync(*args)
            return await result if hasattr(result, "__await__") else result
        return self.d.DiscoveryService(
            self.repo, owner or Owner(), meta_service or MetaService(), recognizer or Recognizer(media), config or self.config(),
            fetch=fetch_async, clock=self.clock,
            inventory=inventory or (lambda _target: {"state": "MISSING", "evidence_ref": "fixture:missing"}),
            inventory_refresh=inventory_refresh,
            authorized=authorized or (lambda *_: True), excluded=excluded or (lambda _target: False),
            current=lambda: True, owner_check=owner_check or owner_receipt, accepted=accepted,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")

    def test_site_identity_bridge_preserves_source_and_admission_gates(self):
        for outcome in ('VERIFIED', 'CONFLICT', 'UNKNOWN', 'inventory_unknown', 'wrong_id'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                self.repo = self.repo_mod.Repository(Path(directory) / 'state.sqlite3')
                original = types.SimpleNamespace(type='电影', identity=('douban','35322132'),
                    douban_id='35322132', title='Fixture', year='2026', category='movie', tmdb_info={})
                mapped = types.SimpleNamespace(type='电影', identity=('themoviedb','42'),
                    title='Fixture', year='2026', category='movie', tmdb_info={'vote_average':8.0})
                owner, seen = Owner(), []
                def inventory(target):
                    seen.append(target.media_source)
                    return {'state':'UNKNOWN' if outcome=='inventory_unknown' or target.media_source=='douban' else 'MISSING',
                            'evidence_ref':'fixture'}
                service = self.service(media=original, owner=owner, inventory=inventory)
                def bridge(source, media):
                    self.assertIs(media, original)
                    return {'state':outcome if outcome in {'UNKNOWN','CONFLICT'} else 'VERIFIED', 'media':mapped,
                        'evidence':{'media_type':'电影','douban_id':'wrong' if outcome=='wrong_id' else '35322132',
                                    'canonical':['themoviedb','42'],'rule':'site-description-imdb-v1'}}
                service.identity_bridge = bridge
                self.wait(service.run())
                records=service.records()
                record=next(r for r in records if r['raw']['douban_subject_id']=='35322132')
                if outcome=='VERIFIED':
                    self.assertEqual(record['identity']['media_source'],'themoviedb')
                    self.assertEqual(record['site_identity']['raw_revision'],record['raw_revision'])
                    self.assertEqual(record['identity_evidence']['declared_source'],'douban')
                    self.assertTrue(owner.calls)
                    self.assertIn('themoviedb',seen)
                else:
                    self.assertFalse(owner.calls)
                self.assertFalse(hasattr(mapped,'douban_id'))

    def test_linked_site_bridge_revalidates_exact_identity_without_duplicate(self):
        original = types.SimpleNamespace(type='电影', identity=('douban','35322132'),
            douban_id='35322132', title='Fixture', year='2026', category='movie', tmdb_info={})
        mapped = types.SimpleNamespace(type='电影', identity=('themoviedb','42'),
            title='Fixture', year='2026', category='movie', tmdb_info={})
        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        service = self.service(self.config(request_budget=ONE_BUDGET), media=original, owner=owner)
        calls = []
        def bridge(source, media):
            calls.append(media)
            return {'state':'VERIFIED', 'media':mapped, 'evidence':{
                'media_type':'电影', 'douban_id':'35322132', 'canonical':list(mapped.identity)}}
        service.identity_bridge = bridge
        self.wait(service.run())
        record = service.records()[0]
        self.assertEqual(record['targets'][0]['state'], 'SUBMITTED')
        task_id = record['targets'][0]['task_id']
        service.reprocess([record['id']]);self.wait(service.run())
        current = service.records()[0]
        self.assertEqual(current['targets'][0]['state'], 'SUBMITTED')
        self.assertEqual(current['targets'][0]['task_id'], task_id)
        self.assertEqual(current['identity']['media_id'], '42')
        self.assertEqual(len(calls), 2)
        service.config_digest = 'changed-policy-revision'
        service.reprocess([record['id']]);self.wait(service.run())
        current = service.records()[0]
        self.assertEqual(current['targets'][0]['state'], 'ALREADY_MANAGED')
        self.assertEqual(current['targets'][0]['task_id'], task_id)
        mapped.identity = ('themoviedb','43')
        service.reprocess([record['id']]);self.wait(service.run())
        current = service.records()[0]
        self.assertEqual(current['reason'], 'IDENTITY_CHANGED')
        self.assertEqual(current['targets'][0]['task_id'], task_id)
        self.assertEqual(len(self.repo.list_tasks()), 1)
        self.assertEqual(host.creates, 1)

    def test_original_linked_douban_task_cannot_be_converted_by_bridge(self):
        original = types.SimpleNamespace(type='电影', identity=('douban','35322132'),
            douban_id='35322132', title='Fixture', year='2026', category='movie', tmdb_info={})
        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        service = self.service(self.config(request_budget=ONE_BUDGET), media=original, owner=owner)
        self.wait(service.run())
        record = service.records()[0]
        bridge_calls = []
        def forbidden(*args):
            bridge_calls.append(args)
            self.fail('linked Douban target must not invoke bridge')
        service.identity_bridge = forbidden
        service.reprocess([record['id']]);self.wait(service.run())
        current = service.records()[0]
        self.assertEqual(current['targets'][0]['state'], 'SUBMITTED')
        self.assertEqual(current['identity']['media_source'], 'douban')
        self.assertEqual(current['state'], 'SUBMITTED')
        self.assertEqual(bridge_calls, [])
        self.assertEqual(len(self.repo.list_tasks()), 1)
        self.assertEqual(host.creates, 1)

    def test_user_seven_presets_exact_paths_limits_and_custom_host(self):
        cases = [
            ('tv_real_time_hotest', 'tv_real_time_hotest', 'TV', 10),
            ('tv_global_best_weekly', 'tv_global_best_weekly', 'TV', 10),
            ('tv_chinese_best_weekly', 'tv_chinese_best_weekly', 'TV', 10),
            ('ECFA5DI7Q', 'ECFA5DI7Q/&dt_dapp=1', 'TV', 16),
            ('tv_american', 'tv_american', 'TV', 15),
            ('show_hot', 'show_hot', 'TV', 10),
            ('movie_real_time_hotest', 'movie_real_time_hotest', 'Movie', 10),
        ]
        for key, path, hint, limit in cases:
            with self.subTest(key=key):
                self.assertIn(key, self.d.ROUTES)
                for base in ('http://rsshub:1200', 'http://192.168.50.6:1200/proxy/rsshub'):
                    config = self.config(rsshub_base_url=base, sources=[dict(
                        id=key, kind='rsshub', route_key=key, request_budget={'items': limit})])
                    self.assertEqual(base + '/douban/list/' + path + '?limit=' + str(limit),
                                     self.d.source_url(config, config.sources[0]))
                    self.assertEqual('电视剧' if hint == 'TV' else '电影', self.d.ROUTES[key][1])
                original = 'http://original:1200/douban/list/' + path
                if key in ('tv_american', 'show_hot'):
                    original += '?limit=' + str(limit)
                imported = self.d.import_legacy({'rss_addrs': original + '@@' + hint})
                source = self.d.SourceConfig.model_validate(imported['sources'][0])
                self.assertEqual(hint, source.source_type_hint)
                self.assertEqual(original, self.d.source_url(config, source))
                catalog = {row['route_key']: row for row in self.service(config).catalog()}
                self.assertIn('user-supplied', catalog[key]['provenance']) if key in (
                    'ECFA5DI7Q', 'show_hot') else self.assertIn('deployed RSSHub', catalog[key]['provenance'])
        self.assertEqual('近期热门美剧榜 (ECFA5DI7Q)', self.d.ROUTES.get('ECFA5DI7Q', (None, None, None))[0])
        self.assertTrue(self.d.ROUTES.get('ECFA5DI7Q', (None, None, None))[2])
        self.assertFalse(self.d.ROUTES.get('show_hot', (None, None, None))[2])
        with self.assertRaises(ValidationError):
            self.d.SourceConfig(id='unsafe', kind='rsshub', route_key='ECFA5DI7Q/&dt_dapp=1')

    def test_user_preset_public_ui_catalog_provenance(self):
        from importlib import import_module
        fake_modules = {name: types.ModuleType(name) for name in ('app', 'app.sdk', 'app.sdk.security', 'app.schemas', 'app.schemas.token')}
        fake_modules['app.sdk.security'].verify_token = lambda: None
        fake_modules['app.schemas.token'].TokenPayload = type('TokenPayload', (), {})
        with patch.dict(sys.modules, fake_modules):
            ui = import_module(self.d.__package__ + '.ui')
        plugin = types.SimpleNamespace(repository=self.repo, _authorize=lambda user: None)
        routes = {row['key']: row for row in ui.Views(plugin).catalog(user=None).result['routes']}
        self.assertEqual(15, len(routes))
        for key in ('show_hot', 'ECFA5DI7Q'):
            self.assertIn('user-supplied', routes[key]['provenance'])
            self.assertEqual(self.d.ROUTES[key][0], routes[key]['label'])
        self.assertEqual('近期热门美剧榜 (ECFA5DI7Q)', routes['ECFA5DI7Q']['label'])
        self.assertIn('deployed RSSHub', routes['movie_weekly_best']['provenance'])

    def test_catalog_basepath_and_no_public_fallback(self):
        self.assertEqual(15, len(self.d.ROUTES))
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
        tested = self.wait(service.test_source(proposed=proposed))
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

    def test_T191_non_rss_and_invalid_channels_are_explicit_failures(self):
        for body, reason in (
            (b'<html><body>Access denied</body></html>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<feed xmlns="http://www.w3.org/2005/Atom"/>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<response><item><title>Not a feed</title></item></response>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<rss/>', 'RSS_INVALID_STRUCTURE'),
            (b'<rss><wrapper><channel/></wrapper></rss>', 'RSS_INVALID_STRUCTURE'),
            (b'<rss><channel/><channel/></rss>', 'RSS_INVALID_STRUCTURE'),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, '^' + reason + '$'):
                self.d.parse_rss(body)
        self.assertEqual([], self.d.parse_rss(b'<rss version="2.0"><channel/></rss>'))
        body = (b'<rss><channel><item><title>Actual item</title></item>'
                b'<description><item><title>Nested error item</title></item></description>'
                b'</channel><item><title>Outside channel</title></item></rss>')
        self.assertEqual(['Actual item'], [item.title for item in self.d.parse_rss(body)])

    def test_T191_bad_document_keeps_success_watermark_and_other_source_runs(self):
        for index, (body, reason) in enumerate((
            (b'<html><body>Access denied</body></html>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<feed xmlns="http://www.w3.org/2005/Atom"/>', 'RSS_UNSUPPORTED_FORMAT'),
            (b'<rss/>', 'RSS_INVALID_STRUCTURE'),
            (b'<rss><channel/><channel/></rss>', 'RSS_INVALID_STRUCTURE'),
        )):
            with self.subTest(reason=reason, index=index):
                bad_id, good_id = 'bad-' + str(index), 'good-' + str(index)
                config = self.config(sources=[
                    dict(id=bad_id, kind='custom', url='https://' + bad_id + '.invalid/rss'),
                    dict(id=good_id, kind='custom', url='https://' + good_id + '.invalid/rss')])
                owner, calls = Owner(), []
                def fetch(url, source, budget):
                    calls.append(source.id)
                    return self.d.FetchResult(body if source.id == bad_id else b'<rss><channel/></rss>')
                service = self.service(config, fetch=fetch, owner=owner)
                result = self.wait(service.run())
                self.assertEqual({'state': 'FAILED', 'reason': reason}, result['sources'][bad_id])
                self.assertEqual({'state': 'SUCCESS', 'reason': '', 'items': 0}, result['sources'][good_id])
                self.assertEqual([bad_id, good_id], calls)
                self.assertEqual([], owner.calls)
                with self.repo.connection() as db:
                    bad = dict(db.execute('SELECT * FROM discovery_sources WHERE source_id=?', (bad_id,)).fetchone())
                    good = dict(db.execute('SELECT * FROM discovery_sources WHERE source_id=?', (good_id,)).fetchone())
                    self.assertEqual(0, db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
                self.assertEqual(('FAILED', reason, 1, None),
                                 (bad['last_state'], bad['last_reason'], bad['failures'], bad['last_success']))
                self.assertIsNotNone(good['last_success'])

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

    def test_douban_dispatch_links_preserve_identity_and_source_type(self):
        for hint, kind, media_id in (("TV", "电视剧", "36449295"), ("Movie", "电影", "37068446")):
            link = "https://www.douban.com/doubanapp/dispatch/movie/" + media_id
            body = ("<rss><channel><item><title>榜单条目</title><link>" + link +
                    "</link></item></channel></rss>").encode()
            item = self.d.parse_rss(body)[0]
            self.assertEqual(media_id, item.douban_subject_id)
            self.assertIsNone(item.year)
            config = self.config(sources=[dict(id=hint, kind="custom", url="https://feed.invalid/rss", source_type_hint=hint)])
            media = types.SimpleNamespace(type=types.SimpleNamespace(value=kind), identity=("douban", "999"))
            recognizer, owner = Recognizer(media), Owner()
            service = self.service(config, recognizer=recognizer, owner=owner)
            service._observe(config.sources[0], item)
            self.assertEqual((("douban", media_id), kind, None), recognizer.calls[0])
            self.assertEqual("SOURCE_ID_CONFLICT", service.records(source_id=hint)[0]["reason"])
            self.assertEqual([], owner.calls)
        for link in ("https://www.douban.com.evil/doubanapp/dispatch/movie/123",
                     "http://www.douban.com/doubanapp/dispatch/movie/123",
                     "https://www.douban.com/doubanapp/dispatch/movie/123-extra"):
            body = ("<rss><channel><item><title>x</title><link>" + link +
                    "</link></item></channel></rss>").encode()
            self.assertIsNone(self.d.parse_rss(body)[0].douban_subject_id)

    def test_host_fetcher_enforces_total_deadline_identity_encoding_and_close(self):
        events = []
        class Response:
            status_code = 200
            headers = {"Content-Encoding": "identity"}
            async def aiter_bytes(self, _):
                yield b"<rss>"
                try:
                    await asyncio.sleep(10)
                finally:
                    events.append("body-cancelled")
        response = Response()
        request = types.SimpleNamespace(call=None)
        class Client:
            async def __aenter__(self): events.append("client-entered"); return self
            async def __aexit__(self, *_): events.append("client-closed")
        class Requests:
            def __init__(self, **kwargs): request.init = kwargs
            @asynccontextmanager
            async def get_stream(self, url, **kwargs):
                request.call = dict(url=url, **kwargs); events.append("entered")
                try:
                    yield response
                finally:
                    events.append("closed")
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(url, allowed_domains, strict=False, block_private=False,
                                                allowed_private_ranges=None):
                request.security = (url, allowed_domains, strict, block_private, allowed_private_ranges)
                return types.SimpleNamespace(allowed=url == "https://feed.invalid/rss"
                                              and allowed_domains == ["feed.invalid"]
                                              and strict and block_private)
        modules = {
            "app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
            "app.sdk.network": types.ModuleType("app.sdk.network"),
            "app.sdk.config": types.ModuleType("app.sdk.config"),
            "httpx2": types.ModuleType("httpx2")}
        def client_factory(**kwargs): request.client = kwargs; return Client()
        modules["httpx2"].AsyncClient, modules["httpx2"].Timeout = client_factory, lambda value: value
        modules["app.sdk.network"].AsyncRequestUtils = Requests
        modules["app.sdk.network"].SecurityUtils = Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid/rss")
        budget = types.SimpleNamespace(timeout=0.05, response_bytes=1024, items=1)
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
            self.wait(self.d.HostRSSFetcher(source.url)(source.url, source, budget))
        self.assertEqual("identity", request.call["headers"]["Accept-Encoding"])
        self.assertEqual(["client-entered", "entered", "body-cancelled", "closed", "client-closed"], events)
        self.assertFalse(request.client["trust_env"])
        self.assertTrue(request.client["verify"])
        self.assertTrue(request.client["http2"])
        self.assertFalse(request.client["follow_redirects"])
        self.assertEqual(("https://feed.invalid/rss", ["feed.invalid"], True, True, None), request.security)

    def test_host_fetcher_exactly_allows_configured_private_ip(self):
        captured = []
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(url, allowed_domains, strict=False, block_private=False,
                                                allowed_private_ranges=None):
                captured.append((allowed_domains, strict, block_private, allowed_private_ranges))
                return types.SimpleNamespace(allowed=True)
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network")}
        modules["app.sdk.network"].SecurityUtils = Security
        fetcher = self.d.HostRSSFetcher("http://192.168.50.6:1200/proxy/rsshub")
        with patch.dict(sys.modules, modules):
            asyncio.run(fetcher._safe("http://192.168.50.6:1200/proxy/rsshub/douban/list/movie_weekly_best",
                                      "http://192.168.50.6:1200/proxy/rsshub"))
        self.assertEqual([(["192.168.50.6:1200"], True, True, ["192.168.50.6/32"])], captured)

    def test_fix3_fetcher_is_native_async_and_closes_inside_running_loop(self):
        events = []
        class Response:
            status_code = 200
            headers = {"Content-Encoding": "identity"}
            async def aiter_bytes(self, _):
                yield b"<rss/>"
        class Client:
            async def __aenter__(self): events.append("client-open"); return self
            async def __aexit__(self, *_): events.append("client-close")
        class Requests:
            def __init__(self, **_): pass
            @asynccontextmanager
            async def get_stream(self, *_args, **_kwargs):
                events.append("open")
                try: yield Response()
                finally: events.append("close")
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(*_args, **_kwargs): return types.SimpleNamespace(allowed=True)
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network"),
                   "app.sdk.config": types.ModuleType("app.sdk.config"),
                   "httpx2": types.ModuleType("httpx2")}
        modules["httpx2"].AsyncClient, modules["httpx2"].Timeout = lambda **_: Client(), lambda value: value
        modules["app.sdk.network"].AsyncRequestUtils, modules["app.sdk.network"].SecurityUtils = Requests, Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid/rss")
        async def invoke():
            return await self.d.HostRSSFetcher(source.url)(source.url, source, self.d.RequestBudget(timeout=1))
        with patch.dict(sys.modules, modules):
            result = asyncio.run(invoke())
        self.assertEqual(b"<rss/>", result.body)
        self.assertEqual(["client-open", "open", "close", "client-close"], events)

    def test_fix3_async_url_safety_is_cancelled_inside_total_deadline(self):
        events = []
        class Client:
            async def __aenter__(self): events.append("client-open"); return self
            async def __aexit__(self, *_): events.append("client-close")
        class Requests:
            def __init__(self, **_): pass
            @asynccontextmanager
            async def get_stream(self, *_args, **_kwargs):
                raise AssertionError("transport must not start before URL safety completes")
                yield
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(*_args, **_kwargs):
                events.append("safety-open")
                try: await asyncio.sleep(10)
                finally: events.append("safety-cancelled")
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network"),
                   "app.sdk.config": types.ModuleType("app.sdk.config"),
                   "httpx2": types.ModuleType("httpx2")}
        modules["app.sdk.network"].AsyncRequestUtils, modules["app.sdk.network"].SecurityUtils = Requests, Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        modules["httpx2"].AsyncClient, modules["httpx2"].Timeout = lambda **_: Client(), lambda value: value
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid/rss")
        budget = types.SimpleNamespace(timeout=0.05, response_bytes=1024, items=1)
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
            self.wait(self.d.HostRSSFetcher(source.url)(source.url, source, budget))
        self.assertEqual(["client-open", "safety-open", "safety-cancelled", "client-close"], events)

    def test_fix3_slow_default_getaddrinfo_returns_at_deadline_without_starting_http(self):
        events, dns_finished = [], threading.Event()
        class Client:
            async def __aenter__(self): events.append("client-open"); return self
            async def __aexit__(self, *_): events.append("client-close")
        class Requests:
            def __init__(self, **_): pass
            @asynccontextmanager
            async def get_stream(self, *_args, **_kwargs):
                raise AssertionError("transport must not start before URL safety completes")
                yield
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(*_args, **_kwargs):
                try:
                    await asyncio.get_running_loop().getaddrinfo("slow.invalid", 443)
                finally:
                    events.append("safety-cancelled")
        def slow_getaddrinfo(*_args, **_kwargs):
            try:
                time.sleep(0.2)
                return []
            finally:
                dns_finished.set()
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network"),
                   "app.sdk.config": types.ModuleType("app.sdk.config"),
                   "httpx2": types.ModuleType("httpx2")}
        modules["app.sdk.network"].AsyncRequestUtils, modules["app.sdk.network"].SecurityUtils = Requests, Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        modules["httpx2"].AsyncClient, modules["httpx2"].Timeout = lambda **_: Client(), lambda value: value
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid/rss")
        budget = types.SimpleNamespace(timeout=0.02, response_bytes=1024, items=1)
        async def scenario():
            started = time.monotonic()
            with self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
                await self.d.HostRSSFetcher(source.url)(source.url, source, budget)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.12)
            self.assertTrue(await asyncio.to_thread(dns_finished.wait, 1))
        with patch.dict(sys.modules, modules), patch.object(socket, "getaddrinfo", slow_getaddrinfo):
            self.wait(scenario())
        self.assertEqual(["client-open", "safety-cancelled", "client-close"], events)

    def test_fix3_cancelled_run_drains_sync_stage_and_generation_fence_blocks_submit(self):
        entered, release = threading.Event(), threading.Event()
        active = [True]
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"),
                                      identity=("themoviedb", "700"), title="Fixture", year="2026",
                                      category="movie", tmdb_info={}, douban_id="35322132")
        class BlockingRecognizer(Recognizer):
            def recognize(self, *args, **kwargs):
                entered.set()
                if not release.wait(1):
                    raise RuntimeError("fixture release timeout")
                return super().recognize(*args, **kwargs)
        async def fetch(*_):
            return self.d.FetchResult(SYNTHETIC_RSS)
        owner = Owner()
        service = self.d.DiscoveryService(
            self.repo, owner, MetaService(), BlockingRecognizer(media),
            self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET),
            fetch=fetch, clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref": "fixture"},
            authorized=lambda *_: True, excluded=lambda _: False, current=lambda: active[0],
            owner_check=owner_receipt, owner_snapshot=lambda *_: OWNER_SNAPSHOT,
            instance_id="SubscriBetter")
        async def scenario():
            task = asyncio.create_task(service.run())
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            active[0] = False
            started = time.monotonic()
            asyncio.get_running_loop().call_later(0.05, release.set)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertGreaterEqual(time.monotonic() - started, 0.04)
        self.wait(scenario())
        self.assertEqual([], owner.calls)
        target = service.records()[0]["targets"][0]
        self.assertEqual(("DEFERRED", "STALE_GENERATION"), (target["state"], target["reason"]))

    def test_fix4_failed_drain_preserves_repeated_cancellation_and_stops_later_sources(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        fetches, later_effects = [], []
        config = self.config(sources=[
            {"id": "a", "kind": "custom", "url": "https://a.invalid/rss"},
            {"id": "b", "kind": "custom", "url": "https://b.invalid/rss"}],
            request_budget=ONE_BUDGET)
        async def fetch(_url, source, _budget):
            fetches.append(source.id)
            return self.d.FetchResult(SYNTHETIC_RSS)
        service = self.service(config, fetch=fetch)
        def observe(source, _item):
            if source.id == "a":
                entered.set()
                try:
                    if not release.wait(1):
                        raise RuntimeError("fixture release timeout")
                    raise RuntimeError("failed while draining")
                finally:
                    finished.set()
            later_effects.append(source.id)
            return "REJECTED"
        service._observe = observe
        async def scenario():
            loop = asyncio.get_running_loop()
            prior_handler = loop.get_exception_handler()
            unexpected = []
            loop.set_exception_handler(lambda _loop, context: unexpected.append(context))
            task = asyncio.create_task(service.run())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                loop.call_later(0.05, release.set)
                try:
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                self.assertTrue(task.cancelled())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual([], unexpected)
            finally:
                release.set()
                loop.set_exception_handler(prior_handler)
        self.wait(scenario())
        self.assertTrue(finished.is_set())
        self.assertEqual(["a"], fetches)
        self.assertEqual([], later_effects)
        def ordinary_failure():
            raise RuntimeError("ordinary stage failure")
        with self.assertRaisesRegex(RuntimeError, "ordinary stage failure"):
            self.wait(self.d._drainable_to_thread(ordinary_failure))

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
        result = self.wait(self.service(config, fetch=fetch).run())
        self.assertEqual("FAILED", result["sources"]["a"]["state"])
        self.assertEqual("SUCCESS", result["sources"]["b"]["state"])
        self.assertEqual("RATELIMITED", result["sources"]["c"]["state"])
        self.assertEqual(2, len(calls))
        restarted = self.wait(self.service(config, fetch=fetch).run(["c"]))
        self.assertEqual("RATELIMITED", restarted["sources"]["c"]["state"])
        self.clock.advance(121)
        self.assertEqual("SUCCESS", self.wait(self.service(config, fetch=fetch).run(["c"]))["sources"]["c"]["state"])

    def test_retry_limit_stops_repeated_fetch_until_explicit_action(self):
        config = self.config(request_budget={"items": 1, "response_bytes": 16384,
                                             "interval_min_seconds": 1, "interval_max_seconds": 1,
                                             "retry_limit": 1})
        calls = []
        def fetch(*_):
            calls.append(1)
            raise self.d.FetchError("NETWORK_ERROR")
        service = self.service(config, fetch=fetch)
        self.assertEqual("FAILED", self.wait(service.run())["sources"]["weekly"]["state"])
        self.clock.advance(2)
        self.assertEqual("FAILED", self.wait(service.run())["sources"]["weekly"]["state"])
        self.clock.advance(2)
        third = self.wait(service.run())["sources"]["weekly"]
        self.assertEqual({"state": "DEFERRED", "reason": "RETRY_EXHAUSTED"}, third)
        self.assertEqual(2, len(calls))
        self.assertEqual(1, service.retry_sources(["weekly"]))
        self.assertEqual("FAILED", self.wait(service.run())["sources"]["weekly"]["state"])
        self.assertEqual(3, len(calls))

    def test_requested_history_replays_without_feed_and_retains_failure_audit(self):
        for original in ("unrecognized", "low_rating"):
            for network in ("empty", "failed", "backoff"):
                with self.subTest(original=original, network=network):
                    # Each case gets an independent real repository.
                    with tempfile.TemporaryDirectory() as directory:
                        self.repo = self.repo_mod.Repository(Path(directory) / "state.sqlite3")
                        media = types.SimpleNamespace(type="电影", identity=("themoviedb", "42"),
                            douban_id="35322132", year="2026", category="movie", tmdb_info={"vote_average":5.0})
                        recognizer, owner = Recognizer(None if original == "unrecognized" else media), Owner()
                        config = self.config(minimum_rating=7.0, request_budget={**ONE_BUDGET, "items":2})
                        body, calls = [SYNTHETIC_RSS], []
                        def fetch(*_):
                            calls.append(1)
                            if body[0] is None: raise self.d.FetchError("NETWORK_ERROR")
                            return self.d.FetchResult(body[0])
                        service = self.service(config, recognizer=recognizer, owner=owner, fetch=fetch)
                        self.wait(service.run())
                        with self.repo.connection() as db:
                            rows = [dict(row) for row in db.execute("SELECT * FROM discovery_records ORDER BY id")]
                        selected, untouched = rows
                        self.assertEqual("IDENTITY_UNKNOWN" if original == "unrecognized" else "RATING_BELOW_MINIMUM", selected["reason"])
                        media.tmdb_info = {"vote_average":8.5}; recognizer.media = media
                        self.assertEqual(1, service.reprocess([selected["id"]]))
                        body[0] = None if network == "failed" else b"<rss><channel/></rss>"
                        if network == "backoff":
                            with self.repo.connection(write=True) as db:
                                db.execute("UPDATE discovery_sources SET failures=100 WHERE source_id='weekly'")
                        if network != "backoff": self.clock.advance(11)
                        result = self.wait(service.run())["sources"]["weekly"]
                        self.assertEqual(["SUBMITTED"], result["history"]["states"])
                        self.assertEqual(1, len(owner.calls))
                        self.assertEqual("FAILED" if network == "failed" else "DEFERRED" if network == "backoff" else "SUCCESS", result["state"])
                        self.assertEqual(1 if network == "backoff" else 2, len(calls))
                        with self.repo.connection() as db:
                            self.assertEqual(untouched, dict(db.execute("SELECT * FROM discovery_records WHERE id=?", (untouched["id"],)).fetchone()))
                            self.assertTrue(db.execute("SELECT 1 FROM audit WHERE action=?", (f"DISCOVERY:{selected['id']}:{selected['state']}:{selected['reason']}",)).fetchone())

    def test_requested_history_shares_items_budget_and_deduplicates_feed(self):
        service = self.service(self.config(request_budget={**ONE_BUDGET,"items":2}))
        self.wait(service.run())
        ids = [row["id"] for row in service.records()]
        service.reprocess(ids)
        service = self.service(self.config(request_budget={**ONE_BUDGET,"items":1}))
        first = self.wait(service.run())["sources"]["weekly"]
        self.assertEqual(1, first["history"]["items"])
        self.assertEqual(1, len(service.recognizer.calls))
        self.assertEqual(1, sum(row["reason"] == "REPROCESS_REQUESTED" for row in service.records()))
        # One history item plus the same item in RSS must not consume two slots/calls.
        service = self.service(self.config(request_budget={**ONE_BUDGET,"items":2}))
        self.clock.advance(11)
        with patch.object(service, "_observe", wraps=service._observe) as observed:
            result = self.wait(service.run())["sources"]["weekly"]
        keys = [(call.args[1].item_key, call.args[1].raw_revision) for call in observed.call_args_list]
        self.assertEqual(2, len(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(1, result["history"]["items"])
        self.assertLessEqual(len(service.recognizer.calls), 2)

    def test_requested_history_respects_source_current_owner_and_target_fences(self):
        for fence in ("config_disabled", "source_disabled", "not_selected", "stale", "owner", "unauthorized", "STOPPED", "RELEASED"):
            with self.subTest(fence=fence), tempfile.TemporaryDirectory() as directory:
                self.repo = self.repo_mod.Repository(Path(directory) / "state.sqlite3")
                media = types.SimpleNamespace(type="电影", identity=("themoviedb", "42"),
                    year="2026", category="movie", tmdb_info={"vote_average":8.5})
                owner = Owner()
                service = self.service(self.config(request_budget=ONE_BUDGET), media=media, owner=owner,
                    inventory=lambda _: {"state":"PRESENT", "evidence_ref":"fixture:existing"})
                self.wait(service.run())
                record = service.records()[0]
                service.reprocess([record["id"]])
                # Target can be stopped after the replay request was queued.
                if fence in ("STOPPED", "RELEASED"):
                    with self.repo.connection(write=True) as db:
                        db.execute("UPDATE discovery_targets SET state=? WHERE record_id=?", (fence, record["id"]))
                    self.assertEqual(0, service.reprocess([record["id"]]))
                config = service.config.model_copy(deep=True)
                if fence == "unauthorized": config.minimum_rating = 7.0
                if fence == "config_disabled": config.enabled = False
                if fence == "source_disabled": config.sources[0].enabled = False
                replay = self.service(config, media=media, owner=owner,
                    fetch=lambda *_: self.d.FetchResult(b"<rss><channel/></rss>"),
                    authorized=(lambda *_: False) if fence == "unauthorized" else None)
                if fence == "stale": replay.current = lambda: False
                if fence == "owner": replay.owner_check = lambda *_: None
                self.clock.advance(11)
                self.wait(replay.run(["another"] if fence == "not_selected" else None))
                self.assertEqual([], owner.calls)
                if fence == "unauthorized":
                    self.assertEqual("SCOPE_NOT_AUTHORIZED", replay.records()[0]["reason"])
                if fence != "unauthorized":
                    self.assertEqual([], replay.recognizer.calls)
                    self.assertEqual("REPROCESS_REQUESTED", replay.records()[0]["reason"])

    def test_history_exception_isolated_and_does_not_overwrite_committed_result(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as directory:
                self.repo = self.repo_mod.Repository(Path(directory) / "state.sqlite3")
                config = self.config(sources=[dict(id="first",kind="rsshub",route_key="movie_weekly_best"),
                    dict(id="second",kind="custom",url="https://other.invalid/rss")], request_budget=ONE_BUDGET)
                service = self.service(config)
                self.wait(service.run())
                ids = [row["id"] for row in service.records()]
                self.assertEqual(2, len(ids))
                service.reprocess(ids)
                media = types.SimpleNamespace(type="电影",identity=("themoviedb","42"),douban_id="35322132",
                    year="2026",category="movie",tmdb_info={"vote_average":8.5})
                service.recognizer.media = media
                observe = service._observe
                def fail_first(source, item):
                    if source.id == "first":
                        if committed: observe(source, item)
                        raise RuntimeError("fixture history failure")
                    return observe(source, item)
                with patch.object(service, "_observe", side_effect=fail_first):
                    result = self.wait(service.run())
                self.assertEqual(["SUBMITTED"], result["sources"]["second"]["history"]["states"])
                self.assertEqual("HISTORY_REPROCESS_FAILED", result["sources"]["first"]["history"]["errors"][0]["reason"])
                self.assertEqual("PARTIAL", result["sources"]["first"]["state"])
                with self.repo.connection() as db:
                    row = db.execute("SELECT * FROM discovery_records WHERE source_id='first'").fetchone()
                    self.assertEqual("SUBMITTED" if committed else "DEFERRED", row["state"])
                    self.assertEqual("" if committed else "HISTORY_REPROCESS_FAILED", row["reason"])
                    self.assertEqual(0, db.execute("SELECT failures FROM discovery_sources WHERE source_id='first'").fetchone()[0])
                calls = len(service.recognizer.calls)
                # No persistent marker left for an unbounded local replay loop.
                self.wait(service.run())
                self.assertEqual(calls, len(service.recognizer.calls))

    def test_history_reparses_original_dispatch_link_and_reuses_same_target(self):
        body = SYNTHETIC_RSS.replace(b"https://movie.douban.com/subject/35322132/",
            b"https://www.douban.com/doubanapp/dispatch/movie/35322132")
        media = types.SimpleNamespace(type="电影", identity=("douban","35322132"),
            year="2026",category="movie",tmdb_info={})
        owner = Owner()
        service = self.service(self.config(request_budget=ONE_BUDGET), media=media, owner=owner,
            fetch=lambda *_: self.d.FetchResult(body))
        self.wait(service.run())
        record = service.records()[0]
        self.assertEqual(1, len(owner.calls))
        with self.repo.connection(write=True) as db:
            raw = json.loads(db.execute("SELECT raw FROM discovery_records WHERE id=?", (record["id"],)).fetchone()[0])
            raw["douban_subject_id"] = None  # Legacy parser-derived value, original link retained.
            db.execute("UPDATE discovery_records SET raw=? WHERE id=?", (json.dumps(raw), record["id"]))
        service.reprocess([record["id"]])
        self.wait(service.run())
        self.assertEqual(("douban","35322132"), service.recognizer.calls[-1][0])
        self.assertEqual(1, len(owner.calls))
        with self.repo.connection() as db:
            stored = json.loads(db.execute("SELECT raw FROM discovery_records WHERE id=?", (record["id"],)).fetchone()[0])
        self.assertEqual(raw, stored)

    def test_history_changed_canonical_identity_keeps_existing_task(self):
        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        media = types.SimpleNamespace(type="电影", identity=("douban","35322132"),
            douban_id="35322132",year="2026",category="movie",tmdb_info={})
        service = self.service(self.config(request_budget=ONE_BUDGET), media=media, owner=owner)
        self.wait(service.run())
        record = service.records()[0]
        self.assertIsNotNone(record["targets"][0]["task_id"])
        service.reprocess([record["id"]])
        self.wait(service.run())
        self.assertEqual(1, host.creates)
        old_task = self.repo.get_task(record["targets"][0]["task_id"])
        service.reprocess([record["id"]])
        media.identity = ("themoviedb","42")  # Valid payload maps back to the declared Douban ID.
        self.wait(service.run())
        self.assertEqual(1, host.creates)
        self.assertEqual("IDENTITY_CHANGED", service.records()[0]["reason"])
        self.assertEqual(record["targets"], service.records()[0]["targets"])
        self.assertEqual(old_task, self.repo.get_task(record["targets"][0]["task_id"]))

    def test_rule_revision_replays_only_wrong_identity_from_original_rss(self):
        from test_meta import native
        from test_planner import load

        meta = load("meta")
        body = SYNTHETIC_RSS.replace(b"<title>24</title>",
            "<title>虚构故事 Fictional.2024</title>".encode("utf-8"))
        wrong = types.SimpleNamespace(type="电影", identity=("themoviedb", "41"),
            douban_id="35322132", title="Fictional", year="2024", category="movie", tmdb_info={})
        corrected = types.SimpleNamespace(type="电影", identity=("themoviedb", "42"),
            douban_id="35322132", title="虚构故事", year="2024", category="movie", tmdb_info={})

        class RuleRecognizer(Recognizer):
            def recognize(self, parsed, declared, *, media_type=None):
                self.calls.append((declared, media_type, getattr(parsed, "year", None)))
                if (parsed.cn_name or parsed.en_name) == "虚构故事":
                    return corrected
                return wrong if declared else None

        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        parser = lambda *args, **kwargs: native("Fictional", type="电影", begin_episode=None)
        service = self.service(self.config(request_budget={**ONE_BUDGET, "items": 2}),
            owner=owner, recognizer=RuleRecognizer(),
            meta_service=meta.MetaService(self.repo, meta.MetaCorrector(), parser),
            fetch=lambda *_: self.d.FetchResult(body))
        self.wait(service.run())
        first = next(row for row in service.records() if row["raw"]["douban_subject_id"])
        untouched = next(row for row in service.records() if row["id"] != first["id"])
        self.assertEqual("SUBMITTED", first["state"])
        self.assertEqual("41", first["identity"]["media_id"])
        self.assertEqual(1, host.creates)
        with self.repo.connection() as db:
            original_raw = db.execute("SELECT raw FROM discovery_records WHERE id=?", (first["id"],)).fetchone()[0]
            original_other = dict(db.execute("SELECT * FROM discovery_records WHERE id=?", (untouched["id"],)).fetchone())
        sample = "discovery:" + first["raw_revision"]
        old_revision = service.meta_service.corrector.revision
        service.meta_service.corrector = meta.MetaCorrector(["虚构故事"])
        self.assertNotEqual(old_revision, service.meta_service.corrector.revision)
        self.assertEqual(1, service.reprocess([first["id"]]))
        service.fetch = lambda *_: self.d.FetchResult(b"<rss><channel/></rss>")
        self.clock.advance(11)
        self.wait(service.run())
        replayed = next(row for row in service.records() if row["id"] == first["id"])
        self.assertEqual((first["id"], "IDENTITY_CHANGED"), (replayed["id"], replayed["reason"]))
        self.assertEqual("虚构故事", replayed["correction"]["corrected"]["cn_name"])
        self.assertEqual(service.meta_service.corrector.revision, replayed["correction"]["revision"])
        self.assertEqual(first["targets"], replayed["targets"])
        self.assertEqual(1, host.creates)
        self.assertEqual(2, len(self.repo.parse_history(sample)))
        with self.repo.connection() as db:
            self.assertEqual(original_raw, db.execute("SELECT raw FROM discovery_records WHERE id=?", (first["id"],)).fetchone()[0])
            self.assertEqual(original_other, dict(db.execute("SELECT * FROM discovery_records WHERE id=?", (untouched["id"],)).fetchone()))

    def test_record_retry_is_bounded_and_policy_revision_reconsiders(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "42"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        config = self.config(minimum_rating=7.0, request_budget={"items": 1, "response_bytes": 16384,
                             "interval_min_seconds": 1, "interval_max_seconds": 1, "retry_limit": 1})
        service = self.service(config, media=media)
        self.wait(service.run())
        self.assertEqual(1, service.records()[0]["retry_count"])
        self.clock.advance(2)
        self.wait(service.run())
        self.assertEqual("RATING_UNKNOWN", service.records()[0]["reason"])
        self.assertEqual(1, service.records()[0]["retry_count"])
        self.clock.advance(86401)
        self.wait(service.run())
        self.assertEqual(2, service.records()[0]["retry_count"])
        self.clock.advance(2)
        self.wait(service.run())
        self.assertEqual(2, service.records()[0]["retry_count"])
        changed = self.service(config.model_copy(update={"minimum_rating": None}), media=media)
        self.clock.advance(2)
        self.wait(changed.run())
        self.assertEqual("SUBMITTED", changed.records()[0]["state"])
        revision = changed.records()[0]["filter_revision"]
        self.clock.advance(2)
        self.wait(changed.run())
        self.assertEqual(revision, changed.records()[0]["filter_revision"])
        self.assertEqual("SUBMITTED", changed.records()[0]["state"])

    def test_unknown_provider_score_rechecks_after_daily_due_with_threshold_evidence(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "42"),
                                      douban_id="35322132", title="Fixture", year="2026", category="movie", tmdb_info={})
        owner = Owner()
        service = self.service(self.config(minimum_rating=7.0, request_budget={"items":1,"response_bytes":16384,
                               "interval_min_seconds":1,"interval_max_seconds":1,"retry_limit":1}), media=media, owner=owner)
        self.wait(service.run())
        record = service.records()[0]
        self.assertEqual(("DEFERRED","RATING_UNKNOWN"),(record["state"],record["reason"]))
        self.assertEqual({"rating_source":"recognized_provider","recognized_provider":"themoviedb",
                          "minimum_rating":7.0,"minimum_release_year":None},record["filter_evidence"])
        self.assertIsNone(record["rating"])
        media.tmdb_info={"vote_average":8.25}
        self.clock.advance(10)
        self.wait(service.run())
        self.assertEqual("DEFERRED",service.records()[0]["state"])
        self.clock.advance(86401)
        self.wait(service.run())
        record=service.records()[0]
        self.assertEqual("SUBMITTED",record["state"])
        self.assertEqual({"provider":"themoviedb","field":"tmdb_info.vote_average","value":8.25},
                         {key:record["rating"][key] for key in ("provider","field","value")})
        self.assertEqual(1,len(owner.calls))
        self.wait(service.run())
        self.assertEqual(1,len(owner.calls))

    def test_unknown_provider_year_rechecks_after_daily_due(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "42"),
                                      douban_id="35322132", title="Fixture", year=None, category="movie", tmdb_info={})
        owner = Owner()
        service = self.service(self.config(minimum_release_year=2020, request_budget={"items":1,"response_bytes":16384,
                               "interval_min_seconds":1,"interval_max_seconds":1,"retry_limit":1}), media=media, owner=owner)
        self.wait(service.run())
        record=service.records()[0]
        self.assertEqual(("DEFERRED","YEAR_UNKNOWN"),(record["state"],record["reason"]))
        self.assertEqual({"rating_source":"recognized_provider","recognized_provider":"themoviedb",
                          "minimum_rating":None,"minimum_release_year":2020},record["filter_evidence"])
        media.year="2026"
        self.clock.advance(10)
        self.wait(service.run())
        self.assertEqual("DEFERRED",service.records()[0]["state"])
        self.clock.advance(86401)
        self.wait(service.run())
        self.assertEqual("SUBMITTED",service.records()[0]["state"])
        self.assertEqual(1,len(owner.calls))

    def test_movie_provider_enum_rating_retains_payload_evidence(self):
        details={"vote_average":8.25,"media_type":ProviderMediaType.MOVIE,
                 "nested":[{"type":ProviderMediaType.MOVIE,"title":"虚构影片"}]}
        media=types.SimpleNamespace(identity=("themoviedb","42"),type=ProviderMediaType.MOVIE,
                                    title="Fixture",year="2026",category="movie",tmdb_info=details)
        owner=Owner();service=self.service(self.config(request_budget=ONE_BUDGET),media=media,owner=owner)
        result=self.wait(service.run())
        self.assertEqual("SUCCESS",result["sources"]["weekly"]["state"],result)
        self.assertEqual("SUBMITTED",service.records()[0]["state"])
        expected={"vote_average":8.25,"media_type":"电影","nested":[{"type":"电影","title":"虚构影片"}]}
        self.assertEqual("provider-payload:"+self.d._digest(expected),service.records()[0]["rating"]["evidence_ref"])
        self.assertEqual(8.25,service.records()[0]["rating"]["value"])
        self.assertIs(ProviderMediaType.MOVIE,details["media_type"])
        self.assertIs(ProviderMediaType.MOVIE,details["nested"][0]["type"])
        self.assertEqual(1,len(owner.calls))

    def test_provider_rating_known_seasons_and_partial_receipts(self):
        media = types.SimpleNamespace(
            identity=("themoviedb", "1396"), type=types.SimpleNamespace(value="电视剧"),
            title="Fixture Series", year="2008", category="tv",
            tmdb_info={"vote_average": 8.951, "media_type":ProviderMediaType.TV, "seasons": [
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
        result = self.wait(self.service(config, media=media, owner=owner).run())
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

    def test_provider_evidence_keeps_json_digest_and_rejects_unknown_objects(self):
        plain={"vote_average":8.25,"media_type":"电影","nested":[True,None,42,"虚构"]}
        encoded={**plain,"media_type":ProviderMediaType.MOVIE}
        expected=self.d._digest(plain)
        for payload in (plain,encoded,dict(reversed(list(encoded.items())))):
            self.assertEqual(expected,self.d._digest(payload,default=self.d._provider_value))
        with self.assertRaises(TypeError):self.d._digest(encoded)
        class Unsupported:
            def __str__(self):raise AssertionError("secret-bearing str must not be called")
            def __repr__(self):raise AssertionError("secret-bearing repr must not be called")
        class BadEnum(Enum):
            VALUE=Unsupported()
        for value in (Unsupported(),BadEnum.VALUE,{"unordered"},datetime.now(timezone.utc)):
            with self.subTest(kind=type(value).__name__),self.assertRaisesRegex(TypeError,"UNSUPPORTED_PROVIDER_VALUE"):
                self.d._digest({"nested":[value]},default=self.d._provider_value)
        owner=Owner()
        media=types.SimpleNamespace(identity=("themoviedb","42"),type=ProviderMediaType.MOVIE,
                                    title="Fixture",year="2026",category="movie",
                                    tmdb_info={"vote_average":8.25,"unexpected":Unsupported()})
        service=self.service(self.config(request_budget=ONE_BUDGET),media=media,owner=owner)
        result=self.wait(service.run())
        self.assertEqual({"state":"FAILED","reason":"SOURCE_FAILED"},result["sources"]["weekly"])
        self.assertEqual([],owner.calls)

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
                self.wait(service.run())
                record = service.records()[0]
                self.assertEqual("DEFERRED" if label == "score" else "REJECTED", record["state"])
                self.assertEqual(reason, record["reason"])
                self.clock.advance(11)
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "99"),
                                      title="Deadpool 2", year="2018", category="movie", tmdb_info={})
        service = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media,
                               inventory=lambda _: {"state": "UNKNOWN", "evidence_ref": None})
        self.wait(service.run())
        self.assertEqual("LIBRARY_STATE_UNKNOWN", service.records()[0]["reason"])

    def test_existing_record_only_stopped_precedence_cleanup_and_reprocess(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "7"),
                                      title="Fixture", year="2026", category="movie",
                                      tmdb_info={"vote_average": 8.0})
        service = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media,
                               inventory=lambda _: {"state": "PRESENT", "evidence_ref": "archive:v1"})
        self.wait(service.run())
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
        self.wait(service.run())
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
        self.wait(service.run())
        self.assertEqual([('["电影","themoviedb","253774",null,""]', "weekly")], probes)
        self.assertEqual("SUBMITTED", service.records()[0]["state"])

    def test_history_views_separate_identity_evidence(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "8"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        recognized = self.service(self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET), media=media)
        self.wait(recognized.run())
        other = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                            sources=[{"id": "other", "kind": "custom", "url": "https://other.invalid/rss"}])
        self.wait(self.service(other, media=None).run())
        self.assertEqual(1, len(recognized.records(view="recognized")))
        self.assertEqual(1, len(recognized.records(view="unrecognized")))
        self.assertLessEqual(len(recognized.records(view="latest12", limit=100)), 12)

    def test_owner_receipt_is_exact_and_unbound_owner_cannot_submit(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "8"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        async def fetch(*_): return self.d.FetchResult(SYNTHETIC_RSS)
        service = self.d.DiscoveryService(
            self.repo, Owner(), MetaService(), Recognizer(media), self.config(media_type_allowlist=["电影"]),
            fetch=fetch, clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref": "fixture"}, authorized=lambda *_: True,
            excluded=lambda _: False, current=lambda: True, owner_check=lambda *_: None,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")
        result = self.wait(service.run())
        self.assertEqual("OWNER_UNBOUND", result["sources"]["weekly"]["reason"])
        self.assertEqual([], service.owner.calls)

    def test_cross_source_dedup_stopped_precedence_and_common_exclusion_gate(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"), identity=("themoviedb", "700"),
                                      title="Fixture", year="2026", category="movie", tmdb_info={})
        media.douban_id = "35322132"
        host = Host()
        owner = self.ownership_mod.Ownership(self.repo, host)
        async def fetch(*_): return self.d.FetchResult(SYNTHETIC_RSS)
        config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET, sources=[
            {"id": "one", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "two", "kind": "rsshub", "route_key": "movie_showing"},
        ])
        service = self.d.DiscoveryService(
            self.repo, owner, MetaService(), Recognizer(media), config,
            fetch=fetch, clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref": "fixture"}, authorized=lambda *_: True,
            excluded=lambda _: False, current=lambda: True, owner_check=owner_receipt,
            owner_snapshot=lambda *_: OWNER_SNAPSHOT, instance_id="SubscriBetter")
        self.wait(service.run())
        self.clock.advance(10)
        self.wait(service.run())
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
            fetch=fetch, clock=self.clock,
            inventory=lambda _: {"state": "MISSING", "evidence_ref":"fixture"}, authorized=lambda *_: True, excluded=lambda _: False,
            current=lambda: True, owner_check=owner_receipt, owner_snapshot=lambda *_: OWNER_SNAPSHOT,
            instance_id="SubscriBetter")
        self.wait(stopped.run())
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
            for table in ("management_operations", "management_previews", "candidate_decisions", "archive_scan_baselines", "migration_history", "migration_receipts", "discovery_targets", "discovery_records", "discovery_sources"):
                db.execute("DROP TABLE " + table)
            db.execute("DROP INDEX archive_target_identity")
            db.execute("PRAGMA user_version=8")
        migrated = self.repo_mod.Repository(self.repo.path)
        self.assertEqual(task["id"], migrated.get_task(task["id"])["id"])
        db = sqlite3.connect(self.repo.path)
        try:
            self.assertEqual(12, db.execute("PRAGMA user_version").fetchone()[0])
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
                self.wait(service.run())
                self.assertEqual("LIBRARY_STATE_UNKNOWN", service.records(source_id=source_id)[0]["reason"])
                self.assertEqual([], owner.calls)

    def test_fix1_shared_origin_spacing_rotates_without_starvation(self):
        config = self.config(sources=[
            {"id": "a", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "b", "kind": "rsshub", "route_key": "movie_showing"},
            {"id": "c", "kind": "rsshub", "route_key": "movie_real_time_hotest"},
            {"id": "d", "kind": "rsshub", "route_key": "tv_real_time_hotest",
             "request_budget": {**ONE_BUDGET, "retry_limit": 0}}], request_budget=ONE_BUDGET)
        calls = []
        service = self.service(config, fetch=lambda url, *_: (calls.append((url, self.clock())),
                                                               self.d.FetchResult(b"<rss><channel/></rss>"))[1])
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE discovery_sources SET failures=1 WHERE source_id='d'")
        for _ in range(6):
            self.wait(service.run())
            self.clock.advance(10)
        self.assertEqual(["movie_weekly_best", "movie_showing", "movie_real_time_hotest"] * 2,
                         [urlsplit(url).path.rsplit("/", 1)[-1] for url, _ in calls])
        self.assertEqual([0, 10, 20, 30, 40, 50], [int(at - 1_800_000_000) for _, at in calls])

    def test_fix3_unbound_first_peer_does_not_block_owned_fair_rotation(self):
        config = self.config(sources=[
            {"id": "a", "kind": "rsshub", "route_key": "movie_weekly_best"},
            {"id": "b", "kind": "rsshub", "route_key": "movie_showing"},
            {"id": "c", "kind": "rsshub", "route_key": "movie_real_time_hotest"}],
            request_budget=ONE_BUDGET)
        calls = []
        def receipt(module, instance_id, config_digest, route_scope):
            return None if route_scope == "a" else owner_receipt(module, instance_id, config_digest, route_scope)
        service = self.service(config, owner_check=receipt,
                               fetch=lambda url, *_: (calls.append(url), self.d.FetchResult(b"<rss><channel/></rss>"))[1])
        for _ in range(4):
            result = self.wait(service.run())
            self.assertEqual("OWNER_UNBOUND", result["sources"]["a"]["reason"])
            self.clock.advance(10)
        self.assertEqual(["movie_showing", "movie_real_time_hotest"] * 2,
                         [urlsplit(url).path.rsplit("/", 1)[-1] for url in calls])

    def test_fix2_sdk_classification_uses_exact_arbitrary_category_binding(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"),
                                      identity=("themoviedb", "42"), title="Series", year="2020",
                                      category="arbitrary.category-42", tmdb_info={"seasons": [
                                          {"season_number": 1, "air_date": "2020-01-01"}]})
        destinations = []
        config = self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                             request_budget=ONE_BUDGET, sources=[{
                                 "id": "bound", "kind": "rsshub", "route_key": "tv_real_time_hotest",
                                 "destination_templates": {"anime": "/anime"},
                                 "destination_category_bindings": {"fixture.arbitrary.category-42": "anime"}}])
        service = self.service(config, media=media, authorized=lambda _t, _s, destination:
                               (destinations.append(destination), True)[1])
        self.wait(service.run())
        self.assertEqual(["anime"], destinations)
        self.assertEqual("SUBMITTED", service.records()[0]["state"])
        unbound = self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                              request_budget=ONE_BUDGET, sources=[{
                                  "id": "unbound", "kind": "rsshub", "route_key": "tv_real_time_hotest",
                                  "destination_templates": {"tv": "/tv"},
                                  "destination_category_bindings": {}}])
        other = self.service(unbound, media=media)
        self.clock.advance(10)
        self.wait(other.run())
        self.assertEqual("DESTINATION_CATEGORY_UNBOUND", other.records(source_id="unbound")[0]["reason"])

    def test_fix1_total_deadline_covers_redirect_and_delayed_empty_response(self):
        timeouts, events = [], []
        class Response:
            def __init__(self, status, headers): self.status_code, self.headers = status, headers
            async def aiter_bytes(self, _):
                if False: yield b""
        responses = [Response(302, {"Location": "/final"}), Response(200, {"Content-Encoding": "identity"})]
        class Client:
            async def __aenter__(self): events.append(("client", "open")); return self
            async def __aexit__(self, *_): events.append(("client", "close"))
        class Requests:
            def __init__(self, **kwargs): timeouts.append(kwargs["timeout"])
            @asynccontextmanager
            async def get_stream(self, url, **kwargs):
                response = responses.pop(0); events.append(("open", url))
                try:
                    await asyncio.sleep(0.005 if response.status_code == 302 else 10)
                    yield response
                finally:
                    events.append(("close", url))
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(url, allowed_domains, **kwargs):
                return types.SimpleNamespace(allowed=allowed_domains == ["feed.invalid:8443"] and
                                              urlsplit(url).netloc == "feed.invalid:8443")
        modules = {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                   "app.sdk.network": types.ModuleType("app.sdk.network"),
                   "app.sdk.config": types.ModuleType("app.sdk.config"),
                   "httpx2": types.ModuleType("httpx2")}
        modules["httpx2"].AsyncClient, modules["httpx2"].Timeout = lambda **_: Client(), lambda value: value
        modules["app.sdk.network"].AsyncRequestUtils, modules["app.sdk.network"].SecurityUtils = Requests, Security
        modules["app.sdk.config"].settings = types.SimpleNamespace(PROXY={})
        source = self.d.SourceConfig(id="custom", kind="custom", url="https://feed.invalid:8443/rss")
        async def invoke():
            with self.assertRaisesRegex(self.d.FetchError, "TIMEOUT"):
                await self.d.HostRSSFetcher(source.url)(
                    source.url, source, types.SimpleNamespace(timeout=0.05, response_bytes=1024, items=1))
        with patch.dict(sys.modules, modules):
            self.wait(invoke())
        self.assertEqual(2, len(timeouts))
        self.assertEqual(2, len([event for event in events if event[0] == "close"]))
        self.assertEqual(("client", "close"), events[-1])

    def test_fix1_ipv6_allowlist_preserves_brackets_and_port(self):
        captured = []
        class Security:
            @staticmethod
            async def evaluate_url_safety_async(url, allowed_domains, strict=False, block_private=False, allowed_private_ranges=None):
                captured.append((allowed_domains, allowed_private_ranges))
                return types.SimpleNamespace(allowed=allowed_domains == ["[fd00::6]:1200"])
        network = types.ModuleType("app.sdk.network"); network.SecurityUtils = Security
        with patch.dict(sys.modules, {"app": types.ModuleType("app"), "app.sdk": types.ModuleType("app.sdk"),
                                      "app.sdk.network": network}):
            asyncio.run(self.d.HostRSSFetcher("http://[fd00::6]:1200/rss")._safe(
                "http://[fd00::6]:1200/rss", "http://[fd00::6]:1200/rss"))
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
        self.wait(service.run()); rows = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual([1, 2, 3], sorted(rows))
        self.assertEqual("SUBMITTED", rows[1]["state"])
        self.assertEqual("SEASON_NOT_AIRED", rows[2]["reason"])
        self.assertEqual("SEASON_AIR_DATE_UNKNOWN", rows[3]["reason"])
        self.assertEqual([1], [call[1].season for call in owner.calls])
        for _ in range(5):
            self.clock.advance(86400)
            self.wait(service.run())
        media.tmdb_info["seasons"][1]["air_date"] = "2020-01-01"
        self.clock.advance(86400)
        self.wait(service.run())
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
        self.wait(service.run()); rows = {row["season"]: row for row in service.records()[0]["targets"]}
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
        failed_inventory = set()
        def inventory(target):
            if target.season in failed_inventory: raise RuntimeError("inventory offline")
            return {"state": "MISSING", "evidence_ref": "archive:missing"}
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                                {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]),
                               media=media, owner=owner, accepted=accepted, inventory=inventory)
        self.wait(service.run()); rows = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual(("DEFERRED", "SCHEDULE_SCOPE_FAILED"), (rows[1]["state"], rows[1]["reason"]))
        self.assertIsNotNone(rows[1]["task_id"])
        self.assertEqual("SUBMITTED", rows[2]["state"])
        preserved = (rows[1]["task_id"], rows[1]["intent_key"], rows[1]["snapshot_digest"])
        failed_inventory.add(1)
        self.clock.advance(86400)
        self.wait(service.run()); retried = {row["season"]: row for row in service.records()[0]["targets"]}
        self.assertEqual("INVENTORY_FAILED", retried[1]["reason"])
        self.assertEqual(preserved, (retried[1]["task_id"], retried[1]["intent_key"], retried[1]["snapshot_digest"]))
        self.assertEqual("SUBMITTED", retried[2]["state"])

    def test_fix2_terminal_and_unknown_tv_metadata_are_reconsidered_daily(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"),
                                      identity=("themoviedb", "77"), title="Series", year="2020",
                                      category="tv", tmdb_info={"seasons": [
                                          {"season_number": 1, "air_date": "2020-01-01"}]})
        owner = Owner()
        config = self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                             request_budget={**ONE_BUDGET, "retry_limit": 0}, sources=[
                                 {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}])
        service = self.service(config, media=media, owner=owner)
        self.wait(service.run())
        media.tmdb_info["seasons"].append({"season_number": 2, "air_date": "2020-01-01"})
        self.clock.advance(86400)
        self.wait(service.run())
        self.assertEqual([1, 2], [call[1].season for call in owner.calls])
        unknown_media = types.SimpleNamespace(type=types.SimpleNamespace(value="电视剧"),
                                              identity=("themoviedb", "88"), title="Unknown", year="2020",
                                              category="tv", tmdb_info={"seasons": []})
        unknown_config = self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                     request_budget={**ONE_BUDGET, "retry_limit": 0}, sources=[
                                         {"id":"unknown","kind":"rsshub","route_key":"tv_real_time_hotest"}])
        unknown_owner = Owner()
        unknown = self.service(unknown_config, media=unknown_media, owner=unknown_owner)
        self.clock.advance(10)
        self.wait(unknown.run())
        self.assertEqual("SEASON_METADATA_UNKNOWN", unknown.records(source_id="unknown")[0]["reason"])
        unknown_media.tmdb_info["seasons"].append({"season_number": 1, "air_date": "2020-01-01"})
        self.clock.advance(86400)
        self.wait(unknown.run())
        self.assertEqual([1], [call[1].season for call in unknown_owner.calls])

    def test_fix3_policy_change_reconsiders_record_only_existing_target(self):
        media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"),
                                      identity=("themoviedb", "700"), title="Fixture", year="2026",
                                      category="movie", tmdb_info={}, douban_id="35322132")
        first_config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                                   sources=[{"id": "weekly", "kind": "rsshub",
                                             "route_key": "movie_weekly_best",
                                             "existing_media_action": "record_only"}])
        first_owner = Owner()
        inventory = lambda _: {"state": "PRESENT", "evidence_ref": "archive:present"}
        first = self.service(first_config, media=media, owner=first_owner, inventory=inventory)
        self.wait(first.run())
        self.assertEqual("EXISTING", first.records()[0]["state"])
        self.assertEqual([], first_owner.calls)
        changed_source = first_config.sources[0].model_copy(update={"existing_media_action": "manage_authorized"})
        changed_config = first_config.model_copy(update={"sources": [changed_source]})
        changed_owner = Owner()
        changed = self.service(changed_config, media=media, owner=changed_owner, inventory=inventory)
        self.clock.advance(10)
        self.wait(changed.run())
        self.assertEqual(1, len(changed_owner.calls))
        self.assertEqual("SUBMITTED", changed.records()[0]["state"])

    def test_fix2_cross_source_identity_requires_exact_provider_mapping(self):
        declared = self.d.RSSItem("Same Name (1990)", "https://movie.douban.com/subject/123/",
                                  "g", "", "declared", "declared-r", "123")
        class YearMeta(MetaService):
            def parse(self, *args, **kwargs):
                meta = Meta("Same Name"); meta.year = 1990
                return Correction(meta)
        cases = [
            ("verified", "123", "SUBMITTED", ""),
            ("conflict", "456", "DEFERRED", "SOURCE_ID_CONFLICT"),
            ("missing", None, "DEFERRED", "SOURCE_MAPPING_UNKNOWN")]
        for index, (label, douban_id, state, reason) in enumerate(cases):
            with self.subTest(label=label):
                media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"),
                                              identity=("themoviedb", "999"), tmdb_id=999,
                                              douban_id=douban_id, title="Same Name", year="1990",
                                              category="movie", tmdb_info={"id": 999})
                config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                                     sources=[{"id": label, "kind": "custom", "url": f"https://{label}.invalid/rss",
                                               "source_type_hint": "Movie"}])
                service = self.service(config, media=media, meta_service=YearMeta())
                item = declared.__class__(**{**declared.__dict__, "item_key": label, "raw_revision": f"r{index}"})
                self.assertEqual(state, service._observe(config.sources[0], item))
                self.assertEqual(reason, service.records(source_id=label)[0]["reason"])
        unresolved_media = types.SimpleNamespace(type=types.SimpleNamespace(value="电影"),
                                                 identity=("themoviedb", "999"), tmdb_id=999,
                                                 title="Same Name", year="1990", category="movie",
                                                 tmdb_info={"id": 999})
        unresolved_config = self.config(media_type_allowlist=["电影"], request_budget=ONE_BUDGET,
                                        sources=[{"id": "unresolved", "kind": "custom",
                                                  "url": "https://unresolved.invalid/rss", "source_type_hint": "Movie"}])
        unresolved = self.service(unresolved_config, media=unresolved_media)
        item = self.d.RSSItem("Same Name", "", "same-name", "", "unresolved", "unresolved-r")
        unresolved._observe(unresolved_config.sources[0], item)
        self.assertEqual("IDENTITY_EVIDENCE_INSUFFICIENT", unresolved.records(source_id="unresolved")[0]["reason"])

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
            # Accepted runtime admissions declare a scope; retained unit rows alone
            # cannot establish which episodes are still required after a reduction.
            self.repo.setting('runtime-task:'+str(row['id']),{'scope':{'units':list(unit_keys)}})
        service = self.service(self.config(media_type_allowlist=["电视剧"], season_scope="all_known",
                                           request_budget=ONE_BUDGET, sources=[
                                               {"id":"weekly","kind":"rsshub","route_key":"tv_real_time_hotest"}]), media=media, owner=owner, accepted=accepted)
        self.wait(service.run())
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
        async def test_source(source):
            return {"source_id": source, "fetch_only": True}
        async def discovery_tick(generation, source_ids):
            return {"generation": generation, "source_ids": source_ids}
        runtime = types.SimpleNamespace(test_source=test_source)
        plugin = types.SimpleNamespace(discovery=runtime, generation=9, discovery_tick=discovery_tick)
        fetched = self.wait(module.run_host_contract(plugin, phase="fetch", source_id="controlled"))
        self.assertTrue(fetched["fetch_only"])
        self.assertFalse(fetched["submission"])
        with self.assertRaises(ValueError):
            self.wait(module.run_host_contract(plugin, phase="run", source_id="controlled"))
        submitted = self.wait(module.run_host_contract(
            plugin, phase="run", source_id="controlled", confirm_submission=True))
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
