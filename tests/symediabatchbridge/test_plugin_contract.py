import ast
from datetime import datetime
import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from typing import Optional
from unittest.mock import Mock

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def plugin(modules, config_values, monkeypatch):
    host = NS(in_scope=lambda row: True, histories_since=Mock(return_value=[]),
              transfers=NS(get=Mock()), collect=Mock(), bind_cooldown=Mock(), gate_cloud=Mock())
    host.for_config = lambda config: host
    clouds = []
    def cloud_factory(config, stop):
        cloud = NS(close=Mock(), stop=stop)
        clouds.append(cloud)
        return cloud
    monkeypatch.setattr(modules.plugin, "MPHost", lambda config: host)
    monkeypatch.setattr(modules.plugin, "CD2", cloud_factory)
    p = modules.plugin.SymediaBatchBridge()
    p.init_plugin(config_values)
    assert p.get_state()
    return NS(p=p, host=host, clouds=clouds, values=config_values, modules=modules)


def test_metadata_manifest_version_and_native_form(modules):
    cls = modules.plugin.SymediaBatchBridge
    meta = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))[cls.__name__]
    for field, key in [("plugin_version", "version"), ("plugin_name", "name"), ("plugin_desc", "description"),
                       ("plugin_author", "author"), ("auth_level", "level"), ("plugin_icon", "icon")]:
        assert getattr(cls, field) == meta[key]
    assert list(meta["history"])[0] == "v" + cls.plugin_version
    assert "v2" not in meta and meta.get("release") is not True and meta["v3"] is False
    assert (ROOT / "plugins.v2" / cls.__name__.lower() / "__init__.py").is_file()
    form, defaults = cls().get_form()
    assert "VForm" in json.dumps(form) and defaults["enabled"] is False


def test_empty_config_repeat_stop_and_reload_are_safe(plugin):
    p, old = plugin.p, plugin.p._runtime
    p.init_plugin(plugin.values)
    assert old.stop.is_set() and p._runtime is not old
    plugin.clouds[0].close.assert_called_once()
    assert len(p._listeners) == 3
    p.stop_service()
    p.stop_service()
    assert not p.get_state() and p.get_service() == []
    assert not plugin.modules.plugin.eventmanager.handlers
    p.init_plugin(None)
    assert not p.get_state()
    assert p.get_page()


def test_instances_do_not_share_events_state_or_service_ids(plugin, modules):
    cls = type("SymediaBatchBridgeCopy", (modules.plugin.SymediaBatchBridge,), {})
    other = cls()
    other.init_plugin(plugin.values)
    assert plugin.p._lifecycle is not other._lifecycle
    assert plugin.p._runtime.stop is not other._runtime.stop
    assert plugin.p._store.path != other._store.path
    assert plugin.p.get_service()[0]["id"] != other.get_service()[0]["id"]
    assert other.get_service()[0]["id"].startswith("SymediaBatchBridgeCopy.")


def test_invalid_enabled_config_does_not_crash_or_leak_token(plugin):
    plugin.p.init_plugin({**plugin.values, "inbox": "/outside", "cd2_token": "secret-new"})
    assert not plugin.p.get_state()
    page = json.dumps(plugin.p.get_page(), ensure_ascii=False)
    assert "secret-new" not in page and "目录" in page


def add_job(p):
    p._store.observe(instance=p.__class__.__name__, download_hash="hash", downloader="qb",
                     title="同名作品", history_id=1, routing=p._runtime.config.routing())
    return p._store.jobs()[0]


def test_retry_keeps_manifest_and_move_receipt_and_no_secret_in_page(plugin):
    p, request = plugin.p, plugin.modules.plugin.RetryRequest
    job = add_job(p)
    job.update(state="review", move_requested=True, files=[{"relative": "x"}], source="/115/staging/batch", destination="/115/inbox/batch")
    p._store.save(job)
    assert p.retry_batch(request(key=job["id"])).success
    saved = p._store.get(job["id"])
    assert saved["move_requested"] and saved["files"] == job["files"]
    assert saved["state"] == "waiting" and saved["attempts"] == 0
    assert "test-secret" not in json.dumps(p.get_page())
    saved["state"] = "handed_off"
    p._store.save(saved)
    assert not p.retry_batch(request(key=job["id"])).success


def test_busy_worker_cannot_be_reset_by_api(plugin):
    p = plugin.p
    job = add_job(p)
    with p._store.worker_lock() as acquired:
        assert acquired
        response = p.retry_batch(plugin.modules.plugin.RetryRequest(key=job["id"]))
        assert not response.success
    assert p._store.get(job["id"])["message"] != "已安排重新检查"


