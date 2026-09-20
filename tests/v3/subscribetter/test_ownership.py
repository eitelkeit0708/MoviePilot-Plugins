"""W01 unit doubles, not MoviePilot integration evidence (T007/T010/T148/T150)."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins.v3/subscribetter"


def load_modules():
    package = types.ModuleType("w01_subscribetter")
    package.__path__ = [str(PLUGIN)]
    sys.modules[package.__name__] = package
    result = []
    for name in ("repository", "ownership"):
        spec = importlib.util.spec_from_file_location(f"w01_subscribetter.{name}", PLUGIN / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result.append(module)
    return result


class AdapterContractTests(unittest.TestCase):
    """Run the real adapter against the fixed V3 Chain/Oper return contracts."""
    def setUp(self):
        from enum import Enum
        from unittest.mock import patch
        self.repository_module, self.ownership_module = load_modules()
        self.chain = types.SimpleNamespace(add=Mock(return_value=(42, "新增订阅成功")))
        self.oper = types.SimpleNamespace(get=Mock(), list_by_media_identity=Mock(return_value=[]), update=Mock())
        media_type = Enum("MediaType", {"MOVIE": "电影", "TV": "电视剧"})
        def normalize(source):
            return types.SimpleNamespace(value=getattr(source, "value", source))
        modules = {}
        for name, values in {
            "app": {}, "app.sdk": {}, "app.chain": {}, "app.db": {}, "app.db.oper": {}, "app.schemas": {},
            "app.sdk.media": {"normalize_media_source": normalize,
                              "resolve_media_identity": lambda media: (normalize(media.media_source), media.media_id)},
            "app.chain.subscribe": {"SubscribeChain": lambda: self.chain},
            "app.db.oper.subscribe": {"SubscribeOper": lambda: self.oper},
            "app.schemas.types": {"MediaType": media_type},
        }.items():
            modules[name] = types.ModuleType(name)
            modules[name].__dict__.update(values)
        spec = importlib.util.spec_from_file_location("w01_subscribetter.mp_adapter", PLUGIN / "mp_adapter.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.module)
        self.adapter = self.module.NativeAdapter()
        self.target = self.repository_module.Target("电视剧", "themoviedb", "123", 0, "specials")

    def test_V3_adapter_positive_id_accepts_nonempty_success_description(self):
        self.assertEqual(42, self.adapter.create(self.target, {"name": "Fictional"}))
        options = self.chain.add.call_args.kwargs
        self.assertEqual("S", options["state"])
        self.assertEqual(0, options["season"])
        self.assertEqual("specials", options["episode_group"])
        self.assertFalse(options["exist_ok"])

    def test_V3_adapter_missing_invalid_or_existing_id_is_not_new_ownership(self):
        for sid, message in ((None, "识别失败"), (0, "新增订阅失败"), (False, ""),
                             (-1, ""), ("42", ""), (42, "订阅已存在"),
                             (42, ""), (42, "未知结果说明")):
            with self.subTest(sid=sid, message=message):
                self.chain.add.return_value = sid, message
                with self.assertRaises(RuntimeError):
                    self.adapter.create(self.target, {})

    def test_V3_adapter_success_message_still_requires_identity_and_S_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repository_module.Repository(Path(directory) / "state.sqlite3")
            owner = self.ownership_module.Ownership(repo, self.adapter)
            native = types.SimpleNamespace(id=42, type="电视剧", media_source="themoviedb", media_id="wrong", season=0, episode_group="specials", state="S")
            self.oper.get.return_value = native
            task = owner.submit("real-adapter", self.target, {}, "admin")
            self.assertEqual(42, task["native_id"])
            self.assertEqual("PENDING", task["state"])
            self.assertEqual("NATIVE_IDENTITY_MISMATCH", repo.get_action(task["id"])["error_code"])
            native.media_id, native.state = "123", "R"
            owner.reconcile()
            self.assertEqual("PENDING", repo.get_task(task["id"])["state"])
            self.assertEqual("HANDOFF_READBACK_FAILED", repo.get_action(task["id"])["error_code"])
            native.state = "S"
            owner.reconcile()
            self.assertEqual("ACTIVE", repo.get_task(task["id"])["state"])
            self.assertEqual(1, self.chain.add.call_count)


class Host:
    def __init__(self):
        self.rows = {}
        self.creates = 0
        self.before_pause = None
        self.fail_read = False

    @staticmethod
    def capabilities():
        return []

    def list(self):
        return list(self.rows.values())

    def get(self, sid):
        if self.fail_read:
            raise RuntimeError("secret error must not be stored")
        return dict(self.rows[sid]) if sid in self.rows else None

    def find(self, target):
        return [dict(r) for r in self.rows.values() if r["media_source"] == target.media_source and r["media_id"] == target.media_id]

    def create(self, target, snapshot):
        self.creates += 1
        self.rows[42] = dict(snapshot, id=42, type=target.media_type, media_source=target.media_source,
                             media_id=target.media_id, season=target.season, episode_group=target.episode_group, state="S")
        return 42

    def pause(self, sid):
        if self.before_pause:
            self.before_pause()
        self.rows[sid]["state"] = "S"

    def set_state(self, sid, state):
        self.rows[sid]["state"] = state


class OwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (PLUGIN / "ownership.py").exists():
            cls.repo_module, cls.module = load_modules()

    def setUp(self):
        self.assertTrue((PLUGIN / "ownership.py").exists(), "W01 handoff implementation missing")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = self.repo_module.Repository(Path(tmp.name) / "state.sqlite3")
        self.target = self.repo_module.Target("电视剧", "themoviedb", "123", 0, "specials")
        self.host = Host()
        self.service = self.module.Ownership(self.repo, self.host)

    def submit(self, key="intent", **kwargs):
        return self.service.submit(key, self.target, {"name": "Fictional"}, "admin", **kwargs)

    def test_T148_intent_precedes_pause_and_readback_precedes_ack(self):
        self.host.create(self.target, {})
        self.host.rows[42]["state"] = "R"
        self.host.before_pause = lambda: self.assertEqual("PENDING", self.repo.list_tasks()[0]["state"])
        row = self.submit(native_id=42, adopt=True)
        self.assertEqual("ACTIVE", row["state"])
        self.assertEqual("R", row["snapshot"]["state"])
        self.assertEqual("S", self.host.rows[42]["state"])
        self.assertEqual([], self.repo.pending_actions())

    def test_T011_existing_unowned_record_never_silently_adopted(self):
        self.host.create(self.target, {})
        self.host.rows[42]["state"] = "R"
        row = self.submit()
        self.assertEqual("PENDING", row["state"])
        self.assertEqual("R", self.host.rows[42]["state"])
        self.assertEqual(1, self.host.creates)
        self.assertEqual("NATIVE_CONFLICT", self.repo.pending_actions()[0]["error_code"])

    def test_T148_explicit_adoption_resolves_conflict_before_pause(self):
        self.host.create(self.target, {"name": "Native", "keyword": "keep"})
        self.host.rows[42]["state"] = "R"
        row = self.submit()
        def check_persisted():
            current = self.repo.get_task(row["id"])
            self.assertEqual(42, current["native_id"])
            self.assertEqual("R", current["snapshot"]["state"])
            self.assertEqual("keep", current["snapshot"]["keyword"])
        self.host.before_pause = check_persisted
        adopted = self.submit("explicit", native_id=42, adopt=True)
        self.assertEqual(row["id"], adopted["id"])
        self.assertEqual("ACTIVE", adopted["state"])
        self.assertEqual(1, self.host.creates)

    def test_T148_pre_dispatch_read_failure_remains_retryable(self):
        find = self.host.find
        self.host.find = Mock(side_effect=RuntimeError("temporary read failure"))
        row = self.submit()
        self.assertEqual("PENDING", self.repo.pending_actions()[0]["state"])
        self.assertEqual(0, self.host.creates)
        self.host.find = find
        self.service.reconcile()
        self.assertEqual("ACTIVE", self.repo.get_task(row["id"])["state"])
        self.assertEqual(1, self.host.creates)

    def test_T148_unknown_survives_action_read_failure_without_duplicate_create(self):
        task = self.repo.submit("uncertain", self.target, {}, "admin")
        self.assertTrue(self.repo.start_create(task["id"]))
        original = self.repo.get_action
        self.repo.get_action = Mock(side_effect=RuntimeError("temporary action read failure"))
        self.service._handoff(task)
        self.repo.get_action = original
        self.assertEqual("UNKNOWN", original(task["id"])["state"])
        self.assertEqual("CREATE_OUTCOME_UNKNOWN", original(task["id"])["error_code"])
        self.service.reconcile()
        self.assertEqual(0, self.host.creates)

    def test_T148_preflight_cannot_erase_concurrent_unknown_dispatch(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        for mode in ("failure", "conflict"):
            with self.subTest(mode=mode):
                target = self.repo_module.Target("电影", "themoviedb", mode)
                task = self.repo.submit(mode, target, {}, "admin")
                entered, continue_read = Event(), Event()
                def find(_target):
                    entered.set()
                    if not continue_read.wait(3):
                        raise AssertionError("read gate timed out")
                    if mode == "failure":
                        raise RuntimeError("preflight read failed")
                    return [{"type": target.media_type, "media_source": target.media_source,
                             "media_id": target.media_id, "season": None, "episode_group": ""}]
                self.host.find = find
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.service._handoff, task)
                    try:
                        self.assertTrue(entered.wait(3))
                        self.assertTrue(self.repo.start_create(task["id"]))
                    finally:
                        continue_read.set()
                    future.result(timeout=3)
                self.assertEqual("UNKNOWN", self.repo.get_action(task["id"])["state"])
                self.assertEqual("CREATE_OUTCOME_UNKNOWN", self.repo.get_action(task["id"])["error_code"])
        self.service.reconcile()
        self.assertEqual(0, self.host.creates)

    def test_reconciliation_advances_past_unknown_page_and_wraps_after_reload(self):
        for i in range(100):
            target = self.repo_module.Target("电影", "themoviedb", str(i + 1))
            task = self.repo.submit(f"unknown-{i}", target, {}, "admin")
            self.repo.start_create(task["id"])
        later = self.repo.submit("later", self.target, {}, "admin")
        self.service.reconcile()
        self.assertEqual(0, self.host.creates, "one reconciliation run must remain bounded")
        reloaded = self.module.Ownership(self.repo, self.host)
        reloaded.reconcile()
        self.assertEqual("ACTIVE", self.repo.get_task(later["id"])["state"])
        # A repaired early action must not be lost after the cursor reaches the tail.
        self.repo.action_state(1, "PENDING", "HOST_UNAVAILABLE")
        attempted = Mock(side_effect=self.host.find)
        self.host.find = attempted
        reloaded.reconcile()
        self.assertTrue(any(call.args[0].media_id == "1" for call in attempted.call_args_list))

    def test_direct_handoff_is_not_limited_to_first_thousand_actions(self):
        # Seed through the public repository, keeping this a real SQLite regression.
        for i in range(1000):
            target = self.repo_module.Target("电影", "themoviedb", str(i + 1))
            task = self.repo.submit(f"unknown-{i}", target, {}, "admin")
            self.repo.start_create(task["id"])
        row = self.submit("later-direct")
        self.assertEqual("ACTIVE", row["state"])
        self.assertEqual(1, self.host.creates)

    def test_reconciliation_wraps_existing_cycle_before_new_arrivals(self):
        for i in range(101):
            target = self.repo_module.Target("电影", "themoviedb", str(i + 1))
            task = self.repo.submit(f"unknown-{i}", target, {}, "admin")
            self.repo.start_create(task["id"])
        self.service.reconcile()
        for i in range(101, 301):
            target = self.repo_module.Target("电影", "themoviedb", str(i + 1))
            task = self.repo.submit(f"new-{i}", target, {}, "admin")
            self.repo.start_create(task["id"])
        self.service.reconcile()
        self.repo.action_state(1, "PENDING", "HOST_UNAVAILABLE")
        self.service.reconcile()
        self.assertEqual("ACTIVE", self.repo.get_task(1)["state"])

    def test_T148_lost_create_response_is_not_replayed_or_adopted(self):
        original = self.host.create
        def lost(*args):
            original(*args)
            raise RuntimeError("response lost")
        self.host.create = lost
        row = self.submit()
        self.assertEqual("PENDING", row["state"])
        self.assertEqual("UNKNOWN", self.repo.pending_actions()[0]["state"])
        self.service.reconcile()
        self.assertEqual(1, self.host.creates)
        self.assertEqual("CREATE_OUTCOME_UNKNOWN", self.repo.pending_actions()[0]["error_code"])

    def test_T148_pause_readback_failure_can_resume_after_reload(self):
        original = self.host.create
        def fail_read(*args):
            sid = original(*args)
            self.host.fail_read = True
            return sid
        self.host.create = fail_read
        self.assertEqual("PENDING", self.submit()["state"])
        self.host.fail_read = False
        self.module.Ownership(self.repo, self.host).reconcile()
        self.assertEqual("ACTIVE", self.repo.list_tasks()[0]["state"])
        self.assertEqual(1, self.host.creates)

    def test_T010_safe_stop_keeps_shell_paused_stopped_not_reactivated(self):
        row = self.submit()
        self.repo.set_state(row["id"], "STOPPED", "admin")
        self.host.rows[42]["state"] = "R"
        self.service.ensure_paused()
        self.assertEqual("S", self.host.rows[42]["state"])
        self.assertEqual("STOPPED", self.submit("another")["state"])
        self.assertEqual(1, self.host.creates)

    def test_release_requires_current_preview_then_verified_readback(self):
        row = self.submit()
        preview = self.service.release_preview(row["id"])
        with self.assertRaises(ValueError):
            self.service.release(row["id"], "bad-digest", "admin")
        row = self.service.release(row["id"], preview["revision"], "admin")
        self.assertEqual("RELEASED_NATIVE", row["state"])
        self.assertFalse(self.repo.owned(self.target))
        self.assertEqual("R", self.host.rows[42]["state"])
        self.service.ensure_paused()
        self.assertEqual("R", self.host.rows[42]["state"])

class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_module, cls.module = load_modules()

    def setUp(self):
        self.assertTrue(hasattr(self.module, "Guard"), "W01 synchronous guards missing")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = self.repo_module.Repository(Path(tmp.name) / "state.sqlite3")
        self.target = self.repo_module.Target("电视剧", "themoviedb", "123", 0, "specials")
        self.host = Host()
        self.host.create(self.target, {})
        self.owner = self.module.Ownership(self.repo, self.host)
        self.owner.submit("intent", self.target, {}, "admin", native_id=42, adopt=True)
        self.guard = self.module.Guard(self.repo, self.host, lambda native: False)

    def event(self, origin, contexts=None, valid=True, subscribe=None):
        from copy import deepcopy
        data = types.SimpleNamespace(origin=origin, contexts=contexts or [], context=(contexts or [None])[0],
                                     updated=False, updated_contexts=None, cancel=False, source="native", reason="", subscribe=subscribe)
        snap = types.SimpleNamespace(valid=valid, input=types.SimpleNamespace(**vars(data)))
        snap.input.contexts = deepcopy(data.contexts)
        # Snapshot is distinct; mutations must hit event.event_data.
        return types.SimpleNamespace(event_data=data, snapshot=lambda: snap)

    def test_V3_context_media_info_preserves_unrelated_original_and_prior_removals(self):
        owned = types.SimpleNamespace(media_info=types.SimpleNamespace(type="电视剧", media_source="themoviedb", media_id="123"))
        other = types.SimpleNamespace(media_info=types.SimpleNamespace(type="电影", media_source="themoviedb", media_id="987"))
        event = self.event(self.origin(), [owned, other])
        self.guard.selection(event)
        self.assertEqual([other], event.event_data.updated_contexts)
        self.assertIs(other, event.event_data.updated_contexts[0])
        self.assertIsNot(other, event.snapshot().input.contexts[1])
        for previous in ([owned], []):
            event = self.event(self.origin(), [owned, other])
            event.event_data.updated = True
            event.event_data.updated_contexts = previous
            self.guard.selection(event)
            self.assertEqual([], event.event_data.updated_contexts)
        event = self.event(self.origin(), [owned, other], valid=False)
        self.guard.selection(event)
        self.assertEqual([], event.event_data.updated_contexts)

    def origin(self, sid=42):
        import json
        row = dict(self.host.rows[42], id=sid)
        return "Subscribe|" + json.dumps(row)

    def test_T007_T011_manual_other_sid_preserves_original_objects(self):
        candidate = object()
        for origin in ("Manual", self.origin(99)):
            event = self.event(origin, [candidate])
            self.guard.selection(event)
            self.guard.download(event)
            self.assertFalse(event.event_data.updated)
            self.assertFalse(event.event_data.cancel)
            self.assertIs(candidate, event.event_data.contexts[0])
        event = self.event(self.origin(), [candidate])
        self.guard.selection(event)
        self.assertTrue(event.event_data.updated)
        self.assertEqual([], event.event_data.updated_contexts)
        self.guard.download(event)
        self.assertTrue(event.event_data.cancel)

    def test_T007_auto_scope_before_added_persistence(self):
        self.guard = self.module.Guard(self.repo, self.host, lambda native: native["id"] == 99)
        self.host.rows[99] = dict(self.host.rows[42], id=99)
        event = self.event(self.origin(99), [object()])
        self.guard.download(event)
        self.assertTrue(event.event_data.cancel)
        self.assertIsNone(self.repo.by_native_id(99))

    def test_T010_invalid_snapshot_or_store_failure_does_not_allow_native(self):
        event = self.event(self.origin(), [object()], valid=False)
        self.guard.download(event)
        self.assertTrue(event.event_data.cancel)
        self.repo.by_native_id = Mock(side_effect=RuntimeError("db down"))
        event = self.event(self.origin(), [object()])
        self.guard.download(event)
        self.assertTrue(event.event_data.cancel)
        for origin in ("Manual", self.origin(99)):
            event = self.event(origin, [object()])
            self.guard.download(event)
            self.assertFalse(event.event_data.cancel)

    def test_T008_completion_stopped_guard_and_malformed_origin(self):
        row = self.repo.list_tasks()[0]
        self.repo.set_state(row["id"], "STOPPED", "admin")
        event = self.event(None, subscribe=types.SimpleNamespace(**self.host.rows[42]))
        self.guard.completion(event)
        self.assertTrue(event.event_data.cancel)
        event = self.event("Subscribe|{broken", [object()])
        self.guard.download(event)
        self.assertFalse(event.event_data.cancel)
        self.assertTrue(self.guard.unhealthy)

class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PLUGIN / "__init__.py").exists():
            return
        import enum
        from fastapi import Header, HTTPException
        from pydantic import BaseModel
        class TokenPayload(BaseModel):
            username: str = "admin"
            super_user: bool = True
        def verify_token(authorization: str | None = Header(default=None)):
            if authorization != "Bearer unit-admin":
                raise HTTPException(401)
            return TokenPayload()
        class Types(enum.Enum):
            ResourceSelection = "selection"
            ResourceDownload = "download"
            SubscribeCompletionCheck = "completion"
            TransferIntercept = "transfer"
            SubscribeAdded = "added"
            SubscribeDeleted = "deleted"
        cls.listeners = {}
        manager = types.SimpleNamespace(
            add_event_listener=lambda event, callback, **kw: cls.listeners.__setitem__((event, callback.__name__), callback),
            remove_event_listener=lambda event, callback: cls.listeners.pop((event, callback.__name__), None))
        class Base:
            def get_data_path(self):
                return self.data_path
        for name, values in {
            "app": {}, "app.sdk": {}, "app.sdk.plugin": {"_PluginBase": Base},
            "app.sdk.events": {"eventmanager": manager},
            "app.sdk.security": {"verify_token": verify_token},
            "app.schemas": {}, "app.schemas.token": {"TokenPayload": TokenPayload},
            "app.schemas.types": {"ChainEventType": Types, "EventType": Types},
        }.items():
            module = types.ModuleType(name)
            module.__dict__.update(values)
            sys.modules[name] = module
        # Adapter is a host boundary double; its real contract is separately inspected below.
        adapter = types.ModuleType("w01_plugin.mp_adapter")
        adapter.NativeAdapter = Host
        adapter.target_from_native = lambda row: sys.modules["w01_plugin.repository"].Target(row["type"], row["media_source"], str(row["media_id"]), row.get("season"), row.get("episode_group") or "")
        adapter.make_target = lambda *args: sys.modules["w01_plugin.repository"].Target(*args)
        sys.modules[adapter.__name__] = adapter
        spec = importlib.util.spec_from_file_location("w01_plugin", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.TokenPayload = TokenPayload

    def setUp(self):
        self.assertTrue((PLUGIN / "__init__.py").exists(), "W01 plugin lifecycle/API missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugin = self.mod.SubscriBetter()
        self.plugin.data_path = Path(self.tmp.name)
        self.plugin.init_plugin({})

    def test_T150_reload_single_listener_default_safe_and_stale_job(self):
        self.assertFalse(self.plugin.get_state())
        generation = self.plugin.generation
        self.plugin.init_plugin({"enabled": True, "dry_run": True})
        self.assertEqual(6, len(self.listeners))
        self.plugin.ownership.reconcile = Mock()
        self.plugin.reconcile(generation=generation)
        self.plugin.ownership.reconcile.assert_not_called()
        self.plugin.stop_service()
        self.assertFalse(self.plugin.get_state())
        self.assertEqual(4, len(self.listeners))

    def test_delivery_configuration_failure_keeps_ownership_guard_initialized(self):
        self.plugin.init_plugin({'enabled':True,'dry_run':False,'delivery':{'rules':[]}})
        self.assertIn('DELIVERY_CONFIGURATION_FAILED',self.plugin.errors)
        self.assertTrue(hasattr(self.plugin,'guard'))
        self.assertEqual(6,len(self.listeners))

    def create_owned_fixture(self):
        self.plugin.init_plugin({"enabled": True, "dry_run": False})
        request = self.mod.IntentRequest(intent_key="safety-duty", media_type="电视剧", media_source="themoviedb", media_id="123", season=0, name="Fictional")
        task = self.plugin.submit_intent(request, user=self.TokenPayload())
        return task, self.plugin.adapter

    def test_V3_host_lifecycle_keeps_disabled_stopped_ownership_guarded(self):
        import json
        from unittest.mock import patch
        task, host = self.create_owned_fixture()
        self.plugin.repository.set_state(task.id, "STOPPED", "admin")
        with patch.object(self.mod, "NativeAdapter", return_value=host):
            self.plugin.init_plugin({"enabled": False, "dry_run": True})
        # Faithful lifecycle gate: host enables/disables the entire owner class.
        enabled_classes = {type(self.plugin)} if self.plugin.get_state() else set()
        native = host.rows[42]
        origin = "Subscribe|" + json.dumps(native)
        selection = types.SimpleNamespace(event_data=types.SimpleNamespace(origin=origin, contexts=[object()], updated=False, updated_contexts=None),
                                          snapshot=lambda: types.SimpleNamespace(valid=False))
        download = types.SimpleNamespace(event_data=types.SimpleNamespace(origin=origin, cancel=False), snapshot=lambda: types.SimpleNamespace(valid=True))
        completion = types.SimpleNamespace(event_data=types.SimpleNamespace(subscribe=types.SimpleNamespace(**native), cancel=False), snapshot=lambda: types.SimpleNamespace(valid=True))
        for (_, callback), event in zip(self.plugin._listeners()[:3], (selection, download, completion)):
            self.assertIs(callback.__self__, self.plugin)
            if type(callback.__self__) in enabled_classes:
                callback(event)
        self.assertTrue(selection.event_data.updated)
        self.assertEqual([], selection.event_data.updated_contexts)
        self.assertTrue(download.event_data.cancel)
        self.assertTrue(completion.event_data.cancel)
        diagnostics = self.plugin.diagnostics(user=self.TokenPayload())
        self.assertFalse(diagnostics.enabled)
        self.assertFalse(diagnostics.ordinary_work_active)
        self.assertTrue(diagnostics.safety_required)
        self.assertTrue(diagnostics.safety_active)
        self.assertEqual("S", native["state"])
        self.plugin.stop_service()
        self.assertFalse(self.plugin.get_state(), "real stop must still obey the host lifecycle")
        diagnostics = self.plugin.diagnostics(user=self.TokenPayload())
        self.assertTrue(diagnostics.safety_required)
        self.assertFalse(diagnostics.safety_active)

    def test_V3_safety_duty_survives_capability_and_warm_storage_failure(self):
        from unittest.mock import patch
        _, host = self.create_owned_fixture()
        host.capabilities = lambda: ["HOST_CONTRACT_MISMATCH"]
        with patch.object(self.mod, "NativeAdapter", return_value=host):
            self.plugin.init_plugin({"enabled": False, "dry_run": True})
        self.assertTrue(self.plugin.get_state())
        self.assertFalse(self.plugin.diagnostics(user=self.TokenPayload()).ordinary_work_active)
        with patch.object(self.mod, "Repository", side_effect=RuntimeError("store unavailable")):
            self.plugin.init_plugin({"enabled": False, "dry_run": True})
            self.assertTrue(self.plugin.get_state(), "retain the known owned safety duty on warm failure")
            cold = self.mod.SubscriBetter()
            cold.data_path = self.plugin.data_path
            cold.init_plugin({"enabled": False, "dry_run": True})
        self.assertFalse(cold.get_state(), "cold failure cannot infer unknown ownership")
        diagnostics = cold.diagnostics(user=self.TokenPayload())
        self.assertIn("INITIALIZATION_FAILED", diagnostics.errors)
        self.assertFalse(diagnostics.safety_active)

    def test_V3_release_removes_safety_duty_without_enabling_ordinary_work(self):
        from unittest.mock import patch
        task, host = self.create_owned_fixture()
        preview = self.plugin.release_preview(task.id, user=self.TokenPayload())
        self.plugin.release_native(task.id, self.mod.ReleaseRequest(revision=preview.revision), user=self.TokenPayload())
        with patch.object(self.mod, "NativeAdapter", return_value=host):
            self.plugin.init_plugin({"enabled": False, "dry_run": True})
        self.assertFalse(self.plugin.get_state())
        diagnostics = self.plugin.diagnostics(user=self.TokenPayload())
        self.assertFalse(diagnostics.enabled)
        self.assertFalse(diagnostics.safety_required)
        self.assertFalse(diagnostics.safety_active)

    def test_V3_service_contract_separates_scheduler_and_callback_kwargs(self):
        self.plugin.init_plugin({"enabled": True, "dry_run": False})
        service = self.plugin.get_service()[0]
        scheduler = Mock()
        # Mirror scheduler/reconcile.py:442-448, including its own kwargs.
        scheduler.add_job(Mock(), service["trigger"], **(service.get("kwargs") or {}),
                          kwargs={"job_id": "SubscriBetter_ownership"}, replace_existing=True)
        self.assertEqual(60, scheduler.add_job.call_args.kwargs["seconds"])
        callback_kwargs = service.get("func_kwargs") or {}
        self.assertEqual({"generation": self.plugin.generation}, callback_kwargs)
        self.plugin.ownership.reconcile = Mock()
        service["func"](**callback_kwargs)
        self.plugin.ownership.reconcile.assert_called_once_with()
        self.plugin.init_plugin({"enabled": True, "dry_run": False})
        self.plugin.ownership.reconcile = Mock()
        service["func"](**callback_kwargs)
        self.plugin.ownership.reconcile.assert_not_called()

    def test_authenticated_api_rejects_anonymous_invalid_and_dryrun_mutations(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        for api in self.plugin.get_api():
            self.assertEqual("bear", api["auth"])
            self.assertIsNotNone(api["response_model"])
            app.add_api_route(api["path"], api["endpoint"], methods=api["methods"], response_model=api["response_model"])
        client = TestClient(app)
        self.assertEqual(401, client.get("/diagnostics").status_code)
        headers = {"Authorization": "Bearer unit-admin"}
        response = client.get("/diagnostics", headers=headers)
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["enabled"])
        body = {"intent_key": "fictional", "media_type": "电视剧", "media_source": "themoviedb", "media_id": "123", "season": 0, "name": "Fictional"}
        self.assertEqual(409, client.post("/intents", json=body, headers=headers).status_code)
        self.assertEqual(422, client.post("/intents", json=dict(body, path="/etc/passwd"), headers=headers).status_code)
        with self.assertRaises(Exception):
            self.plugin.diagnostics(user=self.TokenPayload(super_user=False))

    def test_W09_discovery_config_api_and_host_scheduler_are_wired_default_unbound(self):
        config = {"enabled": True, "dry_run": False, "discovery": {
            "enabled": True, "rsshub_base_url": "http://rss.internal:1200/proxy/rsshub",
            "cron": "15 7 * * *", "sources": [
                {"id": "weekly", "kind": "rsshub", "route_key": "movie_weekly_best"}]}}
        self.plugin.init_plugin(config)
        self.assertNotIn("INVALID_DISCOVERY_CONFIG", self.plugin.errors)
        paths = {api["path"] for api in self.plugin.get_api()}
        self.assertTrue({"/discovery/sources", "/discovery/test", "/discovery/run",
                         "/discovery/records", "/discovery/reprocess",
                         "/discovery/history/cleanup", "/discovery/sources/retry"} <= paths)
        catalog = self.plugin.discovery_sources(user=self.TokenPayload())
        weekly = next(row for row in catalog["catalog"] if row["route_key"] == "movie_weekly_best")
        self.assertEqual("http://rss.internal:1200/proxy/rsshub/douban/list/movie_weekly_best?limit=50",
                         weekly["full_url"])
        from unittest.mock import patch
        cron = types.ModuleType("apscheduler.triggers.cron")
        cron.CronTrigger = types.SimpleNamespace(from_crontab=lambda value: ("cron", value))
        with patch.dict(sys.modules, {"apscheduler": types.ModuleType("apscheduler"),
                                      "apscheduler.triggers": types.ModuleType("apscheduler.triggers"),
                                      "apscheduler.triggers.cron": cron}):
            jobs = {job["id"]: job for job in self.plugin.get_service()}
        self.assertIn("SubscriBetter_discovery", jobs)
        result = jobs["SubscriBetter_discovery"]["func"](**jobs["SubscriBetter_discovery"]["func_kwargs"])
        self.assertEqual("OWNER_UNBOUND", result["sources"]["weekly"]["reason"])

    def test_W09_discovery_scope_and_inventory_refresh_contract_are_explicit(self):
        archive=types.SimpleNamespace(
            sources=types.SimpleNamespace(libraries={'emby':{'10'}}),
            mappings=types.SimpleNamespace(rules=[{'emby_service':'emby','library_id':'10',
                                                   'media_source':'themoviedb'}]))
        self.plugin.delivery_worker=types.SimpleNamespace(archive=archive,rules={'rule':{'enabled':True}})
        target=sys.modules['w01_plugin.repository'].Target('电视剧','themoviedb','1396',1)
        source=self.mod.SourceConfig(id='tv',kind='custom',url='https://feed.invalid/rss',
                                     destination_templates={'tv':'/downloads/tv'})
        self.assertTrue(self.plugin._discovery_authorized(target,source,'tv'))
        self.assertFalse(self.plugin._discovery_authorized(target,source,'anime'))
        anime=source.model_copy(update={'destination_templates':{'anime':'/downloads/anime'}})
        self.assertTrue(self.plugin._discovery_authorized(target,anime,'anime'))
        self.assertFalse(self.plugin._discovery_authorized(target,anime,'tv'))
        self.assertFalse(self.plugin._discovery_authorized(target,source.model_copy(update={'destination_templates':{}}),'tv'))
        requests=[]
        self.plugin.migration=types.SimpleNamespace(inventory_refresh=lambda request:
            (requests.append(request),{'state':'MISSING','evidence_ref':'archive-probe:test'})[1])
        result=self.plugin._discovery_inventory_refresh(target,source)
        self.assertEqual('MISSING',result['state'])
        self.assertEqual([['emby','10']],requests[0]['library_scopes'])
        self.assertEqual(target.key,requests[0]['target_key'])
        with self.assertRaises(Exception):
            self.mod.DiscoveryReprocessRequest(record_ids=list(range(1,102)))

    def test_W09_discovery_accept_opens_shared_scheduler_scope(self):
        self.plugin.init_plugin({'enabled':True,'dry_run':False})
        movie=sys.modules['w01_plugin.repository'].Target('电影','themoviedb','253774')
        row=self.plugin.ownership.submit('fixture-movie',movie,{'name':'Caminandes'},'fixture')
        source=self.mod.SourceConfig(id='controlled',kind='custom',url='https://feed.invalid/rss',
                                     destination_templates={'movie':'/test-data/downloads/open-film'})
        receipt=self.plugin._discovery_accept(row,movie,source,row['snapshot'])
        self.assertEqual('CONTINUOUS',self.plugin.scheduler.opportunity(receipt['opportunity_id'])['mode'])
        tvplugin=self.mod.SubscriBetter();tvplugin.data_path=Path(self.tmp.name)/'tv'
        tvplugin.init_plugin({'enabled':True,'dry_run':False});self.addCleanup(tvplugin.stop_service)
        tv=sys.modules['w01_plugin.repository'].Target('电视剧','themoviedb','1396',1)
        tvrow=tvplugin.ownership.submit('fixture-tv',tv,{'name':'Fixture TV'},'fixture')
        with self.assertRaisesRegex(ValueError,'TV_SCOPE_UNBOUND'):
            tvplugin._discovery_accept(tvrow,tv,source.model_copy(update={'destination_templates':{'tv':'/tv'}}),tvrow['snapshot'])
        tvplugin.migration=types.SimpleNamespace(discovery_scope=lambda request:{'episodes':[1,3]})
        receipt=tvplugin._discovery_accept(tvrow,tv,source.model_copy(update={'destination_templates':{'tv':'/tv'}}),tvrow['snapshot'])
        self.assertEqual(2,len(receipt['target_units']))

    def test_enabled_authenticated_submission_and_disabled_guard(self):
        self.plugin.init_plugin({"enabled": True, "dry_run": False})
        request = self.mod.IntentRequest(intent_key="working", media_type="电视剧", media_source="themoviedb", media_id="123", season=0, name="Fictional")
        result = self.plugin.submit_intent(request, user=self.TokenPayload())
        self.assertEqual("ACTIVE", result.state)
        self.assertEqual(0, result.season)
        self.assertNotIn("snapshot", result.model_dump())
        import json
        native = self.plugin.adapter.rows[42]
        data = types.SimpleNamespace(origin="Subscribe|" + json.dumps(native), cancel=False)
        event = types.SimpleNamespace(event_data=data, snapshot=lambda: types.SimpleNamespace(valid=True))
        self.plugin.stop_service()
        self.plugin.resource_download(event)
        self.assertTrue(data.cancel)
        self.assertEqual("S", native["state"])

    def test_T150_auto_enable_excludes_existing_and_reenable_disabled_arrivals(self):
        self.plugin.init_plugin({"enabled": True, "dry_run": False, "auto_types": ["电视剧"]})
        native = {"id": 99, "type": "电视剧", "media_source": "themoviedb", "media_id": "777", "season": 0, "episode_group": "", "state": "R"}
        self.plugin.adapter.rows[99] = native
        self.assertTrue(self.plugin._auto_scope(native))
        self.plugin.subscribe_added(types.SimpleNamespace(event_data={"subscribe_id": 99}))
        self.assertEqual("ACTIVE", self.plugin.repository.by_native_id(99)["state"])
        self.assertEqual("S", native["state"])
        self.plugin.init_plugin({"enabled": False, "dry_run": False, "auto_types": ["电视剧"]})
        host = self.plugin.adapter
        host.rows[100] = dict(native, id=100, media_id="888", state="R")
        original = self.mod.NativeAdapter
        self.mod.NativeAdapter = lambda: host
        try:
            self.plugin.init_plugin({"enabled": True, "dry_run": False, "auto_types": ["电视剧"]})
            self.assertFalse(self.plugin._auto_scope(host.rows[100]))
        finally:
            self.mod.NativeAdapter = original

    def test_T150_queued_mutations_recheck_enablement_after_stop(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event, RLock
        from fastapi import HTTPException
        for operation in ("submit", "release", "recover"):
            with self.subTest(operation=operation):
                self.plugin.init_plugin({"enabled": True, "dry_run": False})
                waiting = Event()
                underlying = RLock()
                class ObservedLock:
                    def __enter__(self):
                        waiting.set()
                        underlying.acquire()
                    def __exit__(self, *args):
                        underlying.release()
                self.plugin.runtime_lock = ObservedLock()
                user = self.TokenPayload()
                if operation == "submit":
                    request = self.mod.IntentRequest(intent_key="queued", media_type="电影", media_source="themoviedb", media_id="555", name="Fictional")
                    invoke = lambda: self.plugin.submit_intent(request, user=user)
                    method = "submit"
                elif operation == "release":
                    invoke = lambda: self.plugin.release_native(1, self.mod.ReleaseRequest(revision="0" * 64), user=user)
                    method = "release"
                else:
                    invoke = lambda: self.plugin.recover_native(1, self.mod.RecoveryRequest(native_id=42, confirm_adoption=True), user=user)
                    method = "recover_native"
                mutation = Mock(side_effect=AssertionError("mutation ran after stop"))
                setattr(self.plugin.ownership, method, mutation)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    underlying.acquire()
                    try:
                        future = pool.submit(invoke)
                        self.assertTrue(waiting.wait(3), "request did not reach lock")
                        self.plugin.stop_service()
                    finally:
                        underlying.release()
                    with self.assertRaises(HTTPException) as caught:
                        future.result(timeout=3)
                    self.assertEqual(409, caught.exception.status_code)
                mutation.assert_not_called()


class RecoveryTests(unittest.TestCase):
    setUpClass = OwnershipTests.__dict__["setUpClass"]
    setUp = OwnershipTests.setUp
    submit = OwnershipTests.submit
    def test_unknown_requires_operator_binding_and_target_readback(self):
        original = self.host.create
        def lost(*args):
            original(*args)
            raise RuntimeError("lost")
        self.host.create = lost
        row = self.submit()
        self.assertTrue(hasattr(self.service, "recover_native"), "operator recovery missing")
        self.host.rows[99] = dict(self.host.rows[42], id=99, season=1)
        with self.assertRaises(ValueError):
            self.service.recover_native(row["id"], 99, "admin")
        recovered = self.service.recover_native(row["id"], 42, "admin")
        self.assertEqual("ACTIVE", recovered["state"])
        self.assertEqual(42, recovered["native_id"])
        self.assertEqual("S", self.host.rows[42]["state"])


if __name__ == "__main__":
    unittest.main()
