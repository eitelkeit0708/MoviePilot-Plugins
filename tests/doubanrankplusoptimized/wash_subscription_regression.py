"""Run: python -m unittest discover -s tests/doubanrankplusoptimized -p '*_regression.py' -v

Runs the real RSS-to-subscription workflow with offline MoviePilot adapters.
MP V2 contract checked at 1528176beacc0f9f8d8d9cbf1adf048b0da67b5c:
best_version=1 enables upgrades; best_version_full=0 selects per-episode upgrades;
exists() uses meta.begin_season, and add() returns (subscription_id, message).
"""

import copy
import importlib.util
import re
import sys
import types
import unittest
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"
    UNKNOWN = "未知"


class MetaInfo:
    def __init__(self, title):
        match = re.search(r"S(\d+)", title)
        self.begin_season = int(match[1]) if match else None
        self.type = MediaType.TV if match else MediaType.MOVIE
        self.year = None


def load_plugin():
    exports = {
        "apscheduler.schedulers.background": {"BackgroundScheduler": Mock},
        "apscheduler.triggers.cron": {"CronTrigger": Mock},
        "app.schemas": {"Response": types.SimpleNamespace},
        "app.schemas.types": {"MediaType": MediaType},
        "app.core.context": {"MediaInfo": types.SimpleNamespace},
        "app.core.meta.metabase": {"MetaBase": MetaInfo},
        "app.core.metainfo": {"MetaInfo": MetaInfo},
        "app.core.config": {"settings": types.SimpleNamespace(TZ="Asia/Hong_Kong", API_TOKEN="test-token")},
        "app.chain.download": {"DownloadChain": Mock},
        "app.chain.media": {"MediaChain": Mock},
        "app.chain.subscribe": {"SubscribeChain": Mock},
        "app.log": {"logger": Mock()},
        "app.plugins": {"_PluginBase": type("_PluginBase", (), {})},
        "app.utils.dom": {"DomUtils": Mock},
        "app.utils.http": {"RequestUtils": Mock},
        "app.modules.douban.apiv2": {"DoubanApi": Mock},
    }
    modules = {}
    for name, attributes in exports.items():
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            parent = ".".join(parts[:end])
            modules.setdefault(parent, types.ModuleType(parent))
        modules[name].__dict__.update(attributes)
    spec = importlib.util.spec_from_file_location(
        "doubanrankplusoptimized_under_test",
        ROOT / "plugins.v2/doubanrankplusoptimized/__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


MOD = load_plugin()


def fixture(mtype=MediaType.TV, title="Example S02 Show", all_seasons=True,
            existing_seasons=(), library_exists=True):
    plugin = MOD.DoubanRankPlusOptimized()
    plugin._event = Event()
    plugin._task_lock = Lock()
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
    plugin.downloadchain = Mock()
    plugin.downloadchain.get_no_exists_info.return_value = (
        library_exists, {} if library_exists else {123: {2: {}}}
    )
    plugin.mediachain = Mock()
    plugin.mediachain.recognize_media.return_value = types.SimpleNamespace(
        title="Example", title_year="Example (2026)", year="2026",
        type=mtype, tmdb_id=123, number_of_seasons=3 if mtype == MediaType.TV else None,
        genre_ids=[], vote_average=8.0, overview="Example overview",
        season_info=[{"season_number": i, "episode_count": 10, "air_date": "2026-01-01"} for i in range(4)],
        seasons={i: list(range(1, 11)) for i in range(4)},
        season_years={i: "2026" for i in range(4)},
        get_poster_image=lambda: "poster.jpg",
    )
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
    return types.SimpleNamespace(plugin=plugin, rows=rows, store=store)


class WashSubscriptionTests(unittest.TestCase):
    def run_refresh(self, f):
        f.plugin._DoubanRankPlusOptimized__start_task()
        # The plugin catches RSS exceptions; insist the workflow reached history.
        self.assertEqual(len(f.store.get("history", [])), 1)
        return f.store["history"][0]["status"]

    def assert_wash(self, row):
        self.assertEqual(row["best_version"], 1)
        self.assertEqual(row["best_version_full"], 0)
        self.assertIs(type(row["best_version"]), int)
        self.assertIs(type(row["best_version_full"]), int)

    def test_movie_in_library_can_be_upgraded(self):
        f = fixture(MediaType.MOVIE, title="Example Movie")
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {None})
        self.assert_wash(f.rows[None])
        self.assertEqual(f.rows[None]["save_path"], "/movies")
        f.plugin.downloadchain.get_no_exists_info.assert_not_called()

    def test_all_seasons_in_library_get_per_episode_upgrades(self):
        f = fixture()
        self.run_refresh(f)
        self.assertEqual(set(f.rows), {1, 2, 3})
        for row in f.rows.values():
            self.assert_wash(row)
            self.assertEqual(row["save_path"], "/tv")
        f.plugin.downloadchain.get_no_exists_info.assert_not_called()
        meta = f.plugin.mediachain.recognize_media.call_args.kwargs["meta"]
        self.assertEqual(meta.begin_season, 2)

    def test_existing_rss_season_does_not_block_other_seasons(self):
        f = fixture(existing_seasons=(2,))
        original = copy.deepcopy(f.rows[2])
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(set(f.rows), {1, 2, 3})
        self.assertEqual(f.rows[2], original)
        self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [1, 3])

    def test_existing_first_season_does_not_block_later_seasons(self):
        f = fixture(title="Example", existing_seasons=(1,))
        self.run_refresh(f)
        self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [2, 3])

    def test_only_selected_season_when_all_seasons_disabled(self):
        f = fixture(all_seasons=False)
        self.run_refresh(f)
        self.assertEqual(set(f.rows), {2})
        self.assert_wash(f.rows[2])

    def test_unspecified_season_defaults_to_one_for_deduplication(self):
        f = fixture(title="Example", all_seasons=False, existing_seasons=(1,))
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_EXISTS.value)
        f.plugin.subscribechain.add.assert_not_called()

    def test_special_season_zero_is_preserved(self):
        f = fixture(title="Example S00 Specials", all_seasons=False)
        self.run_refresh(f)
        self.assertEqual(set(f.rows), {0})
        self.assert_wash(f.rows[0])

    def test_missing_library_content_also_uses_wash_subscriptions(self):
        f = fixture(library_exists=False)
        self.run_refresh(f)
        self.assertEqual(set(f.rows), {1, 2, 3})
        for row in f.rows.values():
            self.assert_wash(row)

    def test_year_and_rating_filters_remain_effective(self):
        for key, value, status in [
            ("_release_year", 2027, MOD.Status.YEAR_NOT_MATCH),
            ("_vote", 9, MOD.Status.RATING_NOT_MATCH),
        ]:
            with self.subTest(filter=key):
                f = fixture()
                setattr(f.plugin, key, value)
                self.assertEqual(self.run_refresh(f), status.value)
                f.plugin.subscribechain.add.assert_not_called()

    def test_failed_creation_is_not_reported_as_success(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kwargs: (None, "未获取到总集数")
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_FAILED.value)
        self.assertFalse(f.rows)

    def test_history_prevents_recreating_completed_subscriptions(self):
        f = fixture()
        self.run_refresh(f)
        f.rows.clear()
        f.plugin.subscribechain.add.reset_mock()
        self.run_refresh(f)
        f.plugin.subscribechain.add.assert_not_called()