def test_retry_preserves_per_file_instant_schedule_and_page_explains_it(plugin):
    p = plugin.p
    job = add_job(p)
    job.update(state="waiting_instant", files=[{"relative": "作品/视频.mkv", "uploaded": False,
               "instant_misses": 24, "instant_started_at": 1_800_000_000, "instant_next_at": 1_800_086_400}])
    p._store.save(job)
    assert p.retry_batch(plugin.modules.plugin.RetryRequest(key=job["id"])).success
    assert p._store.get(job["id"])["files"] == job["files"]
    p.view_records(plugin.modules.plugin.ViewRequest(key=job["id"]))
    page = json.dumps(p.get_page(), ensure_ascii=False)
    assert "秒传未命中 24/24" in page and "普通上传" in page and "视频.mkv" in page


def test_only_native_storage_and_explicit_legacy_batch_switch(plugin):
    p, modules = plugin.p, plugin.modules
    assert modules.domain.Config.parse({**plugin.values, "storage": "115网盘Plus"}).storage == "u115"
    form, defaults = p.get_form()
    assert defaults["storage"] == "u115" and "VSelect" not in json.dumps(form)
    job = add_job(p)
    job["routing"]["storage"] = "115网盘Plus"
    job["files"] = [{"relative": "old.mkv", "uploaded": True, "receipt": {"size": 5}}]
    p._store.save(job)
    assert not p.retry_batch(modules.plugin.RetryRequest(key=job["id"])).success
    assert p._store.get(job["id"])["routing"]["storage"] == "115网盘Plus"
    assert p.retry_batch(modules.plugin.RetryRequest(key=job["id"], switch_to_native=True)).success
    saved = p._store.get(job["id"])
    assert saved["routing"]["storage"] == "u115" and saved["files"] == job["files"]


def test_legacy_switch_cannot_retarget_paths(plugin):
    p = plugin.p
    job = add_job(p)
    job["routing"].update(storage="115网盘Plus", inbox="/115/old-inbox")
    p._store.save(job)
    assert not p.retry_batch(plugin.modules.plugin.RetryRequest(key=job["id"], switch_to_native=True)).success
    assert p._store.get(job["id"])["routing"] == job["routing"]


