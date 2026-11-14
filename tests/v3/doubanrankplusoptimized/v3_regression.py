"""V3-only regressions for DoubanRankPlusOptimized.

Run: python -m unittest discover -s tests/v3/doubanrankplusoptimized -p '*_regression.py' -v
Offline host fakes only: no network, database, install, or real subscription.
"""

import copy
import datetime
import importlib.util
import json
import re
import sys
import types
import unittest
from enum import Enum
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar
from unittest.mock import MagicMock, Mock, patch

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
T = TypeVar("T")


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"
    UNKNOWN = "未知"


class MediaSource(str, Enum):
    TMDB = "themoviedb"
    Douban = "douban"


def resolve_media_identity(media=None, media_source=None, media_id=None):
    if media_source is not None or media_id is not None:
        source = media_source
        if isinstance(source, str):
            source = {"themoviedb": MediaSource.TMDB, "tmdb": MediaSource.TMDB,
                      "douban": MediaSource.Douban}.get(source.strip().casefold(), source)
        ident = str(media_id).strip() if media_id is not None else ""
        if isinstance(source, MediaSource) and ident and ident != "0":
            return source, ident
        return None, None
    if media is None:
        return None, None
    return resolve_media_identity(
        media_source=getattr(media, "media_source", None),
        media_id=getattr(media, "media_id", None),
    )


def build_media_key(source, media_id):
    source, media_id = resolve_media_identity(media_source=source, media_id=media_id)
    if not source:
        return ""
    prefix = "tmdb" if source == MediaSource.TMDB else source.value
    return f"{prefix}:{media_id}"


class Response(BaseModel, Generic[T]):
    success: bool
    message: str = ""
    data: Optional[T] = None


class MetaInfo:
    def __init__(self, title, mtype=None):
        match = re.search(r"S(\d+)|第([一二三四])季", title)
        self.begin_season = (int(match[1]) if match[1] else "一二三四".index(match[2]) + 1) if match else None
        self.name = title[:match.start()].strip() if match else title
        self.type = mtype or (MediaType.TV if match else MediaType.MOVIE)
        self.year = None


class _PluginBase:
    pass


def load_plugin(module_name="app.plugins.doubanrankplusoptimized"):
    exports = {
        "apscheduler.schedulers.background": {"BackgroundScheduler": Mock},
        "apscheduler.triggers.cron": {"CronTrigger": Mock()},
        "app.schemas": {"Response": Response},
        "app.schemas.types": {"MediaType": MediaType, "MediaSource": MediaSource,
                              "ChainEventType": types.SimpleNamespace(SubscribeCompletionCheck="check")},
        "app.sdk.config": {"settings": types.SimpleNamespace(TZ="Asia/Hong_Kong", API_TOKEN="test-token")},
        "app.sdk.logging": {"logger": Mock()},
        "app.sdk.events": {"eventmanager": types.SimpleNamespace(register=lambda _: lambda func: func)},
        "app.sdk.media": {
            "MediaInfo": types.SimpleNamespace, "MetaBase": MetaInfo,
            "MetaInfo": MetaInfo, "build_media_key": build_media_key,
            "resolve_media_identity": resolve_media_identity,
        },
        "app.sdk.network": {"RequestUtils": Mock},
        "app.sdk.plugin": {"_PluginBase": _PluginBase},
        "app.sdk.queries": {"list_subscriptions": Mock(), "list_download_history": Mock(), "list_transfer_history": Mock()},
        "app.sdk.services": {"DownloaderHelper": Mock, "MediaServerHelper": Mock},
        "app.application.subscription.delete": {"SubscribeDeletionActor": lambda u, s: types.SimpleNamespace(
            username=u, is_superuser=s)},
        "app.application.subscription.mutation": {"SubscriptionActor": lambda u, s: types.SimpleNamespace(
            name=u, is_superuser=s)},
        "app.application.subscription.priority": {
            "get_best_version_target_episodes": lambda row: range(row.start_episode, row.total_episode + 1),
            "get_episode_priority": lambda row: dict(row.episode_priority or {}),
            "prepare_subscribe_progress_fields": lambda row: {
                "current_priority": min(row.episode_priority.values()),
                "lack_episode": sum(not priority for priority in row.episode_priority.values()),
            },
        },
        "app.sdk.utilities": {"DomUtils": Mock},
        "app.chain.subscribe": {"SubscribeChain": Mock},
        "app.chain.media": {"MediaChain": Mock},
        "app.chain.mediaserver": {"MediaServerChain": Mock},
    }
    modules = {}
    for name, attributes in exports.items():
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            modules.setdefault(".".join(parts[:end]), types.ModuleType(".".join(parts[:end])))
        modules[name].__dict__.update(attributes)
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "plugins.v3/doubanrankplusoptimized/__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    modules[module_name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