class RetryWorkflowTests(unittest.TestCase):
    run_refresh = WashSubscriptionTests.run_refresh

    def record(self, f):
        return f.store["history"][0]

    def test_failed_subscription_retries_three_times_then_stops(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "未获取到总集数")
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            for attempt, delay in enumerate((900, 3600, 21600, None), 1):
                self.run_refresh(f)
                retry = self.record(f)["season_results"]["2"]["retry"]
                self.assertEqual(retry["attempts"], attempt)
                self.assertEqual(f.plugin.subscribechain.add.call_count, attempt)
                if delay is not None:
                    self.assertEqual(retry["next_retry_at"], clock.return_value + delay)
                    self.run_refresh(f)
                    self.assertEqual(f.plugin.subscribechain.add.call_count, attempt)
                    clock.return_value = retry["next_retry_at"]
                else:
                    self.assertIsNone(retry["next_retry_at"])
            clock.return_value += 999999
            self.run_refresh(f)
            self.assertEqual(f.plugin.subscribechain.add.call_count, 4)

    def test_partial_seasons_persist_and_only_failed_season_retries(self):
        f = fixture()
        add = f.plugin.subscribechain.add.side_effect

        def fail_second(**kw):
            if kw["season"] == 2:
                self.assertEqual(self.record(f)["season_results"]["1"]["status"], MOD.Status.SUBSCRIPTION_ADDED.value)
                raise RuntimeError("temporary failure")
            return add(**kw)

        f.plugin.subscribechain.add.side_effect = fail_second
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.assertEqual(self.run_refresh(f), MOD.Status.PARTIAL_SUCCESS.value)
            self.assertEqual(self.record(f)["summary"], "成功 2 季，失败 1 季，跳过 0 季")
            self.assertEqual(set(f.rows), {1, 3})
            # A completed native subscription may have disappeared before the retry.
            del f.rows[1]
            f.plugin.subscribechain.add.reset_mock()
            f.plugin.subscribechain.add.side_effect = add
            clock.return_value = 1900
            self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [2])
            self.assertEqual(set(f.rows), {2, 3})
            self.assertNotIn("retry", self.record(f)["season_results"]["2"])

    def test_recognition_retry_survives_item_leaving_rss_and_restart(self):
        f = fixture(all_seasons=False)
        media = f.plugin.mediachain.recognize_media.return_value
        f.plugin.mediachain.recognize_media.return_value = None
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.assertEqual(self.run_refresh(f), MOD.Status.UNRECOGNIZED.value)
            saved = copy.deepcopy(f.store["history"])
            restarted = fixture(all_seasons=False)
            restarted.store["history"] = saved
            restarted.plugin._DoubanRankPlusOptimized__get_rss_info.return_value = []
            restarted.plugin.mediachain.recognize_media.return_value = media
            clock.return_value = 1900
            self.assertEqual(self.run_refresh(restarted), MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual(set(restarted.rows), {2})

    def test_one_bad_item_does_not_abort_later_items(self):
        f = fixture(all_seasons=False)
        rss = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value
        rss.append(dict(rss[0], title="Another S03 Show", doubanid="789"))
        media = f.plugin.mediachain.recognize_media.return_value
        f.plugin.mediachain.recognize_media.side_effect = [RuntimeError("bad item"), media]
        f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(len(f.store["history"]), 2)
        self.assertEqual([r["status"] for r in f.store["history"]],
                         [MOD.Status.PROCESS_FAILED.value, MOD.Status.SUBSCRIPTION_ADDED.value])
        self.assertEqual(set(f.rows), {3})

    def test_invalid_rating_and_year_defer_without_creating(self):
        cases = [("year", None, "_release_year", 2020, MOD.Status.YEAR_UNKNOWN),
                 ("year", "unknown", "_release_year", 2020, MOD.Status.YEAR_UNKNOWN),
                 ("vote_average", None, "_vote", 7, MOD.Status.RATING_UNKNOWN),
                 ("vote_average", float("nan"), "_vote", 7, MOD.Status.RATING_UNKNOWN),
                 ("vote_average", 0, "_vote", 7, MOD.Status.RATING_UNKNOWN)]
        for field, value, setting, minimum, expected in cases:
            with self.subTest(field=field, value=value):
                f = fixture(all_seasons=False)
                setattr(f.plugin.mediachain.recognize_media.return_value, field, value)
                setattr(f.plugin, setting, minimum)
                self.assertEqual(self.run_refresh(f), expected.value)
                f.plugin.subscribechain.add.assert_not_called()
                self.assertEqual(self.record(f)["season_results"]["2"]["retry"]["attempts"], 1)

    def test_removed_source_does_not_retry_saved_item(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed")
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.run_refresh(f)
            f.plugin._rss_addrs = ["https://example.invalid/other"]
            f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value = []
            clock.return_value = 1900
            self.run_refresh(f)
            self.assertEqual(f.plugin.subscribechain.add.call_count, 1)
            response = f.plugin.retry_history({"key": self.record(f)["unique"], "apikey": "test-token"})
            self.assertFalse(response.success)

    def test_legacy_success_is_preserved_and_legacy_failure_can_retry(self):
        for status in (MOD.Status.SUBSCRIPTION_ADDED, MOD.Status.UNRECOGNIZED):
            with self.subTest(status=status):
                f = fixture(all_seasons=False)
                item = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]
                key = f.plugin._DoubanRankPlusOptimized__unique(item)
                old = dict(unique=key, title=item["title"], status=status.value, legacy_field="preserved")
                f.store["history"] = [copy.deepcopy(old)]
                self.run_refresh(f)
                self.assertEqual(self.record(f)["legacy_field"], "preserved")
                if status == MOD.Status.SUBSCRIPTION_ADDED:
                    self.assertEqual(self.record(f), old)
                    f.plugin.subscribechain.add.assert_not_called()
                else:
                    self.assertEqual(set(f.rows), {2})

    def test_same_item_in_two_feeds_has_one_attempt_per_run(self):
        f = fixture(all_seasons=False)
        f.plugin._rss_addrs.append("https://example.invalid/other")
        f.plugin.mediachain.recognize_media.return_value = None
        self.run_refresh(f)
        self.assertEqual(f.plugin.mediachain.recognize_media.call_count, 1)

    def test_manual_retry_resets_failure_budget_and_preserves_successes(self):
        f = fixture()
        add = f.plugin.subscribechain.add.side_effect
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed") if kw["season"] == 2 else add(**kw)
        self.run_refresh(f)
        successful = copy.deepcopy(self.record(f)["season_results"]["1"])
        self.record(f)["season_results"]["2"]["retry"] = {"attempts": 4, "next_retry_at": None}
        f.plugin._scheduler = Mock(running=False)
        response = f.plugin.retry_history({"key": self.record(f)["unique"], "apikey": "test-token"})
        self.assertTrue(response.success)
        self.assertEqual(self.record(f)["season_results"]["1"], successful)
        self.assertEqual(self.record(f)["season_results"]["2"]["retry"]["attempts"], 0)
        f.plugin._scheduler.start.assert_called_once()
        job = f.plugin._scheduler.add_job.call_args.args[0]
        f.plugin.subscribechain.add.side_effect = add
        f.plugin.subscribechain.add.reset_mock()
        job(**f.plugin._scheduler.add_job.call_args.kwargs["kwargs"])
        self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [2])
        self.assertNotIn("manual_retry", self.record(f))

    def test_manual_retry_of_filtered_item_uses_updated_filters(self):
        f = fixture(all_seasons=False)
        f.plugin._vote = 9
        self.assertEqual(self.run_refresh(f), MOD.Status.RATING_NOT_MATCH.value)
        f.plugin._vote = 7
        response = f.plugin.retry_history({"key": self.record(f)["unique"], "apikey": "test-token"})
        self.assertTrue(response.success)
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)

    def test_bad_token_and_running_task_cannot_mutate_retry_or_delete(self):
        f = fixture(all_seasons=False)
        f.plugin.mediachain.recognize_media.return_value = None
        self.run_refresh(f)
        old = copy.deepcopy(f.store)
        key = self.record(f)["unique"]
        self.assertFalse(f.plugin.retry_history({"key": key, "apikey": "bad-token"}).success)
        with f.plugin._task_lock:
            self.assertFalse(f.plugin.retry_history({"key": key, "apikey": "test-token"}).success)
            self.assertFalse(f.plugin.delete_history(key, "test-token").success)
            f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(f.store, old)
        self.assertEqual(f.plugin.mediachain.recognize_media.call_count, 1)

    def test_changed_identity_does_not_mix_season_results(self):
        f = fixture(all_seasons=False)
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed")
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.run_refresh(f)
            f.plugin.mediachain.recognize_media.return_value.tmdb_id = 999
            clock.return_value = 1900
            self.assertEqual(self.run_refresh(f), MOD.Status.PROCESS_FAILED.value)
            self.assertEqual(f.plugin.subscribechain.add.call_count, 1)
            self.assertEqual(self.record(f)["identity"]["tmdbid"], "123")

    def test_history_card_shows_seasons_and_retry_action(self):
        f = fixture()
        add = f.plugin.subscribechain.add.side_effect
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed") if kw["season"] == 2 else add(**kw)
        self.run_refresh(f)
        card = f.plugin._DoubanRankPlusOptimized__get_history_post_content(self.record(f))
        text = " ".join(item.get("text", "") for item in card["content"])
        self.assertIn("成功 2 季，失败 1 季", text)
        self.assertIn("第 2 季：添加订阅失败", text)
        self.assertIn("下次重试", text)
        button = next(item for item in card["content"] if item.get("text") == "重新处理")
        self.assertEqual(button["events"]["click"]["method"], "post")

    def test_manual_action_only_processes_requested_item_and_accepts_json_body(self):
        from fastapi import FastAPI
        f = fixture(all_seasons=False)
        add = f.plugin.subscribechain.add.side_effect
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed")
        self.run_refresh(f)
        original = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]
        f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value = [dict(original, title="Other S03 Show", doubanid="789")]
        f.plugin._scheduler = Mock(running=False)
        event = f.plugin._DoubanRankPlusOptimized__get_history_post_content(self.record(f))["content"][-1]["events"]["click"]
        app = FastAPI()
        app.add_api_route("/retry_history", f.plugin.retry_history, methods=["POST"])
        operation = app.openapi()["paths"]["/retry_history"]["post"]
        self.assertIn("application/json", operation["requestBody"]["content"])
        self.assertNotIn("parameters", operation)
        self.assertTrue(f.plugin.retry_history(event["params"]).success)
        f.plugin.subscribechain.add.side_effect = add
        f.plugin.subscribechain.add.reset_mock()
        job = f.plugin._scheduler.add_job.call_args
        job.args[0](**job.kwargs["kwargs"])
        self.assertEqual(set(f.rows), {2})
        self.assertEqual(len(f.store["history"]), 1)

    def test_metadata_exception_after_partial_failure_preserves_successes(self):
        f = fixture()
        add = f.plugin.subscribechain.add.side_effect
        f.plugin.subscribechain.add.side_effect = lambda **kw: (None, "failed") if kw["season"] == 2 else add(**kw)
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.run_refresh(f)
            f.plugin.mediachain.recognize_media.side_effect = RuntimeError("temporary recognition failure")
            clock.return_value = 1900
            self.assertEqual(self.run_refresh(f), MOD.Status.PROCESS_FAILED.value)
            self.assertEqual(self.record(f)["season_results"]["1"]["status"], MOD.Status.SUBSCRIPTION_ADDED.value)
            f.plugin.mediachain.recognize_media.side_effect = None
            f.plugin.subscribechain.add.side_effect = add
            f.plugin.subscribechain.add.reset_mock()
            clock.return_value = 2800
            self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [2])


