"""W03 deterministic snapshots and lifecycle doubles; real host check is opt-in below."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock

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
        result = self.c.correct(native("Wrong", begin_episode=None), "[HHWEB][虚构故事][2160p][中文字幕] Fictional.2024")
        self.assertEqual("虚构故事", result.meta.cn_name)
        self.assertNotEqual("HHWEB", result.meta.en_name)
        for bracket in ("HHWEB", "1080p", "简体字幕", "某某字幕组"):
            result = self.c.correct(native("Fictional", begin_episode=None), f"[{bracket}] Fictional.2024")
            self.assertEqual("Fictional", result.meta.en_name)
        result = self.c.correct(native("Wrong", begin_episode=None), "【虚构别名】 / Tainted The Movie 2024")
        self.assertEqual("虚构别名", result.meta.cn_name)
        self.assertNotIn("The Movie", result.meta.cn_name)

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
            return native("GAT")
        def rust(parsed):
            return native("GAT")
        host = types.SimpleNamespace(_build_python_meta_info=python, _meta_from_rust=rust)
        system = types.ModuleType("app.chain.system")
        system.SystemChain = types.SimpleNamespace(get_server_local_version=lambda: "v3.0.4")
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"app.chain.system": system}), patch.object(
                PluginTests.mod, "MetaPatch", lambda corrector: compat.MetaPatch(corrector, host)):
            plugin = PluginTests.mod.SubscriBetter()
            plugin.data_path = Path(directory)
            for _ in range(2):
                plugin.init_plugin({"enabled": True, "dry_run": False, "enhance_host_meta": True})
                self.assertTrue(plugin.meta_patch.active)
                self.assertEqual("GATE24", host._build_python_meta_info("GATE24").en_name)
            plugin.stop_service()
            self.assertIs(python, host._build_python_meta_info)
            plugin.init_plugin({"enabled": True, "dry_run": True, "enhance_host_meta": True})
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
        self.host = types.SimpleNamespace()
        def python(title, subtitle=None, custom_words=None):
            return native("GAT")
        def rust(parsed):
            return native("GAT")
        self.host._build_python_meta_info, self.host._meta_from_rust = python, rust
        self.originals = python, rust
        self.bridge = self.b.MetaPatch(self.m.MetaCorrector(), self.host)

    def tearDown(self):
        if hasattr(self, "bridge"):
            self.bridge.uninstall()

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
        self.assertNotIn("secret", str(self.bridge.diagnostics()))


def run_host_contract():
    """Call from the initialized isolated V3 main process, never from unit doubles.

    This temporarily owns only the two approved bridges, restores both, and uses
    fictional parse-only inputs. No disk media, recognizer, network or DB calls.
    Global enhancement must be off before running. A missing Rust runtime FAILS.
    """
    import importlib
    from app.sdk.media import MetaInfo, MetaInfoPath, MetaVideo
    from app.chain.system import SystemChain
    core = importlib.import_module("app.plugins.subscribetter.meta")
    compat = importlib.import_module("app.plugins.subscribetter.meta_compat")
    host = importlib.import_module("app.domain.metainfo")  # same two allowed bridges only
    names = ("_build_python_meta_info", "_meta_from_rust")
    originals = {name: getattr(host, name) for name in names}
    checks, outputs, calls = {}, {}, []
    class Capture(core.MetaCorrector):
        def correct(self, *args, **kwargs):
            result = super().correct(*args, **kwargs)
            calls.append({"path": "rust" if kwargs.get("context_known") is False else "python",
                          "status": result.status, "reasons": list(result.reasons)})
            return result
    corrector = Capture()
    bridge = compat.MetaPatch(corrector)
    # A comment containing the native generic-ID marker selects Python without
    # applying words, inventing an ID, disabling Rust, or replacing another hook.
    python_words = ["# [media_source=python-contract]"]
    third = None
    try:
        baseline = MetaInfo("Fictional.S00E02-E04.2024.2160p.WEB-DL.HDR.HEVC-HHWEB", custom_words=python_words)
        assert bridge.install(SystemChain.get_server_local_version()), bridge.state
        wrappers = {name: getattr(host, name) for name in names}
        checks["idempotent_install"] = bridge.install(SystemChain.get_server_local_version()) and all(getattr(host, n) is w for n, w in wrappers.items())
        checks["second_owner_refused"] = not compat.MetaPatch(Capture()).install(SystemChain.get_server_local_version())
        for title in ("GATE24.2024.2160p", "CODE46.2024.1080p", "1917.2019.1080p", "1917"):
            result = MetaInfo(title, custom_words=python_words)
            key = title.split(".")[0]
            outputs[title] = core.snapshot(result)
            checks["python_name:" + title] = result.name == key
            if key in ("GATE24", "CODE46"):
                checks["python_scope:" + title] = result.begin_episode is None and result.total_episode == 0
        checks["python_year"] = outputs["1917.2019.1080p"]["year"] == "2019" and outputs["1917"]["year"] is None
        corrected = MetaInfo("Fictional.S00E02-E04.2024.2160p.WEB-DL.HDR.HEVC-HHWEB", custom_words=python_words)
        checks["python_s00_range"] = (corrected.begin_season, corrected.begin_episode, corrected.end_episode, corrected.total_episode) == (0, 2, 4, 3)
        tech = ("resource_pix", "video_encode", "resource_effect", "resource_team", "audio_encode", "web_source")
        checks["technical_attributes_unchanged"] = all(getattr(baseline, k) == getattr(corrected, k) for k in tech)
        for path, expected_name in (("/Library/GATE24.2024/GATE24.2024.2160p.mkv", "GATE24"),
                                    ("/Library/GATE24.2024/简体字幕.mkv", "GATE24")):
            result = MetaInfoPath(Path(path), custom_words=python_words)
            outputs[path] = core.snapshot(result)
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
        checks["audio_music_bypass"] = len(calls) == before and type(music).__name__ == "MetaMusic" and type(music_path).__name__ == "MetaMusic"
        result = MetaInfo("GATE24.2024.flac", force_video=True, custom_words=python_words)
        checks["force_video"] = len(calls) > before and type(result).__name__ != "MetaMusic" and result.name == "GATE24"
        before = len(calls)
        MetaVideo("GATE24.2024.2160p")
        checks["direct_metavideo_documented_bypass"] = len(calls) == before
        before = len(calls)
        for title in ("GATE24.2024.2160p", "CODE46.2024.1080p", "Fictional.S00E02-E04.2024.1080p"):
            result = MetaInfo(title, custom_words=["#"])
            outputs["rust:" + title] = core.snapshot(result)
            managed = core.MetaCorrector().correct(result, title, custom_words=["#"])
            if title.startswith(("GATE24", "CODE46")):
                checks["managed_after_rust:" + title] = managed.status == "OK" and managed.meta.name == title.split(".")[0] and managed.meta.begin_episode is None
        result = MetaInfoPath(Path("/Fictional.2024/Season 0/Fictional.S00E02-E04.mkv"), custom_words=["#"])
        checks["rust_final_s00_path"] = (result.begin_season, result.begin_episode, result.end_episode, result.total_episode) == (0, 2, 4, 3)
        checks["actual_rust_bridge_called"] = any(c["path"] == "rust" for c in calls[before:])
        checks["actual_python_bridge_called"] = any(c["path"] == "python" for c in calls)
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
    checks["both_originals_restored"] = all(getattr(host, n) is f for n, f in originals.items())
    fresh = compat.MetaPatch(core.MetaCorrector())
    try:
        checks["fresh_reload_install"] = fresh.install(SystemChain.get_server_local_version())
    finally:
        fresh.uninstall()
    checks["reload_restored"] = all(getattr(host, n) is f for n, f in originals.items())
    return {"checks": checks, "outputs": outputs, "bridge_calls": calls,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "coverage": "Actual SDK functions and final MetaInfoPath; no recognition/search/download or historical host cache invalidation"}


if __name__ == "__main__":
    unittest.main()