MOD = load_plugin()


def fixture(mtype=MediaType.TV, title="Example S02 Show", all_seasons=True,
            existing_seasons=(), library_exists=True, tmdb_id=123):
    plugin = MOD.DoubanRankPlusOptimized()
    plugin._scheduler = None
    plugin._rss_addrs = ["https://example.invalid/rss;/movies#/tv#/anime"]
    plugin._ranks = []
    plugin._is_seasons_all = all_seasons
    plugin._is_only_movies = False
    plugin._release_year = 0
    plugin._vote = 0
    plugin._min_sleep_time = plugin._max_sleep_time = 0
    plugin._migrate_once = False
    plugin._clearflag = False
    plugin._clearflag_unrecognized = False
    plugin.mediachain = Mock()
    plugin.mediachain.recognize_media.return_value = types.SimpleNamespace(
        title="Example", original_title="Example", title_year="Example (2026)", year="2026",
        type=mtype, tmdb_id=tmdb_id,
        media_source=MediaSource.TMDB if tmdb_id else None,
        media_id=str(tmdb_id) if tmdb_id else None,
        number_of_seasons=3 if mtype == MediaType.TV else None,
        genre_ids=[], vote_average=8.0, overview="Example overview",
        season_info=[{"season_number": i, "episode_count": 10, "air_date": "2026-01-01"} for i in range(4)],
        seasons={i: list(range(1, 11)) for i in range(4)},
        season_years={i: "2026" for i in range(4)},
        get_poster_image=lambda: "poster.jpg",
    )
    plugin.mediachain.search_medias.return_value = []
    rows = {}
    for season in existing_seasons:
        rows[season] = {"id": len(rows) + 1, "best_version": 0, "season": season}
    plugin.subscribechain = Mock()
    plugin.subscribechain.exists.side_effect = lambda *, mediainfo, meta: meta.begin_season in rows

    def add(**kwargs):
        sid = len(rows) + 1
        rows[kwargs["season"]] = {"id": sid, **kwargs}
        return sid, "新增订阅成功"

    plugin.subscribechain.add.side_effect = add
    plugin._DoubanRankPlusOptimized__get_rss_info = Mock(return_value=[{
        "title": title, "year": "2026", "doubanid": "456",
        "mtype": "tv" if mtype == MediaType.TV else "movie",
    }])
    store = {}
    plugin.get_data = lambda key: copy.deepcopy(store.get(key))
    plugin.save_data = lambda key, value: store.__setitem__(key, copy.deepcopy(value))
    state = types.SimpleNamespace(plugin=plugin, rows=rows, store=store,
                                  mediachain=plugin.mediachain,
                                  subscribechain=plugin.subscribechain)
    return state


