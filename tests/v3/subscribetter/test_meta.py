"""W03 deterministic snapshots and lifecycle doubles; real host check is opt-in below."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins.v3/subscribetter"


def load(name):
    package = sys.modules.setdefault("w03_subscribetter", types.ModuleType("w03_subscribetter"))
    package.__path__ = [str(PLUGIN)]
    spec = importlib.util.spec_from_file_location(f"w03_subscribetter.{name}", PLUGIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def native(name="Fictional", **fields):
    values = dict(title=name, org_string=name, subtitle=None, cn_name=None, en_name=name,
                  original_name=name, year=None, type="电视剧", begin_season=None,
                  end_season=None, total_season=0, begin_episode=24, end_episode=None,
                  total_episode=1, apply_words=[], media_source=None, media_id=None,
                  episode_group=None, resource_pix="2160p", video_encode="HEVC",
                  resource_effect="HDR", resource_team="HHWEB", audio_encode="DTS")
    values.update(fields)
    return types.SimpleNamespace(**values)


class MetaVideo(types.SimpleNamespace):
    def __init__(self, name="GAT", **fields):
        super().__init__(**vars(native(name, **fields)))


class MetaAnime(MetaVideo):
    pass


class Envelope:
    def __init__(self, meta):
        self.meta = meta

    def __getattr__(self, name):
        return getattr(self.meta, name)

    def __deepcopy__(self, memo):
        from copy import deepcopy
        return type(self)(deepcopy(self.meta, memo))


class MetaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (PLUGIN / "meta.py").exists():
            cls.m = load("meta")

    def setUp(self):
        self.assertTrue((PLUGIN / "meta.py").exists(), "W03 corrector not implemented")
        self.c = self.m.MetaCorrector()

    def test_numeric_word_and_type_are_repaired_on_copy(self):
        for word, short, episode in (("GATE24", "GAT", 24), ("CODE46", "COD", 46)):
            original = native(short, begin_episode=episode)
            result = self.c.correct(original, word + ".2024.2160p")
            self.assertEqual("OK", result.status)
            self.assertEqual(word, result.meta.en_name)
            self.assertIsNone(result.meta.begin_episode)
            self.assertEqual(0, result.meta.total_episode)
            self.assertNotEqual("电视剧", result.meta.type)
            self.assertEqual(short, original.en_name)
            for key in ("resource_pix", "video_encode", "resource_effect", "resource_team", "audio_encode"):
                self.assertEqual(getattr(original, key), getattr(result.meta, key))
            self.assertEqual(self.m.snapshot(result.meta), self.m.snapshot(self.c.correct(result.meta, word + ".2024.2160p").meta))

    def test_actual_V3_missing_numeric_name_and_false_year_range(self):
        for word, episode, total in (("GATE24", 24, 2001), ("CODE46", 46, 1979)):
            # Exact observed V3.0.4 Python/Rust shape, not the old GAT/COD double.
            values = dict.fromkeys(self.m.FIELDS)
            values.update(title=f"{word}.2024.2160p", org_string=f"{word}.2024.2160p",
                          isfile=False, type="电视剧", begin_season=1, total_season=0,
                          begin_episode=episode, end_episode=2024, total_episode=total, apply_words=[])
            original = types.SimpleNamespace(**values)
            result = self.c.correct(original, values["title"], custom_words=["#"])
            self.assertEqual("OK", result.status)
            self.assertEqual(word, result.meta.en_name)
            self.assertEqual("2024", result.meta.year)
            self.assertEqual((None, None, 0, None, None, 0), tuple(getattr(result.meta, name) for name in
                             ("begin_season", "end_season", "total_season", "begin_episode", "end_episode", "total_episode")))
            self.assertEqual(values, self.m.snapshot(original))
            self.assertIsNone(result.meta.resource_pix)  # no second video-spec parser
            unknown = self.c.correct(original, values["title"], context_known=False)
            self.assertEqual("DEFER", unknown.status)
            self.assertIn("RUST_LOCK_CONTEXT_UNKNOWN", unknown.reasons)
            self.assertEqual(values, self.m.snapshot(unknown.meta))
        for title in ("GATE24 Other Story", "[GATE24] Other Story.2024", "Unrelated GATE24.2024"):
            self.assertEqual("DEFER", self.c.correct(native(None), title).status)

    def test_numeric_title_year_and_ambiguous_episode(self):
        for title, year in (("1917.2019.1080p", "2019"), ("1917", None)):
            result = self.c.correct(native("", year="1917", begin_episode=None), title)
            self.assertEqual("1917", result.meta.en_name)
            self.assertEqual(year, result.meta.year)
        result = self.c.correct(native("Fictional", begin_episode=86), "Fictional 86 1080p")
        self.assertEqual("DEFER", result.status)
        self.assertEqual(86, result.meta.begin_episode)
        result = self.c.correct(native("Fictional", begin_episode=7), "Fictional - 07 [1080p]")
        self.assertEqual("OK", result.status)
        self.assertEqual(7, result.meta.begin_episode)

    def test_bracket_roles_and_independent_alias(self):
        result = self.c.correct(native("Wrong", begin_episode=None), "[HHWEB][片名：虚构故事][2160p][中文字幕] Fictional.2024")
        self.assertEqual("虚构故事", result.meta.cn_name)
        self.assertNotEqual("HHWEB", result.meta.en_name)
        for bracket in ("HHWEB", "1080p", "简体字幕", "某某字幕组"):
            result = self.c.correct(native("Fictional", begin_episode=None), f"[{bracket}] Fictional.2024")
            self.assertEqual("Fictional", result.meta.en_name)
        result = self.c.correct(native("Wrong", begin_episode=None), "【又名：虚构别名】 / Tainted The Movie 2024")
        self.assertEqual("虚构别名", result.meta.cn_name)
        self.assertNotIn("The Movie", result.meta.cn_name)
        for text in ("[HHWEB][虚构故事][2160p][中文字幕] Fictional.2024", "【虚构别名】 / Tainted The Movie 2024", "[仅供交流] Fictional.2024"):
            result = self.c.correct(native("Fictional", begin_episode=None), text)
            self.assertEqual("DEFER", result.status)
            self.assertEqual("Fictional", result.meta.en_name)
        result = self.m.MetaCorrector(["虚构故事"]).correct(native("Wrong", begin_episode=None), "[虚构故事] Fictional.2024")
        self.assertEqual("虚构故事", result.meta.cn_name)

    def test_managed_final_path_repairs_real_rust_range_and_preserves_parent_locks(self):
        # Actual Rust final path preserves S00 but drops E04 and gives total 1.
        original = native("Fictional", title="Fictional.S00E02-E04.mkv", org_string="Fictional.S00E02-E04",
                          begin_season=0, total_season=1, begin_episode=2, end_episode=None, total_episode=1)
        path = "/Fictional.2024/Season 0/Fictional.S00E02-E04.mkv"
        result = self.c.correct_path(original, path, custom_words=["#"])
        self.assertEqual("OK", result.status)
        self.assertEqual((0, 2, 4, 3), (result.meta.begin_season, result.meta.begin_episode, result.meta.end_episode, result.meta.total_episode))
        original.begin_episode = 8
        result = self.c.correct_path(original, "/Fictional {[e=8]}/Season 0/Fictional.S00E02-E04.mkv", custom_words=["#"])
        self.assertEqual(8, result.meta.begin_episode)
        self.assertIsNone(result.meta.end_episode)
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_module.Repository(Path(directory) / "state.sqlite3")
            parser = Mock(return_value=original)
            service = self.m.MetaService(repo, self.c, parser)
            service.parse_path("path:1", path, custom_words=["#"])
            self.assertEqual(1, parser.call_count)
            self.assertEqual(Path(path), parser.call_args.args[0])
            row = repo.parse_samples(["path:1"])[0]
            self.assertTrue(row["inputs"]["is_path"])
            self.assertEqual(path, row["inputs"]["title"])
            service.replay(["path:1"])
            self.assertEqual(2, parser.call_count)

    def test_managed_parent_season_survives_numeric_false_episode_repair(self):
        for path, expected in (("/GATE24.2024/S00/GATE24.2024.mkv", 0),
                               ("/GATE24.2024/S02/GATE24.2024.mkv", 2),
                               ("/GATE24.2024/Season 0/GATE24.2024.mkv", 0),
                               ("/GATE24.2024/第2季/GATE24.2024.mkv", 2),
                               ("/GATE24.2024.S00/Extras/GATE24.2024.mkv", 0),
                               ("/GATE24.2024.S00/S02/GATE24.2024.mkv", 2),
                               ("/GATE24.2024/S02/GATE24.S03.2024.mkv", 3)):
            with self.subTest(path=path):
                original = native("GAT", begin_season=2, total_season=1, end_episode=2024, total_episode=2001)
                result = self.c.correct_path(original, path, custom_words=["#"])
                self.assertEqual("OK", result.status)
                self.assertEqual("GATE24", result.meta.en_name)
                self.assertEqual("电视剧", result.meta.type)
                self.assertEqual((expected, None, 1, None, None, 0), tuple(getattr(result.meta, key) for key in
                                 ("begin_season", "end_season", "total_season", "begin_episode", "end_episode", "total_episode")))
                self.assertEqual(24, original.begin_episode)
        # Positive folder evidence repairs stale native seasons, but an explicit lock still wins.
        original = native("GAT", begin_season=8, total_season=1)
        result = self.c.correct_path(original, "/GATE24/S00/GATE24.2024.mkv", locks=("season",))
        self.assertEqual(8, result.meta.begin_season)
        self.assertIsNone(result.meta.begin_episode)

    def test_auxiliary_only_stem_under_generic_parent_defers_final_name(self):
        for folder in ("Extras", "Season 0", "字幕"):
            with self.subTest(folder=folder):
                original = native("Mkv", begin_episode=None, type="电影")
                result = self.c.correct_path(original, f"/Film.2024/{folder}/简体字幕.mkv")
                self.assertEqual("DEFER", result.status)
                self.assertIn("AUXILIARY_TITLE_UNCONFIRMED", result.reasons)
                self.assertEqual("Mkv", result.meta.en_name)

        named = native("Film", begin_episode=None, type="电影")
        result = self.c.correct_path(named, "/Film.2024/简体字幕.mkv")
        self.assertEqual("OK", result.status)
        self.assertEqual("Film", result.meta.en_name)
        music = native("Track", type="音乐", begin_episode=None)
        self.assertEqual("SKIP", self.c.correct_path(music, "/Album/Extras/简体字幕.mkv").status)

    def test_explicit_ranges_s00_and_subtitle_conflict(self):
        result = self.c.correct(native(begin_season=1), "Fictional.S00E02-E04.1080p")
        self.assertEqual((0, 2, 4, 3), (result.meta.begin_season, result.meta.begin_episode, result.meta.end_episode, result.meta.total_episode))
        result = self.c.correct(native(begin_season=1), "Fictional.S00E02", "第 2 季 第 7 集")
        self.assertEqual("DEFER", result.status)
        result = self.c.correct(native(begin_episode=None), "Fictional", "第 0 季 第 2-4 集")
        self.assertEqual((0, 2, 4), (result.meta.begin_season, result.meta.begin_episode, result.meta.end_episode))

    def test_user_words_tags_and_locks_win_without_reapplying_offset(self):
        original = native("Custom", type="电视剧", begin_season=0, begin_episode=5, end_episode=8,
                          total_episode=4, media_source="themoviedb", media_id="42", apply_words=["第 <> 集 >> EP+1"])
        result = self.c.correct(original, "GATE24.S01E04-E07", custom_words=["第 <> 集 >> EP+1"])
        self.assertEqual(self.m.snapshot(original), self.m.snapshot(result.meta))
        result = self.c.correct(original, "GATE24.S01E04-E07 {[type=tv;s=0;e=5-8]}")
        self.assertEqual(0, result.meta.begin_season)
        self.assertEqual("42", result.meta.media_id)
        result = self.c.correct(native("GAT"), "GATE24", locks=("name", "episode", "type"))
        self.assertEqual("GAT", result.meta.en_name)
        self.assertEqual(24, result.meta.begin_episode)
        result = self.c.correct(native("GAT"), "GATE24", custom_words=["Unrelated => Other"])
        self.assertEqual("GATE24", result.meta.en_name)
        result = self.c.correct(native("GAT", begin_season=0, total_season=1), "GATE24 {[s=0]}")
        self.assertEqual(("电视剧", 0, None), (result.meta.type, result.meta.begin_season, result.meta.begin_episode))
        result = self.c.correct(native("GAT", org_string="GATE24 1080p", apply_words=["HHWEB => "]), "GATE24 1080p HHWEB")
        self.assertEqual("DEFER", result.status)

    def test_movie_clears_all_false_tv_fields_but_keeps_theatrical_name(self):
        original = native("Steins;Gate The Movie", begin_season=1, end_season=2, total_season=2,
                          begin_episode=4, end_episode=8, total_episode=5)
        result = self.c.correct(original, "Steins;Gate The Movie.2024.1080p")
        self.assertEqual("电影", result.meta.type)
        self.assertEqual("Steins;Gate The Movie", result.meta.en_name)
        self.assertIsNone(result.meta.begin_season)
        self.assertIsNone(result.meta.end_episode)
        self.assertEqual(0, result.meta.total_season)
        result = self.c.correct(original, "Steins;Gate The Movie.S01E04")
        self.assertEqual("DEFER", result.status)

    def test_rust_missing_lock_context_is_diagnostic_not_false_success(self):
        original = native("GAT")
        result = self.c.correct(original, "GATE24", context_known=False)
        self.assertEqual("DEFER", result.status)
        self.assertIn("RUST_LOCK_CONTEXT_UNKNOWN", result.reasons)
        self.assertEqual(self.m.snapshot(original), self.m.snapshot(result.meta))
        self.assertEqual("OK", self.c.correct(native(begin_season=1, total_season=1, begin_episode=3), "Fictional.S01E03", context_known=False).status)

    def test_failure_original_and_input_budget(self):
        class Broken:
            def __deepcopy__(self, memo):
                raise RuntimeError("private secret")
        original = Broken()
        result = self.c.correct(original, "GATE24")
        self.assertIs(original, result.meta)
        self.assertEqual("ERROR", result.status)
        self.assertNotIn("private secret", str(result.reasons))
        self.assertEqual("ERROR", self.c.correct(native(), "x" * 8193).status)

    def test_negative_episode_tokens_and_year_prefix_are_not_title_proof(self):
        result = self.c.correct(native("Fictional", begin_episode=None, year="2024"), "2024 Fictional 1080p")
        self.assertEqual("Fictional", result.meta.en_name)
        for title in ("X3E03.2024", "Fictional.S01E02E03"):
            result = self.c.correct(native("Fictional", begin_episode=2), title)
            self.assertNotEqual("OK", result.status)
            self.assertEqual(2, result.meta.begin_episode)
        original = native("GATE24", type="电影", begin_episode=None, total_episode=0)
        self.assertEqual("电影", self.c.correct(original, "GATE24.2024.1080p").meta.type)

    def test_explicit_movie_lock_and_credential_replay_are_safe(self):
        result = self.c.correct(native(type="电影", begin_episode=None), "Fictional.S01E03 {[type=movie]}")
        self.assertEqual("电影", result.meta.type)
        self.assertIsNone(result.meta.begin_episode)
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_module.Repository(Path(directory) / "state.sqlite3")
            service = self.m.MetaService(repo, self.c, Mock(return_value=native("GAT")))
            service.parse("private", "GATE24", "https://fiction.invalid/a?passkey=SECRET")
            row = repo.parse_samples(["private"])[0]
            self.assertNotIn("SECRET", str(row))
            self.assertFalse(row["replay_allowed"])
            self.assertEqual([], service.replay(["private"]))

    def test_type_only_lock_does_not_lock_false_episode(self):
        for title, locks in (("GATE24 {[type=movie]}", ()), ("GATE24", ("type",))):
            original = native("GAT", type="电影", begin_season=1, total_season=1)
            result = self.c.correct(original, title, locks=locks)
            self.assertEqual("OK", result.status)
            self.assertEqual("电影", result.meta.type)
            self.assertEqual("GATE24", result.meta.en_name)
            self.assertEqual((None, None, 0, None, None, 0), tuple(getattr(result.meta, name) for name in
                             ("begin_season", "end_season", "total_season", "begin_episode", "end_episode", "total_episode")))

    def test_release_status_and_audio_brackets_are_not_names(self):
        for label in ("国语", "粤语", "国粤双语", "已完结", "全24集", "更新至12集", "无删减", "未删减", "导演剪辑版"):
            result = self.c.correct(native("Fictional", begin_episode=None), f"[{label}] Fictional.2024.1080p")
            self.assertEqual("Fictional", result.meta.en_name, label)
            self.assertIsNone(result.meta.cn_name, label)

    def test_entire_credential_headers_are_redacted_in_samples_and_history(self):
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_module.Repository(Path(directory) / "state.sqlite3")
            service = self.m.MetaService(repo, self.c, Mock(return_value=native("GAT")))
            description = "Cookie: UID=secret-one; CID=secret-two; SEID=secret-three\nAuthorization: Bearer secret-four\nUseful context"
            service.parse("credential-test", "GATE24", description)
            persisted = str(repo.parse_samples()) + str(repo.parse_history("credential-test"))
            for secret in ("secret-one", "secret-two", "secret-three", "secret-four"):
                self.assertNotIn(secret, persisted)
            self.assertIn("Useful context", persisted)
            self.assertEqual([], service.replay(["credential-test"]))

    def test_plugin_managed_api_is_wired_and_default_global_off(self):
        from test_ownership import PluginTests
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        PluginTests.setUpClass()
        with tempfile.TemporaryDirectory() as directory:
            plugin = PluginTests.mod.SubscriBetter()
            plugin.data_path = Path(directory)
            plugin.init_plugin({})
            self.assertTrue(hasattr(plugin, "meta_service"), "managed parse entry is not wired")
            plugin.meta_service.parser = Mock(return_value=native("GAT"))
            app = FastAPI()
            for route in plugin.get_api():
                app.add_api_route(route["path"], route["endpoint"], methods=route["methods"], response_model=route["response_model"])
            client = TestClient(app)
            request = {"sample_key": "site:1:2", "title": "GATE24"}
            self.assertEqual(401, client.post("/parse", json=request).status_code)
            response = client.post("/parse", json=request, headers={"Authorization": "Bearer unit-admin"})
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual("GATE24", response.json()["corrected"]["en_name"])
            self.assertFalse(plugin.meta_patch.active)
            self.assertEqual(1, len(plugin.repository.parse_samples()))
            plugin.meta_service.parser = Mock(return_value=native("Fictional", subtitle="Cookie: UID=fixture-secret-one; CID=fixture-secret-two"))
            response = client.post("/parse", json={"sample_key": "private", "title": "Fictional"}, headers={"Authorization": "Bearer unit-admin"})
            self.assertNotIn("fixture-secret", response.text)
            response = client.get("/parse/samples", headers={"Authorization": "Bearer unit-admin"})
            self.assertNotIn("fixture-secret", response.text)
            response = client.post("/parse/replay", json={"sample_keys": ["private"]}, headers={"Authorization": "Bearer unit-admin"})
            self.assertEqual(200, response.status_code)
            self.assertNotIn("fixture-secret", response.text)
            path = "/Fictional.2024/Season 0/Fictional.S00E02-E04.mkv"
            plugin.meta_service.parser = Mock(return_value=native("Fictional", begin_season=0, begin_episode=2, total_season=1))
            response = client.post("/parse", json={"sample_key": "path:1", "title": path, "is_path": True}, headers={"Authorization": "Bearer unit-admin"})
            self.assertEqual(200, response.status_code)
            self.assertEqual(4, response.json()["corrected"]["end_episode"])
            self.assertEqual(1, plugin.meta_service.parser.call_count)
            plugin.stop_service()

    def test_schema_migration_and_backup_keep_old_rows(self):
        from contextlib import closing
        import sqlite3
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            repo = repo_module.Repository(path)
            task = repo.submit("before", repo_module.Target("电影", "themoviedb", "42"), {}, "tester")
            with repo.connection(write=True) as db:
                # Reconstruct a real v1 database, including when newer packages add tables.
                keep = {"tasks", "intents", "outbox", "audit", "settings", "parse_history", "parse_samples", "parse_revisions"}
                for table in [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in keep]:
                    db.execute('DROP TABLE "' + table + '"')
                db.execute("DROP TABLE parse_history")
                db.execute("DROP TABLE parse_samples")
                db.execute("DROP TABLE parse_revisions")
                db.execute("PRAGMA user_version=1")
            repo = repo_module.Repository(path)
            self.assertEqual(task, repo.get_task(task["id"]))
            backup = Path(directory) / "backup.sqlite3"
            with repo.connection() as src, closing(sqlite3.connect(backup)) as dest:
                src.backup(dest)
            restored = repo_module.Repository(backup)
            self.assertEqual(task, restored.get_task(task["id"]))
            self.assertEqual([], restored.parse_samples())

    def test_plugin_global_config_save_stop_and_dry_run(self):
        from test_ownership import PluginTests
        from unittest.mock import patch
        PluginTests.setUpClass()
        compat = load("meta_compat")
        def python(title, subtitle=None, custom_words=None):
            return MetaVideo()
        def rust(parsed):
            return MetaVideo()
        host = types.SimpleNamespace(_build_python_meta_info=python, _meta_from_rust=rust,
                                     MetaVideo=MetaVideo, MetaAnime=MetaAnime)
        system = types.ModuleType("app.chain.system")
        system.SystemChain = types.SimpleNamespace(get_server_local_version=lambda: "v3.0.4")
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"app.chain.system": system}), patch.object(
                PluginTests.mod, "MetaPatch", lambda corrector: compat.MetaPatch(corrector, host)):
            plugin = PluginTests.mod.SubscriBetter()
            plugin.data_path = Path(directory)
            plugin.init_plugin({})
            for _ in range(2):
                current = plugin.configuration.view()
                preview = plugin.configuration.preview(
                    {"enabled": True, "dry_run": False, "enhance_host_meta": True},
                    current["revision"], current["digest"], "unit-admin")
                self.assertTrue(preview["valid"])
                plugin.init_plugin(preview["config"])
                self.assertTrue(plugin.meta_patch.active)
                self.assertEqual("GATE24", host._build_python_meta_info("GATE24").en_name)
            plugin.stop_service()
            self.assertIs(python, host._build_python_meta_info)
            current = plugin.configuration.view()
            preview = plugin.configuration.preview({"dry_run": True}, current["revision"], current["digest"], "unit-admin")
            self.assertTrue(preview["valid"])
            plugin.init_plugin(preview["config"])
            self.assertFalse(plugin.meta_patch.active)
            self.assertIs(python, host._build_python_meta_info)
            plugin.stop_service()

    def test_revision_persist_and_directed_replay_do_not_mutate_business(self):
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_module.Repository(Path(directory) / "state.sqlite3")
            task = repo.submit("test", repo_module.Target("电影", "themoviedb", "42"), {}, "tester")
            repo.setting("business-clock", {"first": "2026-01-01", "budget": 3, "publish": "UNKNOWN"})
            parser = Mock(return_value=native("GAT"))
            service = self.m.MetaService(repo, self.c, parser)
            service.parse("site:1:2", "GATE24", task_id=task["id"])
            rows = repo.parse_samples(["site:1:2"])
            self.assertEqual("GATE24", rows[0]["inputs"]["title"])
            self.assertEqual("GAT", rows[0]["native"]["en_name"])
            self.assertIn("en_name", rows[0]["result"]["diff"])
            before = repo.get_task(task["id"]), repo.get_action(task["id"]), repo.setting("business-clock")
            service = self.m.MetaService(repo, self.m.MetaCorrector(["Protected 86"]), parser)
            self.assertEqual(1, len(service.replay(["site:1:2"])))
            self.assertEqual(2, len(repo.parse_history("site:1:2")))
            self.assertEqual(before, (repo.get_task(task["id"]), repo.get_action(task["id"]), repo.setting("business-clock")))
            repo.set_state(task["id"], "STOPPED", "tester")
            self.assertEqual([], service.replay(["site:1:2"]))
            with self.assertRaises(ValueError):
                service.replay([str(i) for i in range(101)])

    def test_native_failure_is_persisted_and_retryable_without_exposing_error(self):
        repo_module = load("repository")
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_module.Repository(Path(directory) / "state.sqlite3")
            service = self.m.MetaService(repo, self.c, Mock(side_effect=RuntimeError("credential-secret")))
            result = service.parse("site:1:2", "GATE24")
            self.assertEqual("ERROR", result.status)
            self.assertNotIn("credential-secret", str(repo.parse_samples()))
            self.assertEqual("NATIVE_PARSE_FAILED", result.reasons[0])
            service.parser = Mock(return_value=native("GAT"))
            self.assertEqual("OK", service.replay(["site:1:2"])[0]["status"])
            with self.assertRaises(ValueError):
                service.parse("https://fiction.invalid/?token=secret", "GATE24")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PLUGIN / "meta_compat.py").exists(), "W03 bridges not implemented")
        self.m, self.b = load("meta"), load("meta_compat")
        self.host = types.SimpleNamespace(MetaVideo=MetaVideo, MetaAnime=MetaAnime)
        self.native = MetaVideo()
        def python(title, subtitle=None, custom_words=None):
            return self.native
        def rust(parsed):
            return self.native
        self.host._build_python_meta_info, self.host._meta_from_rust = python, rust
        self.originals = python, rust
        self.bridge = self.b.MetaPatch(self.m.MetaCorrector(), self.host)

    def tearDown(self):
        if hasattr(self, "bridge"):
            self.bridge.uninstall()

    def test_forwarding_result_wrapper_must_disable_instead_of_shadow_correcting(self):
        self.native = Envelope(MetaVideo())
        self.assertTrue(self.bridge.install("v3.0.4"))
        result = self.host._build_python_meta_info("GATE24")
        self.assertEqual({"identity": True, "exposed": "GAT", "nested": "GAT",
                          "state": "RESULT_UNSUPPORTED", "active": False,
                          "reason": "RESULT_SHAPE_UNSUPPORTED"},
                         {"identity": result is self.native, "exposed": result.en_name,
                          "nested": result.meta.en_name, "state": self.bridge.state,
                          "active": self.bridge.active, "reason": self.bridge.last_reason})

    def test_only_declared_concrete_types_are_accepted_by_both_bridges(self):
        self.assertTrue(self.bridge.install("v3.0.4"))
        for kind in (MetaVideo, MetaAnime):
            for engine in ("python", "rust"):
                with self.subTest(kind=kind.__name__, engine=engine):
                    # Rust cannot correct a diff without caller locks; use a stable
                    # concrete result to prove its copy ABI without hiding that limit.
                    self.native = (kind() if engine == "python" else
                                   kind("Fictional", begin_episode=None, total_episode=0))
                    before = self.m.snapshot(self.native)
                    result = (self.host._build_python_meta_info("GATE24") if engine == "python" else
                              self.host._meta_from_rust({"title": "Fictional"}))
                    self.assertIs(type(result), kind)
                    self.assertIsNot(result, self.native)
                    self.assertEqual(before, self.m.snapshot(self.native))
                    self.assertEqual("GATE24" if engine == "python" else "Fictional", result.en_name)
                    self.assertEqual("ACTIVE", self.bridge.state)
        self.native = MetaVideo()
        self.assertIs(self.native, self.host._meta_from_rust({"title": "GATE24"}))
        self.assertEqual("RUST_LOCK_CONTEXT_UNKNOWN", self.bridge.last_reason)
        self.assertTrue(self.bridge.active)

    def test_unsupported_shapes_stop_both_bridges_without_touching_native(self):
        class Subclass(MetaVideo):
            pass
        class MetaMusic(MetaVideo):
            pass
        for value in (Envelope(MetaVideo()), Subclass(), (MetaVideo(), {}), native("GAT"),
                      MetaMusic(type="音乐"), {"meta": MetaVideo()}):
            for engine in ("python", "rust"):
                with self.subTest(kind=type(value).__name__, engine=engine):
                    self.native = value
                    self.assertTrue(self.bridge.install("v3.0.4"))
                    correct = self.bridge.corrector.correct
                    self.bridge.corrector.correct = Mock(wraps=correct)
                    try:
                        result = (self.host._build_python_meta_info("GATE24") if engine == "python" else
                                  self.host._meta_from_rust({"title": "GATE24"}))
                        self.assertIs(value, result)
                        self.assertFalse(self.bridge.active)
                        self.assertEqual("RESULT_UNSUPPORTED", self.bridge.state)
                        self.assertEqual("RESULT_SHAPE_UNSUPPORTED", self.bridge.last_reason)
                        self.bridge.corrector.correct.assert_not_called()
                        if isinstance(value, Envelope):
                            self.assertEqual(("GAT", "GAT"), (value.en_name, value.meta.en_name))
                            self.assertNotIn("en_name", vars(value))
                        self.native = MetaVideo()
                        self.assertIs(self.native, self.host._build_python_meta_info("GATE24"))
                        self.assertIs(self.native, self.host._meta_from_rust({"title": "GATE24"}))
                        self.bridge.corrector.correct.assert_not_called()
                    finally:
                        self.bridge.corrector.correct = correct
                        self.bridge.uninstall()
                    self.assertEqual(self.originals, (self.host._build_python_meta_info, self.host._meta_from_rust))

    def test_rust_none_is_valid_but_python_none_is_not(self):
        self.native = None
        self.assertTrue(self.bridge.install("v3.0.4"))
        self.assertIsNone(self.host._meta_from_rust({}))
        self.assertTrue(self.bridge.active)
        self.assertIsNone(self.host._build_python_meta_info("GATE24"))
        self.assertFalse(self.bridge.active)
        self.assertEqual("RESULT_SHAPE_UNSUPPORTED", self.bridge.last_reason)

    def test_missing_or_invalid_type_declarations_leave_helpers_unchanged(self):
        for name in ("MetaVideo", "MetaAnime"):
            for invalid in (None, "MetaVideo", MetaVideo()):
                with self.subTest(name=name, invalid=invalid):
                    with patch.object(self.host, name, invalid):
                        self.assertFalse(self.bridge.install("v3.0.4"))
                        self.assertFalse(self.bridge.active)
                        self.assertEqual("RESULT_TYPE_UNSUPPORTED", self.bridge.state)
                        self.assertEqual(self.originals, (self.host._build_python_meta_info, self.host._meta_from_rust))
            kind = getattr(self.host, name)
            delattr(self.host, name)
            try:
                self.assertFalse(self.bridge.install("v3.0.4"))
                self.assertEqual("RESULT_TYPE_UNSUPPORTED", self.bridge.state)
            finally:
                setattr(self.host, name, kind)

    def test_deepcopy_must_preserve_exact_concrete_class(self):
        self.assertTrue(self.bridge.install("v3.0.4"))
        before = self.m.snapshot(self.native)
        with patch.object(MetaVideo, "__deepcopy__", lambda value, memo: MetaAnime(), create=True):
            self.assertIs(self.native, self.host._build_python_meta_info("GATE24"))
        self.assertEqual(before, self.m.snapshot(self.native))
        self.assertFalse(self.bridge.active)
        self.assertEqual("RESULT_SHAPE_UNSUPPORTED", self.bridge.last_reason)

    def test_corrector_error_keeps_existing_fail_closed_reason(self):
        self.native.year = object()  # Exact supported class, incompatible field.
        self.assertTrue(self.bridge.install("v3.0.4"))
        self.assertIs(self.native, self.host._build_python_meta_info("GATE24"))
        self.assertFalse(self.bridge.active)
        self.assertEqual("RESULT_UNSUPPORTED", self.bridge.state)
        self.assertEqual("META_CORRECTION_UNAVAILABLE", self.bridge.last_reason)

    def test_idempotent_install_second_owner_and_restore(self):
        self.assertTrue(self.bridge.install("v3.0.4"))
        installed = self.host._build_python_meta_info
        self.assertTrue(self.bridge.install("v3.0.4"))
        self.assertIs(installed, self.host._build_python_meta_info)
        second = self.b.MetaPatch(self.m.MetaCorrector(), self.host)
        self.assertFalse(second.install("v3.0.4"))
        self.assertEqual("GATE24", self.host._build_python_meta_info("GATE24").en_name)
        self.bridge.uninstall()
        self.assertEqual(self.originals, (self.host._build_python_meta_info, self.host._meta_from_rust))

    def test_third_party_above_disables_both_without_overwriting_it(self):
        self.bridge.install("v3.0.4")
        ours = self.host._build_python_meta_info
        def third(title, subtitle=None, custom_words=None):
            return ours(title, subtitle, custom_words)
        self.host._build_python_meta_info = third
        self.assertEqual("GAT", third("GATE24").en_name)
        self.assertFalse(self.bridge.install("v3.0.4"))
        self.assertEqual("CONFLICT", self.bridge.diagnostics()["state"])
        self.bridge.uninstall()
        self.assertIs(third, self.host._build_python_meta_info)
        self.assertEqual("GAT", third("GATE24").en_name)

    def test_two_owners_race_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        other = self.b.MetaPatch(self.m.MetaCorrector(), self.host)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda bridge: bridge.install("v3.0.4"), (self.bridge, other)))
        self.assertEqual(1, sum(results))
        other.uninstall()

    def test_version_signature_and_exception_fallback(self):
        self.assertFalse(self.bridge.install("v3.0.5"))
        self.host._meta_from_rust = lambda parsed, extra: None
        self.assertFalse(self.bridge.install("v3.0.4"))
        self.assertIs(self.originals[0], self.host._build_python_meta_info)
        self.host._meta_from_rust = self.originals[1]
        self.assertTrue(self.bridge.install("v3.0.4"))
        self.bridge.corrector.correct = Mock(side_effect=RuntimeError("secret"))
        result = self.host._build_python_meta_info("GATE24")
        self.assertEqual("GAT", result.en_name)
        self.assertIs(self.native, result)
        self.assertTrue(self.bridge.active)
        self.assertEqual("CORRECTION_FAILED", self.bridge.last_reason)
        self.assertNotIn("secret", str(self.bridge.diagnostics()))


def run_host_contract():
    """Call from the initialized isolated V3 main process, never from unit doubles.

    This temporarily owns only the two approved bridges, restores both, and uses
    fictional parse-only inputs. No disk media, recognizer, network or DB calls.
    Global enhancement must be off before running. A missing Rust runtime FAILS.
    """
    import importlib
    from app.sdk.media import MetaInfo, MetaInfoPath, MetaVideo, MetaAnime, MetaMusic
    from app.chain.system import SystemChain
    core = importlib.import_module("app.plugins.subscribetter.meta")
    compat = importlib.import_module("app.plugins.subscribetter.meta_compat")
    host = importlib.import_module("app.domain.metainfo")  # same two allowed bridges only
    names = ("_build_python_meta_info", "_meta_from_rust")
    originals = {name: getattr(host, name) for name in names}
    checks, outputs, calls = {}, {}, []
    allowed_types = (MetaVideo, MetaAnime)
    class Capture(core.MetaCorrector):
        def correct(self, *args, **kwargs):
            before = core.snapshot(args[0])
            result = super().correct(*args, **kwargs)
            calls.append({"path": "rust" if kwargs.get("context_known") is False else "python",
                          "status": result.status, "reasons": list(result.reasons),
                          "exact_native_type": type(args[0]) in allowed_types,
                          "same_result_type": type(result.meta) is type(args[0]),
                          "native_unchanged": before == core.snapshot(args[0]),
                          "copied": result.meta is not args[0],
                          "native_before": {k: before[k] for k in
                                            ("title", "cn_name", "en_name", "year", "type", "begin_season", "begin_episode", "end_episode", "total_episode", "apply_words")}})
            return result
    corrector = Capture()
    bridge = compat.MetaPatch(corrector)
    # A comment containing the native generic-ID marker selects Python without
    # applying words, inventing an ID, disabling Rust, or replacing another hook.
    python_words = ["# [media_source=python-contract]"]
    third = None
    try:
        checks["sdk_class_identity"] = host.MetaVideo is MetaVideo and host.MetaAnime is MetaAnime
        assert checks["sdk_class_identity"], "Pinned host/SDK concrete classes differ"
        helper_native = originals[names[0]]("GATE24.2024.2160p", custom_words=python_words)
        checks["original_python_helper_exact_type"] = type(helper_native) in allowed_types
        baseline = MetaInfo("Fictional.S00E02-E04.2024.2160p.WEB-DL.HDR.HEVC-HHWEB", custom_words=python_words)
        assert bridge.install(SystemChain.get_server_local_version()), bridge.state
        wrappers = {name: getattr(host, name) for name in names}
        checks["idempotent_install"] = bridge.install(SystemChain.get_server_local_version()) and all(getattr(host, n) is w for n, w in wrappers.items())
        checks["second_owner_refused"] = not compat.MetaPatch(Capture()).install(SystemChain.get_server_local_version())
        for title in ("GATE24.2024.2160p", "CODE46.2024.1080p", "1917.2019.1080p", "1917"):
            result = MetaInfo(title, custom_words=python_words)
            key = title.split(".")[0]
            outputs[title] = core.snapshot(result)
            checks["python_exact_type:" + title] = type(result) in allowed_types
            checks["python_name:" + title] = result.name == key
            if key in ("GATE24", "CODE46"):
                checks["python_scope:" + title] = result.begin_episode is None and result.total_episode == 0
                checks["python_independent_year:" + title] = result.year == "2024"
        checks["python_year"] = outputs["1917.2019.1080p"]["year"] == "2019" and outputs["1917"]["year"] is None
        corrected = MetaInfo("Fictional.S00E02-E04.2024.2160p.WEB-DL.HDR.HEVC-HHWEB", custom_words=python_words)
        checks["python_s00_range"] = (corrected.begin_season, corrected.begin_episode, corrected.end_episode, corrected.total_episode) == (0, 2, 4, 3)
        tech = ("resource_pix", "video_encode", "resource_effect", "resource_team", "audio_encode", "web_source")
        checks["technical_attributes_unchanged"] = all(getattr(baseline, k) == getattr(corrected, k) for k in tech)
        for path, expected_name in (("/Library/GATE24.2024/GATE24.2024.2160p.mkv", "GATE24"),
                                    ("/Library/GATE24.2024/简体字幕.mkv", "GATE24")):
            result = MetaInfoPath(Path(path), custom_words=python_words)
            outputs[path] = core.snapshot(result)
            checks["python_path_exact_type:" + path] = type(result) in allowed_types
            checks["python_final_path:" + path] = result.name == expected_name and result.begin_episode is None
        path = "/Fictional.2024/Season 0/Fictional.S00E02-E04.mkv"
        result = MetaInfoPath(Path(path), custom_words=python_words)
        outputs[path] = core.snapshot(result)
        checks["python_final_s00_path"] = (result.begin_season, result.begin_episode, result.end_episode, result.total_episode) == (0, 2, 4, 3)
        result = MetaInfo("Fictional.S01E04 {[type=tv;s=0;e=5-8;tmdbid=424242]}", custom_words=python_words)
        checks["explicit_identity_range"] = str(result.media_id) == "424242" and (result.begin_season, result.begin_episode, result.end_episode) == (0, 5, 8)
        words = [*python_words, "第 <> 集 >> EP+1"]
        result = MetaInfo("虚构故事 第03集", custom_words=words)
        final = core.MetaCorrector().correct(result, "虚构故事 第03集", custom_words=words)
        checks["offset_once"] = result.begin_episode == 4 and final.meta.begin_episode == 4
        before = len(calls)
        music = MetaInfo("Fictional Artist - Track.flac")
        music_path = MetaInfoPath(Path("/Fictional Album/01 - Track.flac"))
        checks["audio_music_bypass"] = len(calls) == before and type(music) is MetaMusic and type(music_path) is MetaMusic
        result = MetaInfo("GATE24.2024.flac", force_video=True, custom_words=python_words)
        checks["force_video"] = len(calls) > before and type(result) in allowed_types and result.name == "GATE24"
        before = len(calls)
        MetaVideo("GATE24.2024.2160p")
        checks["direct_metavideo_documented_bypass"] = len(calls) == before
        before = len(calls)
        for title in ("GATE24.2024.2160p", "CODE46.2024.1080p", "Fictional.S00E02-E04.2024.1080p"):
            result = MetaInfo(title, custom_words=["#"])
            outputs["rust:" + title] = core.snapshot(result)
            checks["rust_exact_type:" + title] = type(result) in allowed_types
            managed = core.MetaCorrector().correct(result, title, custom_words=["#"])
            if title.startswith(("GATE24", "CODE46")):
                checks["managed_after_rust:" + title] = managed.status == "OK" and managed.meta.name == title.split(".")[0] and managed.meta.begin_episode is None
        path = "/Fictional.2024/Season 0/Fictional.S00E02-E04.mkv"
        result = MetaInfoPath(Path(path), custom_words=["#"])
        outputs["rust_path:" + path] = core.snapshot(result)
        checks["rust_path_exact_type"] = type(result) in allowed_types
        checks["rust_final_s00_path"] = (result.begin_season, result.begin_episode, result.end_episode, result.total_episode) == (0, 2, 4, 3)
        managed = core.MetaCorrector().correct_path(result, path, custom_words=["#"])
        outputs["managed_rust_path:" + path] = managed.record()
        checks["managed_rust_final_s00_path"] = managed.status == "OK" and (managed.meta.begin_season, managed.meta.begin_episode, managed.meta.end_episode, managed.meta.total_episode) == (0, 2, 4, 3)
        for engine, words in (("python", python_words), ("rust", ["#"])):
            for path, season in (("/GATE24.2024/S00/GATE24.2024.mkv", 0),
                                 ("/GATE24.2024/S02/GATE24.2024.mkv", 2),
                                 ("/GATE24.2024/S02/GATE24.S03.2024.mkv", 3)):
                result = MetaInfoPath(Path(path), custom_words=words)
                managed = core.MetaCorrector().correct_path(result, path, custom_words=words)
                key = f"managed_{engine}_parent_season:{path}"
                outputs[key] = managed.record()
                corrected = core.snapshot(managed.meta)
                checks[key] = (managed.status == "OK" and managed.meta.name == "GATE24" and
                               tuple(corrected[k] for k in ("type", "begin_season", "end_season", "total_season", "begin_episode", "end_episode", "total_episode")) ==
                               ("电视剧", season, None, 1, None, None, 0))
        checks["actual_rust_bridge_called"] = any(c["path"] == "rust" for c in calls[before:])
        checks["actual_python_bridge_called"] = any(c["path"] == "python" for c in calls)
        for engine in ("python", "rust"):
            actual = [c for c in calls if c["path"] == engine]
            checks[engine + "_helper_results_preserve_concrete_types"] = bool(actual) and all(
                c["exact_native_type"] and c["same_result_type"] and c["native_unchanged"] for c in actual)
        checks["python_correction_copied"] = any(c["path"] == "python" and c["copied"] for c in calls)
        checks["bridge_active_after_actual_results"] = bridge.diagnostics()["state"] == "ACTIVE"
        preserved = originals[names[0]]("GATE24", custom_words=python_words)
        real_correct = corrector.correct
        corrector.correct = Mock(side_effect=RuntimeError("fictional injected fault"))
        result = MetaInfo("GATE24", custom_words=python_words)
        checks["fault_returns_native"] = core.snapshot(result) == core.snapshot(preserved)
        corrector.correct = real_correct
        ours = host._build_python_meta_info
        def third(title, subtitle=None, custom_words=None):
            return ours(title, subtitle, custom_words)
        host._build_python_meta_info = third
        result = MetaInfo("GATE24", custom_words=python_words)
        checks["third_party_disables_ours"] = bridge.diagnostics()["state"] == "CONFLICT" and core.snapshot(result) == core.snapshot(preserved)
        bridge.uninstall()
        checks["uninstall_preserves_third_party"] = host._build_python_meta_info is third
    finally:
        bridge.uninstall()
        if third is not None and host._build_python_meta_info is third:
            host._build_python_meta_info = originals[names[0]]
        assert all(getattr(host, n) is f for n, f in originals.items()), "Actual helpers not restored"
    checks["both_originals_restored"] = all(getattr(host, n) is f for n, f in originals.items())
    fresh = compat.MetaPatch(core.MetaCorrector())
    try:
        checks["fresh_reload_install"] = fresh.install(SystemChain.get_server_local_version())
    finally:
        fresh.uninstall()
        assert all(getattr(host, n) is f for n, f in originals.items()), "Reload helpers not restored"
    checks["reload_restored"] = all(getattr(host, n) is f for n, f in originals.items())
    # Controlled proxy negative only: real SDK class identities, fictional helper
    # output. Never substitute a real helper and bypass its provenance check.
    nested = MetaVideo("GATE24")
    nested.en_name = "GAT"
    envelope = Envelope(nested)
    before = core.snapshot(nested)
    def python(title, subtitle=None, custom_words=None):
        return envelope
    def rust(parsed):
        return envelope
    proxy = types.SimpleNamespace(MetaVideo=MetaVideo, MetaAnime=MetaAnime,
                                  _build_python_meta_info=python, _meta_from_rust=rust)
    proxy_patch = compat.MetaPatch(core.MetaCorrector(), proxy)
    proxy_checks = {}
    try:
        proxy_checks["installed_with_real_sdk_classes"] = proxy_patch.install(SystemChain.get_server_local_version())
        result = proxy._build_python_meta_info("GATE24")
        proxy_checks["native_envelope_identity"] = result is envelope
        proxy_checks["nested_and_exposed_unchanged"] = (
            before == core.snapshot(nested) and result.en_name == "GAT" and "en_name" not in vars(result))
        proxy_checks["explicit_incompatibility"] = (proxy_patch.state == "RESULT_UNSUPPORTED" and
            not proxy_patch.active and proxy_patch.last_reason == "RESULT_SHAPE_UNSUPPORTED")
        proxy_checks["other_bridge_disabled"] = proxy._meta_from_rust({"title": "GATE24"}) is envelope
    finally:
        proxy_patch.uninstall()
        assert proxy._build_python_meta_info is python and proxy._meta_from_rust is rust
        assert all(getattr(host, n) is f for n, f in originals.items()), "Proxy touched actual helpers"
    return {"checks": checks, "outputs": outputs, "bridge_calls": calls,
            "controlled_proxy_checks": proxy_checks,
            "status": "PASS" if all(checks.values()) and all(proxy_checks.values()) else "FAIL",
            "expected_limitations": {} if checks["rust_final_s00_path"] else {
                "rust_final_s00_path": "Global Rust bridge lacks call-specific locks and full path; it cannot safely expand E02 to E02-E04. Managed full-path correction is checked independently; the original failed coverage check is retained."},
            "coverage": "Actual SDK functions and final MetaInfoPath; no recognition/search/download or historical host cache invalidation"}


if __name__ == "__main__":
    unittest.main()