class IntakeOptimizationTests(unittest.TestCase):
    run_refresh = WashSubscriptionTests.run_refresh
    record = RetryWorkflowTests.record

    def test_renamed_douban_subject_does_not_repeat_recognition_or_create(self):
        f = fixture(all_seasons=False)
        self.run_refresh(f)
        old = copy.deepcopy(f.store["history"])
        f.rows.clear()
        f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0].update(title="New name S02", year="2027")
        self.run_refresh(f)
        self.assertEqual(f.plugin.mediachain.recognize_media.call_count, 1)
        self.assertEqual(f.store["history"], old)

    def test_legacy_title_key_is_preserved_after_subject_is_renamed(self):
        for status in (MOD.Status.SUBSCRIPTION_ADDED, MOD.Status.UNRECOGNIZED):
            with self.subTest(status=status):
                f = fixture(all_seasons=False)
                item = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]
                key = f.plugin._DoubanRankPlusOptimized__legacy_unique(item)
                old = dict(unique=key, title=item["title"], status=status.value, legacy_field="kept")
                f.store["history"] = [copy.deepcopy(old)]
                item["title"] = "New name S02"
                if status == MOD.Status.UNRECOGNIZED:
                    # Even an old card with no saved RSS can be manually retried after a rename.
                    f.plugin._scheduler = Mock(running=False)
                    self.assertTrue(f.plugin.retry_history({"key": key, "apikey": "test-token"}).success)
                    f.plugin._DoubanRankPlusOptimized__start_task(retry_only=True)
                    self.assertEqual(set(f.rows), {2})
                else:
                    self.run_refresh(f)
                    f.plugin.subscribechain.add.assert_not_called()
                    self.assertEqual(self.record(f), old)
                self.assertEqual(len(f.store["history"]), 1)
                self.assertEqual(self.record(f)["unique"], key)
                self.assertEqual(self.record(f)["legacy_field"], "kept")

    def test_different_subjects_same_target_do_not_recreate_within_run(self):
        f = fixture(all_seasons=False)
        rss = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value
        rss.append(dict(rss[0], title="Alias S02", doubanid="789"))
        # Simulate MP removing a completed subscription before the next RSS item.
        f.plugin.subscribechain.add.side_effect = lambda **kw: (1, "created")
        f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(f.plugin.subscribechain.add.call_count, 1)
        self.assertEqual(len(f.store["history"]), 2)
        self.assertEqual(f.store["history"][1]["status"], MOD.Status.SUBSCRIPTION_EXISTS.value)

    def test_canonical_dedup_survives_restart_and_keeps_seasons_separate(self):
        first = fixture(all_seasons=False)
        self.run_refresh(first)
        restarted = fixture(all_seasons=False)
        restarted.store["history"] = copy.deepcopy(first.store["history"])
        rss = restarted.plugin._DoubanRankPlusOptimized__get_rss_info.return_value
        rss[0]["doubanid"] = "789"
        rss.append(dict(rss[0], title="Example S03 Show", doubanid="999"))
        restarted.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual([c.kwargs["season"] for c in restarted.plugin.subscribechain.add.call_args_list], [3])
        self.assertEqual(len(restarted.store["history"]), 3)

    def test_movie_and_tv_same_tmdb_number_are_distinct(self):
        f = fixture(MediaType.MOVIE, title="Example Movie")
        self.run_refresh(f)
        tv = fixture(all_seasons=False)
        tv.store["history"] = copy.deepcopy(f.store["history"])
        tv.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]["doubanid"] = "789"
        tv.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(set(tv.rows), {2})

    def test_no_douban_id_same_title_year_movie_and_tv_are_distinct(self):
        f = fixture(MediaType.MOVIE, title="Example", all_seasons=False)
        item = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]
        item.update(doubanid=None, mtype="")
        f.plugin._rss_addrs = ["https://example.invalid/movies@@Movie", "https://example.invalid/tv@@TV"]
        movie = f.plugin.mediachain.recognize_media.return_value
        tv = copy.copy(movie)
        tv.type = MediaType.TV
        f.plugin.mediachain.recognize_media.side_effect = [movie, tv]
        f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(set(f.rows), {None, 1})
        self.assertEqual(len(f.store["history"]), 2)

    def test_legacy_movie_canonical_target_is_preserved(self):
        f = fixture(MediaType.MOVIE, title="New alias")
        f.store["history"] = [dict(unique="old", type=MediaType.MOVIE.value, tmdbid="123",
                                  title="Old alias", status=MOD.Status.SUBSCRIPTION_ADDED.value)]
        f.plugin._DoubanRankPlusOptimized__start_task()
        f.plugin.subscribechain.add.assert_not_called()
        self.assertEqual(len(f.store["history"]), 2)

    def test_known_success_wins_over_legacy_duplicate_failure(self):
        f = fixture(all_seasons=False)
        f.store["history"] = [
            dict(unique="done_(DB:456)", status=MOD.Status.SUBSCRIPTION_ADDED.value),
            dict(unique="failed_(DB:456)", status=MOD.Status.UNRECOGNIZED.value),
        ]
        old = copy.deepcopy(f.store)
        f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(f.store, old)
        f.plugin.mediachain.recognize_media.assert_not_called()

    def test_actual_seasons_allow_gaps_exclude_specials_and_ignore_total(self):
        f = fixture(title="Example")
        media = f.plugin.mediachain.recognize_media.return_value
        media.season_info = []
        media.seasons = {0: [1], 1: [1, 2], 3: [1]}
        media.number_of_seasons = 99
        self.run_refresh(f)
        self.assertEqual(set(f.rows), {1, 3})

    def test_no_real_season_metadata_does_not_invent_season_one(self):
        f = fixture(title="Example")
        media = f.plugin.mediachain.recognize_media.return_value
        media.season_info = []
        media.seasons = {}
        self.assertEqual(self.run_refresh(f), MOD.Status.SEASON_UNKNOWN.value)
        f.plugin.subscribechain.add.assert_not_called()
        self.assertEqual(self.record(f)["retry"]["attempts"], 1)

    def test_empty_future_season_retries_independently_when_episodes_arrive(self):
        f = fixture()
        media = f.plugin.mediachain.recognize_media.return_value
        media.season_info[3].update(episode_count=0, air_date="2028-01-01")
        media.seasons[3] = []
        with patch.object(MOD.time, "time", return_value=1000) as clock:
            self.assertEqual(self.run_refresh(f), MOD.Status.PARTIAL_SUCCESS.value)
            self.assertEqual(set(f.rows), {1, 2})
            self.assertEqual(self.record(f)["season_results"]["3"]["status"], MOD.Status.SEASON_UNKNOWN.value)
            f.rows.clear()
            media.seasons[3] = [1, 2]
            # A newly announced season must not expand the frozen retry scope.
            media.seasons[4] = [1]
            clock.return_value = 1900
            f.plugin.subscribechain.add.reset_mock()
            self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
            self.assertEqual([c.kwargs["season"] for c in f.plugin.subscribechain.add.call_args_list], [3])

    def test_explicit_missing_season_does_not_subscribe_other_seasons(self):
        f = fixture(title="Example S09")
        self.assertEqual(self.run_refresh(f), MOD.Status.SEASON_UNKNOWN.value)
        f.plugin.subscribechain.add.assert_not_called()

    def test_type_year_and_missing_identity_conflicts_defer_creation(self):
        cases = [("type", MediaType.MOVIE), ("year", "1999"), ("tmdb_id", None),
                 ("type", MediaType.UNKNOWN)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                f = fixture(all_seasons=False)
                media = f.plugin.mediachain.recognize_media.return_value
                if field == "year":
                    media.season_years[2] = "1999"
                setattr(media, field, value)
                self.assertEqual(self.run_refresh(f), MOD.Status.IDENTITY_MISMATCH.value)
                f.plugin.subscribechain.add.assert_not_called()
                self.assertTrue(self.record(f)["error"])

    def test_later_season_year_can_differ_from_series_year(self):
        f = fixture(all_seasons=False)
        f.plugin.mediachain.recognize_media.return_value.year = "2020"
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        self.assertEqual(f.rows[2]["year"], "2020")

    def test_unknown_rss_type_does_not_force_tv_and_numeric_names_are_preserved(self):
        f = fixture(MediaType.MOVIE, title="  Movie   2  ")
        f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value[0]["mtype"] = "unknown"
        self.assertEqual(self.run_refresh(f), MOD.Status.SUBSCRIPTION_ADDED.value)
        clean = f.plugin._DoubanRankPlusOptimized__clean_title
        for title in ("罚罪2", "模范出租车3", "  Movie   2  ", "1899"):
            self.assertEqual(clean(title, MediaType.TV), " ".join(title.split()))

    def test_selected_anime_season_uses_anime_path_with_tv_fallback(self):
        for source, expected in [("https://example.invalid/rss;/movies#/tv#/anime", "/anime"),
                                 ("https://example.invalid/rss;/movies#/tv", "/tv")]:
            with self.subTest(source=source):
                f = fixture(all_seasons=False)
                f.plugin._rss_addrs = [source]
                f.plugin.mediachain.recognize_media.return_value.genre_ids = [16]
                self.run_refresh(f)
                self.assertEqual(f.rows[2]["save_path"], expected)

    def test_processing_interval_only_waits_between_due_items(self):
        f = fixture(all_seasons=False)
        rss = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value
        rss.append(dict(rss[0], title="Example S03", doubanid="789"))
        f.plugin._event = Mock()
        f.plugin._event.is_set.return_value = False
        f.plugin._event.wait.return_value = False
        f.plugin._min_sleep_time = f.plugin._max_sleep_time = 5
        with patch.object(MOD.time, "monotonic", return_value=100):
            f.plugin._DoubanRankPlusOptimized__start_task()
            f.plugin._DoubanRankPlusOptimized__start_task()
        self.assertEqual(set(f.rows), {2, 3})
        f.plugin._event.wait.assert_called_once_with(5)

    def test_stop_signal_interrupts_item_delay_without_failing_unstarted_item(self):
        f = fixture(all_seasons=False)
        rss = f.plugin._DoubanRankPlusOptimized__get_rss_info.return_value
        rss.append(dict(rss[0], title="Example S03", doubanid="789"))
        f.plugin._min_sleep_time = f.plugin._max_sleep_time = 5
        original_event = f.plugin._event

        def stop_in_wait(delay):
            f.plugin.stop_service()
            return True

        with patch.object(original_event, "wait", side_effect=stop_in_wait), patch.object(MOD.time, "monotonic", return_value=100):
            self.run_refresh(f)
        self.assertTrue(original_event.is_set())
        self.assertEqual(set(f.rows), {2})
        self.assertEqual(f.plugin.mediachain.recognize_media.call_count, 1)

    def test_sleep_configuration_is_bounded_and_reinitialization_clears_stop(self):
        parser = MOD.DoubanRankPlusOptimized._DoubanRankPlusOptimized__sleep_range
        for value in (None, "abc", "3", "-1,5", "10,3", "0,3601", "1.5,2"):
            self.assertEqual(parser(value), (3, 10))
        self.assertEqual(parser("0，0"), (0, 0))
        self.assertEqual(parser(" 3, 10 "), (3, 10))
        f = fixture()
        f.plugin.stop_service()
        self.assertTrue(f.plugin._event.is_set())
        f.plugin.init_plugin({"sleep_time": "bad"})
        self.assertFalse(f.plugin._event.is_set())
        self.assertEqual((f.plugin._min_sleep_time, f.plugin._max_sleep_time), (3, 10))


class RssRequestTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.f.plugin._event = Mock()
        self.f.plugin._event.is_set.return_value = False
        self.f.plugin._event.wait.return_value = False

    def response(self, status=200, text="<rss><channel/></rss>", headers=None):
        return types.SimpleNamespace(status_code=status, text=text, headers=headers or {}, close=Mock())

    def fetch(self):
        # Bypass only the fixture's feed stub, exercising real request and XML parsing.
        return MOD.DoubanRankPlusOptimized._DoubanRankPlusOptimized__get_rss_info(
            self.f.plugin, "https://example.invalid/rss")

    def test_timeout_then_503_then_success_has_bounded_waits_and_closes_responses(self):
        failure, success = self.response(503), self.response()
        with patch.object(MOD, "RequestUtils") as client:
            client.return_value.get_res.side_effect = [MOD.requests.exceptions.Timeout(), failure, success]
            self.assertEqual(self.fetch(), [])
            self.assertEqual(client.call_args.kwargs["timeout"], 20)
            self.assertEqual(client.return_value.get_res.call_count, 3)
        self.assertEqual([c.args[0] for c in self.f.plugin._event.wait.call_args_list], [2, 5])
        failure.close.assert_called_once()
        success.close.assert_called_once()

    def test_connection_failure_stops_after_three_attempts(self):
        with patch.object(MOD, "RequestUtils") as client:
            client.return_value.get_res.side_effect = MOD.requests.exceptions.ConnectionError()
            self.fetch()
            self.assertEqual(client.return_value.get_res.call_count, 3)

    def test_404_and_invalid_xml_are_not_retried(self):
        for response in (self.response(404), self.response(text="not XML")):
            with self.subTest(status=response.status_code), patch.object(MOD, "RequestUtils") as client:
                client.return_value.get_res.return_value = response
                self.assertEqual(self.fetch(), [])
                self.assertEqual(client.return_value.get_res.call_count, 1)
                response.close.assert_called_once()
        self.f.plugin._event.wait.assert_not_called()

    def test_rate_limit_retry_after_is_respected_or_left_for_next_refresh(self):
        for delay, calls in (("10", 2), ("600", 1), ("Wed, 07 Oct 2026 12:00:00 GMT", 1)):
            with self.subTest(delay=delay), patch.object(MOD, "RequestUtils") as client:
                self.f.plugin._event.wait.reset_mock()
                client.return_value.get_res.side_effect = [self.response(429, headers={"Retry-After": delay}), self.response()]
                self.fetch()
                self.assertEqual(client.return_value.get_res.call_count, calls)
                if calls == 2:
                    self.f.plugin._event.wait.assert_called_once_with(10)
                else:
                    self.f.plugin._event.wait.assert_not_called()

    def test_stop_during_http_backoff_prevents_next_request(self):
        self.f.plugin._event.wait.return_value = True
        with patch.object(MOD, "RequestUtils") as client:
            client.return_value.get_res.return_value = self.response(503)
            self.fetch()
            self.assertEqual(client.return_value.get_res.call_count, 1)

    def test_douban_ids_require_a_real_subject_link(self):
        parse_id = self.f.plugin._DoubanRankPlusOptimized__douban_subject_id
        for link in ("https://movie.douban.com/subject/123/?ref=test",
                     "https://www.douban.com/doubanapp/dispatch/movie/123"):
            self.assertEqual(parse_id(link), "123")
        for link in ("https://example.com/123/", "https://movie.douban.com/doulist/123/",
                     "https://movie.douban.com.evil.test/subject/123/", "https://movie.douban.com/subject/0/", "bad URL"):
            self.assertIsNone(parse_id(link))

    def test_real_xml_preserves_distinct_subjects_and_does_not_treat_list_id_as_subject(self):
        xml = '''<rss><channel>
        <item><title>Show S02</title><link>https://movie.douban.com/subject/123/</link><year>2026</year><type>tv</type></item>
        <item><title>Other show</title><link>https://example.com/123/</link></item>
        </channel></rss>'''

        def tag_value(item, name, default=""):
            tags = item.getElementsByTagName(name)
            return tags[0].firstChild.data if tags and tags[0].firstChild else default

        with patch.object(MOD, "RequestUtils") as client, patch.object(MOD, "DomUtils") as dom:
            client.return_value.get_res.return_value = self.response(text=xml)
            dom.tag_value.side_effect = tag_value
            items = self.fetch()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["doubanid"], "123")
        self.assertIsNone(items[1]["doubanid"])
        self.assertEqual(items[0]["year"], "2026")
        self.assertNotEqual(self.f.plugin._DoubanRankPlusOptimized__unique(items[0]),
                            self.f.plugin._DoubanRankPlusOptimized__unique(items[1]))


if __name__ == "__main__":
    unittest.main()