class WorkflowTests(unittest.TestCase):
    def run_refresh(self, f, retry_only=False):
        f.plugin._DoubanRankPlusOptimized__start_task(retry_only=retry_only)
        self.assertEqual(len(f.store.get("history", [])), 1)
        return f.store["history"][0]["status"]

    def assert_wash(self, row):
        self.assertEqual(row["best_version"], 1)
        self.assertEqual(row["best_version_full"], 0)
        self.assertIs(type(row["best_version"]), int)
        self.assertIs(type(row["best_version_full"]), int)

    def test_movie_and_library_still_use_wash(self):
        f = fixture(MediaType.MOVIE, title="Example Movie")
        f.plugin.downloadchain = Mock()
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assert_wash(f.rows[None])
        f.plugin.downloadchain.get_no_exists_info.assert_not_called()
        call = f.plugin.subscribechain.add.call_args.kwargs
        self.assertEqual((call["media_source"], call["media_id"]), (MediaSource.TMDB, "123"))
        self.assertNotIn("tmdbid", call)
        self.assertNotIn("doubanid", call)

    def test_tv_all_seasons_per_episode_wash_and_season_dedup(self):
        f = fixture(existing_seasons=(2,))
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {1, 2, 3})
        self.assertEqual(f.rows[2]["best_version"], 0)
        self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [1, 3])
        for season in (1, 3):
            self.assert_wash(f.rows[season])

    def test_failure_is_not_success_and_retry_schedule_preserved(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "未获取到总集数")
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_FAILED.value)
            retry = f.store["history"][0]["season_results"]["2"]["retry"]
            self.assertEqual((retry["attempts"], retry["next_retry_at"]), (1, 1900))
            self.run_refresh(f)
            self.assertEqual(f.plugin.subscribechain.add.call_count, 1)
            clock.return_value = 1900

    def test_explicit_season_limits_scope_and_keeps_separate_history(self):
        f = fixture(title="Example")
        items = f.plugin._DoubanRankPlusOptimized__get_rss_info
        first = dict(items.return_value[0], season=1)
        items.return_value = [first, dict(first, season=2)]
        f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(set(f.rows), {1, 2})
        self.assertEqual([f.rows[season]["title"] for season in (1, 2)], ["Example", "Example"])
        self.assertEqual(len({entry["unique"] for entry in f.store["history"]}), 2)

    def test_migration_known_success_prevents_repeat_add_and_is_idempotent(self):
        f = fixture(MediaType.MOVIE, title="New alias")
        f.store["history"] = [dict(unique="old", type=MediaType.MOVIE.value, tmdbid="123",
                                   title="Old alias", status=MOD.Status.SUBSCRIPTION_ADDED.value)]
        f.plugin.init_plugin({"rss_addrs": "https://example.invalid/rss;/movies#/tv#/anime"})
        f.plugin.mediachain, f.plugin.subscribechain = f.mediachain, f.subscribechain
        migrated = f.store["history"][0]
        self.assertEqual(migrated["identity"]["media_source"], "themoviedb")
        self.assertEqual(migrated["identity"]["media_id"], "123")
        self.assertEqual(migrated["media_source"], "themoviedb")
        self.assertEqual(migrated["media_id"], "123")
        backup = f.store["history_legacy_backup"]
        self.assertEqual(backup[0]["tmdbid"], "123")
        self.assertNotIn("identity", backup[0])
        snapshot = copy.deepcopy(f.store["history"])
        f.plugin.init_plugin({"rss_addrs": "https://example.invalid/rss;/movies#/tv#/anime"})
        self.assertEqual(f.store["history"], snapshot)
        f.plugin._DoubanRankPlusOptimized__start_task()
        f.plugin.subscribechain.add.assert_not_called()
        self.assertEqual(len(f.store["history"]), 2)

    def test_migration_prefers_one_complete_pair_and_runs_before_scheduler(self):
        class Scheduler:
            def __init__(self, **kwargs):
                pass

            def add_job(self, **kwargs):
                pass

            def get_jobs(self):
                return [1]

            def print_jobs(self):
                pass

            def start(self):
                Scheduler.seen = f.store["history"][0].get("identity")

        f = fixture(MediaType.MOVIE, title="Alias")
        f.plugin.update_config = Mock()
        f.store["history"] = [dict(
            unique="root-complete", type=MediaType.MOVIE.value,
            media_source="douban", media_id="456",
            identity={"media_source": "themoviedb"},
            status=MOD.Status.SUBSCRIPTION_ADDED.value,
        )]
        with patch.object(MOD, "BackgroundScheduler", Scheduler):
            f.plugin.init_plugin({
                "enabled": True, "onlyonce": True,
                "rss_addrs": "https://example.invalid/rss",
            })
        self.assertEqual((Scheduler.seen["media_source"], Scheduler.seen["media_id"]), ("douban", "456"))
        record = f.store["history"][0]
        self.assertEqual((record["identity"]["media_source"], record["identity"]["media_id"]),
                         ("douban", "456"))

    def test_existing_valid_pair_wins_and_invalid_legacy_is_preserved(self):
        f = fixture(MediaType.MOVIE, title="Alias")
        f.store["history"] = [
            dict(unique="conflict", type=MediaType.MOVIE.value, tmdbid="999",
                 identity={"type": MediaType.MOVIE.value, "media_source": "douban", "media_id": "456"},
                 status=MOD.Status.SUBSCRIPTION_ADDED.value),
            dict(unique="half", identity={"media_source": "themoviedb"}, status=MOD.Status.UNRECOGNIZED.value),
            dict(unique="zero", identity={"media_source": "themoviedb", "media_id": "0"},
                 status=MOD.Status.UNRECOGNIZED.value),
        ]
        f.plugin.init_plugin({"rss_addrs": "https://example.invalid/rss;/movies#/tv#/anime"})
        history = {r["unique"]: r for r in f.store["history"]}
        self.assertEqual(history["conflict"]["identity"]["media_source"], "douban")
        self.assertEqual(history["conflict"]["identity"]["media_id"], "456")
        self.assertEqual(history["conflict"]["identity_migration"], "kept-existing-conflict")
        self.assertEqual(history["half"]["identity_migration"], "skipped-invalid-identity")
        self.assertEqual(history["zero"]["identity_migration"], "skipped-invalid-identity")
        self.assertEqual(history["zero"]["identity"]["media_id"], "0")

    def test_source_mismatch_defers_without_creating(self):
        f = fixture(MediaType.MOVIE, title="Alias")
        f.store["history"] = [dict(unique="doubanrankplusoptimized_douban:456", doubanid="456",
                                   type=MediaType.MOVIE.value, tmdbid="123",
                                   identity={"type": MediaType.MOVIE.value, "media_source": "douban", "media_id": "456"},
                                   status=MOD.Status.UNRECOGNIZED.value)]
        f.plugin.init_plugin({"rss_addrs": "https://example.invalid/rss;/movies#/tv#/anime"})
        f.plugin.mediachain, f.plugin.subscribechain = f.mediachain, f.subscribechain
        self.assertEqual(self.run_refresh(f), MOD.Status.PROCESS_FAILED.value)
        f.plugin.subscribechain.add.assert_not_called()
        self.assertIn("不一致", f.store["history"][0]["error"])

    def test_partial_tv_failure_preserves_successes_and_retries_only_failed_season(self):
        f = fixture()
        add = f.plugin.subscribechain.add.side_effect

        def fail_second(**kw):
            if kw["season"] == 2:
                return None, "temporary failure"
            return add(**kw)

        f.plugin.subscribechain.add.side_effect = fail_second
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.assertEqual(self.run_refresh(f), MOD.Status.PARTIAL_SUCCESS.value)
            results = f.store["history"][0]["season_results"]
            self.assertEqual(results["1"]["status"], MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual(results["2"]["status"], MOD.Status.SUBSCRIPTION_FAILED.value)
            clock.return_value = 1900
            f.plugin.subscribechain.add.side_effect = add
            f.plugin.subscribechain.add.reset_mock()
            self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [2])

    def test_failure_retries_three_times_then_stops(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "未获取到总集数")
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            for attempt, delay in enumerate((900, 3600, 21600, None), 1):
                self.run_refresh(f)
                retry = f.store["history"][0]["season_results"]["2"]["retry"]
                self.assertEqual(retry["attempts"], attempt)
                self.assertEqual(f.plugin.subscribechain.add.call_count, attempt)
                if delay is None:
                    self.assertIsNone(retry["next_retry_at"])
                else:
                    self.assertEqual(retry["next_retry_at"], clock.return_value + delay)
                    self.run_refresh(f)
                    self.assertEqual(f.plugin.subscribechain.add.call_count, attempt)
                    clock.return_value = retry["next_retry_at"]

    def test_douban_season_is_passed_separately_from_name(self):
        f = fixture(title="骑有此理 第四季")
        media = f.plugin.mediachain.recognize_media.return_value
        media.title = "骑有此理 第四季"
        media.media_source = MediaSource.Douban
        media.media_id = "456"
        media.season_info = []
        media.seasons = {4: [1, 2, 3]}
        media.season_years = {4: "2026"}
        f.mediachain.search_medias.return_value = [media]
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {4})
        self.assertEqual((f.rows[4]["title"], f.rows[4]["media_source"], f.rows[4]["media_id"]),
                         ("骑有此理", MediaSource.Douban, "456"))
        search_meta = f.mediachain.search_medias.call_args.kwargs["meta"]
        self.assertEqual(search_meta.name, "骑有此理")
        self.assertIsNone(search_meta.begin_season)

    def test_literal_movie_title_survives_wrong_primary_recognition(self):
        f = fixture(MediaType.MOVIE, title="哥斯拉-0.0")
        wrong = f.mediachain.recognize_media.return_value
        wrong.title, wrong.year, wrong.media_id = "哥斯拉", "2014", "124905"
        right = copy.copy(wrong)
        right.title, right.year, right.media_id = "哥斯拉-0.0", "2026", "1381071"
        f.mediachain.search_medias.return_value = [wrong, right]
        f.mediachain.recognize_media.side_effect = lambda **kw: right if kw.get("media_id") == "1381071" else wrong
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual((f.rows[None]["title"], f.rows[None]["media_id"]), ("哥斯拉-0.0", "1381071"))
        self.assertEqual(f.mediachain.search_medias.call_args.kwargs["meta"].name, "哥斯拉-0.0")

    def test_blue_planet_separate_series_uses_real_first_season(self):
        f = fixture(title="蓝色星球 第三季")
        target = f.mediachain.recognize_media.return_value
        target.title, target.original_title, target.media_id = "蓝色星球3", "Blue Planet III", "208929"
        target.season_info = []
        target.seasons = {1: list(range(1, 7))}
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {1})
        self.assertEqual(f.rows[1]["media_id"], "208929")

    def test_special_uses_verified_main_episodes_when_catalog_empty(self):
        f = fixture(title="兰香如故·花胜秋宴")
        f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]["doubanid"] = None
        media = f.mediachain.recognize_media.return_value
        media.title, media.media_source, media.media_id = "兰香如故·花胜秋宴", MediaSource.Douban, "39004014"
        media.seasons, media.season_info = {}, []
        f.mediachain.search_medias.return_value = [media]
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {1})
        self.assertEqual(f.rows[1]["total_episode"], 2)


class WashTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.plugin = self.f.plugin
        self.plugin._enabled = True
        self.now = datetime.datetime.now()
        self.tv = types.SimpleNamespace(
            id=4, name="Example", year="2025", username=self.plugin.plugin_name,
            type=MediaType.TV.value, best_version=1, best_version_full=0, season=2,
            episode_group=None, start_episode=1, total_episode=2, state="R",
            media_source=MediaSource.TMDB.value, media_id="123", date="2025-01-01",
            last_update="2025-10-01", current_priority=0, episode_priority={},
        )
        self.f.subscribechain.subscription_repository.get.return_value = self.tv
        self.media = self.f.mediachain.recognize_media.return_value
        self.media.release_date = "2025-01-01"
        self.library = Mock()
        self.library.iteminfo.return_value = types.SimpleNamespace(
            media_source=MediaSource.TMDB.value, media_id="123"
        )
        self.f.mediachain.media_exists.return_value = types.SimpleNamespace(
            type=MediaType.TV, server="Emby", itemid="42", seasons={2: [1, 2]},
        )
        self.series = {"id": 123, "status": "Ended", "in_production": False,
                       "last_episode_to_air": {"season_number": 2, "episode_number": 2, "air_date": "2025-09-01"},
                       "seasons": [{"season_number": 2, "episode_count": 2}]}
        self.season = {"season_number": 2, "episodes": [
            {"episode_number": 1, "air_date": "2025-08-01"},
            {"episode_number": 2, "air_date": "2025-09-01"},
        ]}
        self.f.mediachain.tmdb_info.side_effect = lambda _, t, season=None: self.season if season is not None else self.series

    def ready(self):
        return MOD.ready_to_finish(self.tv, self.media, self.library, self.f.mediachain, self.now.date())

    def test_requires_confirmed_ending_and_exact_library_coverage(self):
        self.assertTrue(self.ready())
        self.series["status"] = "Returning Series"
        self.assertFalse(self.ready())
        self.series["last_episode_to_air"]["season_number"] = 3
        self.assertTrue(self.ready())
        self.series["next_episode_to_air"] = {"season_number": 2, "episode_number": 3}
        self.assertFalse(self.ready())
        del self.series["next_episode_to_air"]
        self.f.mediachain.media_exists.return_value.seasons = {2: [1], 3: [2]}
        self.assertFalse(self.ready())
        self.f.mediachain.media_exists.return_value.seasons = {2: [1, 2]}
        self.library.iteminfo.return_value.media_id = "999"
        self.assertFalse(self.ready())
        self.library.iteminfo.return_value.media_id = "123"
        self.series["seasons"][0]["episode_count"] = 3
        self.assertFalse(self.ready())

    def test_movie_checks_release_and_media_server_identity(self):
        self.tv.type = MediaType.MOVIE.value
        self.media.type = MediaType.MOVIE
        self.f.mediachain.media_exists.return_value.type = MediaType.MOVIE
        self.assertTrue(self.ready())
        self.media.release_date = "2099-01-01"
        self.assertFalse(self.ready())
        self.media.release_date = "2025-01-01"
        self.f.mediachain.media_exists.return_value = None
        self.assertFalse(self.ready())

    def test_native_guard_blocks_owned_incomplete_and_ignores_others(self):
        def event(subscribe, cancel=False):
            return types.SimpleNamespace(event_data=types.SimpleNamespace(
                subscribe=subscribe, mediainfo=self.media, cancel=cancel, source="other", reason="prior veto",
            ))

        with patch.object(MOD, "MediaServerChain", return_value=self.library):
            good = event(self.tv)
            self.plugin.check_subscription_completion(good)
            self.assertFalse(good.event_data.cancel)
            self.f.mediachain.media_exists.return_value = None
            missing = event(self.tv)
            self.plugin.check_subscription_completion(missing)
            self.assertTrue(missing.event_data.cancel)
            other = copy.copy(self.tv)
            other.username = "manual"
            untouched = event(other)
            self.plugin.check_subscription_completion(untouched)
            self.assertFalse(untouched.event_data.cancel)
            vetoed = event(self.tv, cancel=True)
            self.plugin.check_subscription_completion(vetoed)
            self.assertEqual(vetoed.event_data.source, "other")

    def test_deadline_starts_only_when_ready_and_refreshes_after_download(self):
        self.f.subscribechain.is_best_version_complete.return_value = False
        self.tv.last_update = (self.now - datetime.timedelta(days=1)).isoformat()
        finished = self.f.subscribechain._SubscribeChain__finish_subscribe
        with patch.object(MOD, "list_subscriptions", return_value=types.SimpleNamespace(
                items=[self.tv], has_next=False)), patch.object(MOD, "MediaServerChain", return_value=self.library), \
                patch.object(self.f.subscribechain.subscription_repository, "get", return_value=self.tv) as read:
            self.plugin.check_wash_deadlines()
            record = self.f.store["wash_ready_since"]["4"]
            self.assertGreaterEqual(datetime.datetime.fromisoformat(record["since"]), self.now)
            record["since"] = (self.now - datetime.timedelta(days=46)).isoformat()
            self.plugin.check_wash_deadlines()
            finished.assert_not_called()
            self.tv.last_update = (self.now - datetime.timedelta(days=46)).isoformat()
            read.side_effect = [self.tv, self.tv, None]
            self.plugin.check_wash_deadlines()
            finished.assert_called_once()
            self.assertEqual(self.f.store["wash_ready_since"], {})

    def test_expired_wash_clock_survives_missing_provider_then_finishes(self):
        self.f.subscribechain.is_best_version_complete.return_value = False
        record = {"scope": ["2025-01-01", "themoviedb", "123", MediaType.TV.value, 2, 2, "episode_wash_days"],
                  "since": "2020-01-01T00:00:00"}
        self.f.store["wash_ready_since"] = {"4": record}
        present = self.f.mediachain.media_exists.return_value
        self.f.mediachain.media_exists.return_value = None
        with patch.object(MOD, "list_subscriptions", return_value=types.SimpleNamespace(
                items=[self.tv], has_next=False)), patch.object(MOD, "MediaServerChain", return_value=self.library):
            self.plugin.check_wash_deadlines()
            self.assertEqual(self.f.store["wash_ready_since"], {"4": record})
            self.f.subscribechain._SubscribeChain__finish_subscribe.assert_not_called()
            self.f.subscribechain.sync_subscription_delete_scope.assert_not_called()
            self.f.mediachain.media_exists.return_value = present
            self.f.subscribechain.subscription_repository.get.side_effect = [self.tv, self.tv, None]
            self.plugin.check_wash_deadlines()
        self.f.subscribechain._SubscribeChain__finish_subscribe.assert_called_once()

    def test_unfulfilled_expiration_removes_active_only(self):
        self.f.mediachain.media_exists.return_value = None
        self.f.store["history"] = [{"title": "Example"}]
        delete_scope = self.f.subscribechain.sync_subscription_delete_scope
        context = MagicMock()
        context.__enter__.return_value.execute.return_value = True
        delete_scope.return_value = context
        with patch.object(MOD, "list_subscriptions", return_value=types.SimpleNamespace(
                items=[self.tv], has_next=False)), patch.object(MOD, "MediaServerChain", return_value=self.library):
            self.plugin.check_wash_deadlines()
        context.__enter__.return_value.execute.assert_called_once_with(4, types.SimpleNamespace(
            username=self.plugin.plugin_name, is_superuser=False))
        self.assertEqual(self.f.store["history"], [{"title": "Example"}])
        self.assertEqual(self.f.store["subscription_expirations"]["4"]["status"], "removed")
        self.f.subscribechain._SubscribeChain__finish_subscribe.assert_not_called()

    def test_mid_season_break_overrides_ended_or_later_season(self):
        self.season["episodes"][-1]["episode_type"] = "mid_season"
        self.assertFalse(self.ready())
        self.series["last_episode_to_air"]["season_number"] = 3
        self.assertFalse(self.ready())
        self.season["episodes"][-1]["episode_type"] = "finale"
        self.assertTrue(self.ready())

    def test_time_limit_defaults_and_zero(self):
        config = self.plugin.get_form()[1]
        for key, days in {"movie_subscription_days": 360, "full_subscription_days": 180,
                          "episode_subscription_days": 90, "movie_wash_days": 180,
                          "full_wash_days": 90, "episode_wash_days": 45}.items():
            self.assertEqual(config[key], days)
        self.plugin._subscription_days["episode_subscription_days"] = 0
        self.f.mediachain.media_exists.return_value = None
        with patch.object(MOD, "list_subscriptions", return_value=types.SimpleNamespace(
                items=[self.tv], has_next=False)), patch.object(MOD, "MediaServerChain", return_value=self.library), \
                patch.object(MOD, "recover_failed_downloads", autospec=True) as recover:
            self.plugin.check_wash_deadlines()
        recover.assert_called_once_with(self.f.subscribechain, self.tv, self.media, self.f.mediachain, None)
        self.f.subscribechain.sync_subscription_delete_scope.assert_not_called()

    def test_unlimited_preserves_native_completion_guard(self):
        self.assertFalse(MOD.wash_due("2020-01-01", None, 0, self.now))
        self.plugin._wash_days["episode_wash_days"] = 0
        self.f.subscribechain.is_best_version_complete.return_value = True
        with patch.object(MOD, "list_subscriptions", return_value=types.SimpleNamespace(
                items=[self.tv], has_next=False)), patch.object(MOD, "MediaServerChain", return_value=self.library), \
                patch.object(self.f.subscribechain.subscription_repository, "get", side_effect=[self.tv, self.tv, None]):
            self.plugin.check_wash_deadlines()
        self.f.subscribechain._SubscribeChain__finish_subscribe.assert_called_once()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        @dataclass
        class Row:
            id: int = 4
            username: str = "owned"
            date: str = "2025-01-01"
            type: str = MediaType.TV.value
            episode_group: str | None = None
            season: int = 2
            start_episode: int = 1
            total_episode: int = 3
            best_version_full: bool = False
            media_source: str = "themoviedb"
            media_id: str = "123"
            current_priority: int = 100
            episode_priority: dict | None = None
            note: list | None = None

        self.sub = Row(episode_priority={"1": 100, "2": 100, "3": 100}, note=[1, 2, 3])
        self.media = types.SimpleNamespace(media_source="themoviedb", media_id="123")
        self.chain = Mock()
        self.chain.parse_subscribe_source_keyword.return_value = {"id": 4, "season": 2}
        self.chain.subscription_repository.get.return_value = self.sub
        self.mutation = MagicMock()
        self.chain.sync_subscription_mutation_scope.return_value = self.mutation
        self.media_chain = Mock()
        self.service = Mock(type="qbittorrent")
        self.service.instance.get_torrents.return_value = ([], False)
        self.library = Mock(type="emby")
        self.library.instance.get_user.return_value = "library-user"
        self.media_chain.media_exists.return_value = None

    def run_recovery(self, rows, missing, transfers=()):
        from contextlib import ExitStack
        recovery = MOD.recovery
        with ExitStack() as stack:
            stack.enter_context(patch.object(recovery, "DownloaderHelper", return_value=Mock(
                get_service=Mock(return_value=self.service))))
            stack.enter_context(patch.object(recovery, "MediaServerHelper", return_value=Mock(
                get_services=Mock(return_value={"main": self.library}))))
            stack.enter_context(patch.object(recovery, "list_download_history", return_value=types.SimpleNamespace(
                items=rows, has_next=False)))
            stack.enter_context(patch.object(recovery, "list_transfer_history", return_value=types.SimpleNamespace(
                items=transfers, has_next=False)))
            MOD.recover_failed_downloads(self.chain, self.sub, self.media, self.media_chain, missing)
        return self.mutation.__enter__.return_value.update

    def download(self, episodes="E01-E03", hash_="hash", note=None):
        return types.SimpleNamespace(date="2025-03-01", seasons="S02", episodes=episodes,
                                     note={"source": "Subscribe|{}", **(note or {})}, downloader="qb",
                                     download_hash=hash_)

    def test_legacy_pack_recovers_only_missing_failed_episodes(self):
        transferred = types.SimpleNamespace(seasons="S02", episodes="E02")
        update = self.run_recovery([self.download()], {2, 3}, [transferred])
        self.assertEqual(update.call_args.args[1], {"episode_priority": {"1": 100, "2": 100, "3": 0},
                                                   "note": [1, 2], "current_priority": 0, "lack_episode": 1})

    def test_modern_history_and_an_alternative_still_downloading(self):
        self.service.instance.get_torrents.side_effect = [([], False), ([{"state": "downloading"}], False)]
        modern = {"subscription_download": {"subscription_id": 4, "best_version_full": False,
                                            "initial_episodes": [3]}}
        update = self.run_recovery([self.download(hash_="new", note=modern), self.download("E03")], {3})
        update.assert_not_called()

    def test_failed_movie_can_recover_but_outage_cannot_clear_priority(self):
        self.sub = replace(self.sub, type=MediaType.MOVIE.value, season=None)
        self.chain.subscription_repository.get.return_value = self.sub
        self.chain.parse_subscribe_source_keyword.return_value = {"id": 4, "season": None}
        self.library.instance.get_user.return_value = None
        self.run_recovery([self.download()], None).assert_not_called()
        self.library.instance.get_user.return_value = "library-user"
        self.service.instance.get_torrents.return_value = ([], True)
        self.run_recovery([self.download()], None).assert_not_called()
        self.service.instance.get_torrents.return_value = ([], False)
        update = self.run_recovery([self.download()], None)
        self.assertEqual(update.call_args.args[1], {"current_priority": 0})