def test_history_recovery_and_event_replay_share_one_batch(plugin):
    p = plugin.p
    row = NS(id=1, title="作品", download_hash="hash", downloader="qb", date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    plugin.host.histories_since.return_value = [row]
    plugin.host.transfers.get.return_value = row
    p._recover_history(p._runtime)
    p.on_transfer(NS(event_data={"transfer_history_id": 1}))
    assert len(p._store.jobs()) == 1
    assert p._store.meta("history_cursor") is not None


def test_history_outage_does_not_block_existing_batch_or_advance_cursor(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    cursor = "2026-10-08 10:00:00"
    p._store.set_meta("history_cursor", cursor)
    plugin.host.histories_since.side_effect = RuntimeError("database temporarily unavailable")
    processed = []
    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", lambda self, item: processed.append(item["id"]))
    p.check_batches()
    assert processed == [job["id"]]
    assert p._store.meta("history_cursor") == cursor
    assert "已入队批次继续处理" in p._message


def test_cleanup_route_failure_is_persisted_and_does_not_block_next_job(plugin, monkeypatch):
    p = plugin.p
    first = add_job(p)
    first.update(state="handed_off", cleanup_local=True)
    first["routing"]["cd2_prefix"] = "/old-drive"
    p._store.save(first)
    p._store.observe(instance=p.__class__.__name__, download_hash="second", downloader="qb", title="下一批",
                     history_id=2, routing=p._runtime.config.routing())
    second = next(j for j in p._store.jobs() if j["id"] != first["id"])
    second.update(state="handed_off", cleanup_local=True)
    p._store.save(second)
    p._delete_local = True
    cleanup = Mock()
    monkeypatch.setattr(plugin.modules.plugin, "cleanup", cleanup)
    p.check_batches()
    saved = p._store.get(first["id"])
    assert saved["cleanup_error"] and saved["cleanup_next"] > saved["updated"]
    assert not saved.get("cleanup_done")
    assert cleanup.call_args.args[1]["id"] == second["id"]
    assert p._store.meta("last_check")
    assert any(e["kind"] == "cleanup_error" for e in p._store.events(first["id"]))


def test_archive_directory_browser_schedules_verification_and_preserves_move_intent(plugin):
    p, modules = plugin.p, plugin.modules
    job = add_job(p)
    job.update(state="review", move_requested=True)
    p._store.save(job)
    p._runtime.cloud.directories = Mock(return_value=["/115/归档"])
    request = modules.plugin.RecoveryRequest
    assert p.recover_batch(request(key=job["id"])).success
    page = json.dumps(p.get_page(), ensure_ascii=False)
    assert "选择归档后的作品目录" in page and "归档" in page
    assert p.recover_batch(request(key=job["id"], path="/115/归档/作品", verify=True)).success
    saved = p._store.get(job["id"])
    assert saved["state"] == "review" and saved["move_requested"] and saved["next_check"] == 0
    assert saved["recovery_directory"] == "/115/归档/作品"
    assert p._recovery_browser is None


@pytest.mark.parametrize("path", ["/115", "/115/MP暂存/a", "/115/Symedia待归档/a", "/other/a", "/115/../a"])
def test_recovery_rejects_unsafe_directory_without_state_change(plugin, path):
    p = plugin.p
    job = add_job(p)
    job.update(state="review", move_requested=True)
    p._store.save(job)
    assert not p.recover_batch(plugin.modules.plugin.RecoveryRequest(key=job["id"], path=path, verify=True)).success
    assert not p._store.get(job["id"]).get("recovery_directory")


@pytest.mark.parametrize("clone", [False, True])
def test_all_endpoints_require_active_admin_before_any_work(plugin, monkeypatch, clone):
    import sys
    p = plugin.p
    if clone:
        p = type("SymediaBatchBridgeCopy", (type(p),), {})()
        p.init_plugin(plugin.values)
    calls = []
    for name in ("retry_batch", "view_records", "scan_existing", "import_existing", "recover_batch"):
        monkeypatch.setattr(p, name, lambda *a: calls.append(a) or {"success": True})
    def active(authorization: str = Header(default="")):
        if authorization == "Bearer inactive-admin":
            raise HTTPException(403)
        if authorization not in ("Bearer admin", "Bearer regular"):
            raise HTTPException(401)
        return NS(is_superuser=authorization == "Bearer admin")
    monkeypatch.setattr(sys.modules["app.db.user_oper"], "get_current_active_user", active)
    app = FastAPI()
    for route in p.get_api():
        app.add_api_route(route["path"], route["endpoint"], methods=route["methods"])
    client = TestClient(app)
    payloads = {"retry": {"key": "batch"}, "view": {}, "scan": {}, "import": {"history_id": 1},
                "recovery": {"key": "batch", "path": "/115/归档", "verify": True}}
    for name, body in payloads.items():
        for token, status in [("", 401), ("regular", 403), ("inactive-admin", 403)]:
            response = client.post("/" + name, json=body, headers={"Authorization": "Bearer " + token})
            assert response.status_code == status
        assert not calls
        assert client.post("/" + name, json=body, headers={"Authorization": "Bearer admin"}).status_code == 200
        assert len(calls) == 1
        calls.clear()


def test_busy_event_callback_is_recovered_from_native_history(plugin, monkeypatch):
    p = plugin.p
    row = NS(id=2, title="作品", download_hash="hash", downloader="qb", date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    plugin.host.transfers.get.return_value = row
    plugin.host.histories_since.return_value = [row]
    with p._store.worker_lock() as acquired:
        assert acquired
        p.on_transfer(NS(event_data={"transfer_history_id": 2}))
    assert not p._store.jobs()
    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", lambda self, item: None)
    p.check_batches()
    assert len(p._store.jobs()) == 1
    assert p._store.jobs()[0]["history_id"] == 2


def test_recovery_notification_is_throttled_persisted_and_optional(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    now = 1_800_000_000
    monkeypatch.setattr(plugin.modules.plugin.time, "time", lambda: now)
    job.update(state="review", message="CD2 令牌已过期")
    p._notify_issue(p._runtime, job)
    p._flush_notifications()
    assert p.post_message.call_count == 1
    saved = p._store.get(job["id"])
    assert saved["notified_at"] == now
    p._notify_issue(p._runtime, saved)
    p._flush_notifications()
    assert p.post_message.call_count == 1
    now += 86400
    p._notify_issue(p._runtime, saved)
    p._flush_notifications()
    assert p.post_message.call_count == 2
    p._notify = False
    now += 86400
    p._notify_issue(p._runtime, saved)
    p._flush_notifications()
    assert p.post_message.call_count == 2


def test_long_instant_error_and_post_handoff_attachment_notify(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    now = 1_800_000_000
    monkeypatch.setattr(plugin.modules.plugin.time, "time", lambda: now)
    job.update(state="waiting_instant", files=[{"instant_error_since": now - 86401}])
    p._notify_issue(p._runtime, job)
    p._flush_notifications()
    assert p.post_message.call_count == 1
    job.update(state="handed_off", files=[], history_ids=[1], notified_at=now - 86401)
    p._store.save(job)
    row = NS(id=3, title="作品", download_hash="hash", downloader="qb")
    p._observe(p._runtime, row)
    p._flush_notifications()
    assert p.post_message.call_count == 2
    assert p._store.get(job["id"])["late_history_ids"] == [3]


def test_new_route_cannot_watch_an_old_pending_staging_directory(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    old = job["routing"]["staging"]
    p.init_plugin({**plugin.values, "staging": "/new-stage", "inbox": "/115" + old})
    processed = Mock()
    monkeypatch.setattr(plugin.modules.plugin.Engine, "process", processed)
    p.check_batches()
    processed.assert_not_called()
    saved = p._store.get(job["id"])
    assert saved["state"] == "review" and "重叠" in saved["message"]
    assert saved["routing"] == job["routing"]


def test_native_registered_route_accepts_page_render_json_and_requires_bearer(plugin, monkeypatch):
    """Execute the pinned host's registration functions when its source is supplied.

    MP DB/scheduler and token issuer are test doubles; real FastAPI auth dependencies,
    native registration, endpoint validation, and HTTP request dispatch are exercised.
    """
    source_root = os.environ.get("MP_V2_CONTRACT_SOURCE")
    if not source_root:
        pytest.skip("Set MP_V2_CONTRACT_SOURCE to the audited MP V2 source cache")
    path = Path(source_root) / "app__api__endpoints__plugin.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_update_plugin_api_routes", "_remove_routes", "_clean_protected_routes"}
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == 3
    app = FastAPI()
    p = plugin.p
    job = add_job(p)

    users = {"admin": NS(is_superuser=True, is_active=True),
             "regular": NS(is_superuser=False, is_active=True),
             "inactive": NS(is_superuser=True, is_active=False)}

    def verify_token(authorization: Optional[str] = Header(default=None)):
        identities = {"Bearer test-login": "admin", "Bearer regular-login": "regular",
                      "Bearer inactive-login": "inactive", "Bearer deleted-login": "deleted"}
        if authorization not in identities:
            raise HTTPException(401)
        return NS(sub=identities[authorization])

    # Execute the actual host user lookup and active-account guard. Only the
    # token issuer and DB storage boundary are simulated, not role enforcement.
    import sys
    user_path = Path(source_root) / "app__db__user_oper.py"
    user_source = ast.parse(user_path.read_text(encoding="utf-8"))
    user_functions = [n for n in user_source.body if isinstance(n, ast.FunctionDef)
                      and n.name in {"get_current_user", "get_current_active_user"}]
    assert len(user_functions) == 2
    class User:
        @staticmethod
        def get(db, rid):
            return users.get(rid)
    user_namespace = {"Depends": Depends, "HTTPException": HTTPException, "Session": object,
                      "User": User, "schemas": NS(TokenPayload=NS), "get_db": lambda: object(),
                      "verify_token": verify_token}
    exec(compile(ast.Module(body=user_functions, type_ignores=[]), str(user_path), "exec"), user_namespace)
    monkeypatch.setattr(sys.modules["app.db.user_oper"], "get_current_active_user", user_namespace["get_current_active_user"])

    def verify_apikey():
        raise AssertionError("page action must not require the integration API key")

    class Manager:
        def get_plugin_apis(self, pid):
            return [{**route, "path": "/" + pid + route["path"]} for route in p.get_api()]

    namespace = {"Optional": Optional, "app": app, "PluginManager": Manager, "Depends": Depends,
                 "verify_token": verify_token, "verify_apikey": verify_apikey, "logger": Mock(),
                 "PLUGIN_PREFIX": "/api/v1/plugin", "PLUGIN_V2_PREFIX": "/api/v2/plugin", "PROTECTED_ROUTES": []}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    namespace["_update_plugin_api_routes"](p.__class__.__name__, "add")
    client = TestClient(app)
    for prefix in ("/api/v1", "/api/v2"):
        url = prefix + "/plugin/SymediaBatchBridge/retry"
        assert client.post(url, json={"key": job["id"]}).status_code == 401
        assert client.post(url, json={"key": job["id"]}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post(url, json={"key": job["id"]}, headers={"Authorization": "Bearer test-login"}).json()["success"]
        assert client.post(url, json={}, headers={"Authorization": "Bearer test-login"}).status_code == 422
        for token in ("regular-login", "inactive-login", "deleted-login"):
            for route, body in [("retry", {"key": job["id"]}), ("view", {}), ("scan", {}),
                                ("import", {"history_id": 1}), ("recovery", {"key": job["id"]})]:
                protected = prefix + "/plugin/SymediaBatchBridge/" + route
                assert client.post(protected, json=body, headers={"Authorization": "Bearer " + token}).status_code == 403
        users["admin"].is_superuser = False
        assert client.post(url, json={"key": job["id"]}, headers={"Authorization": "Bearer test-login"}).status_code == 403
        users["admin"].is_superuser = True