class ContractTests(unittest.TestCase):
    def test_version_api_models_auth_and_clone_paths(self):
        self.assertEqual(MOD.DoubanRankPlusOptimized.plugin_version, "2.2.1")
        f = fixture()
        apis = {api["path"]: api for api in f.plugin.get_api()}
        self.assertEqual(apis["/delete_history"]["auth"], "bear")
        self.assertEqual(apis["/retry_history"]["auth"], "bear")
        self.assertEqual(apis["/migrate-history"]["auth"], "apikey")
        for api in apis.values():
            self.assertIn("response_model", api)
        self.assertEqual(f.plugin._plugin_id, "DoubanRankPlusOptimized")
        source = (ROOT / "plugins.v3/doubanrankplusoptimized/__init__.py").read_text(encoding="utf-8")
        for forbidden in ("app.core", "app.utils", "app.log", "app.plugins", "app.modules.douban.apiv2",
                          "SessionFactory", "tmdbid=mediainfo"):
            self.assertNotIn(forbidden, source)

    def test_native_route_registration_models_without_http_client(self):
        from fastapi import FastAPI
        f = fixture()
        app = FastAPI()
        for api in f.plugin.get_api():
            route = dict(api)
            route.pop("auth", None)
            route.pop("summary", None)
            app.add_api_route(**route)
        schema = app.openapi()["paths"]
        for path in ("/delete_history", "/retry_history", "/migrate-history", "/migrate-config"):
            self.assertIn(path, schema)
        self.assertIn("application/json", schema["/retry_history"]["post"]["responses"]["200"]["content"])

    def test_empty_reinit_one_shot_and_stop(self):
        f = fixture()
        f.plugin.init_plugin({"enabled": True, "proxy": True, "rss_addrs": "https://example.invalid/rss"})
        self.assertTrue(f.plugin.get_state())
        f.plugin.init_plugin(None)
        self.assertFalse(f.plugin.get_state())
        self.assertEqual(f.plugin.get_service(), [])
        f.plugin.update_config = Mock()
        f.plugin.init_plugin({"enabled": True, "clear": True, "onlyonce": True})
        saved = f.plugin.update_config.call_args.args[0]
        self.assertFalse(saved["onlyonce"])
        self.assertFalse(saved["clear"])
        f.plugin.stop_service()
        self.assertTrue(f.plugin._event.is_set())


if __name__ == "__main__":
    unittest.main()
