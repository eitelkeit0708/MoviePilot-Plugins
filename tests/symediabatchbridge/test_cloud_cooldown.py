"""Real HTTP response classification without touching MP's shared provider."""

import ast
import base64
from hashlib import sha1
import importlib
import os
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from typing import List, Optional
from unittest.mock import Mock

import httpx
import pytest


@pytest.fixture
def api(modules, tmp_path, monkeypatch):
    cooldown = importlib.import_module("app.plugins.symediabatchbridge.cooldown")
    requests = importlib.import_module("app.plugins.symediabatchbridge.cloud_requests")
    clock = NS(now=1_800_000_000.0)
    monkeypatch.setattr(cooldown.time, "time", lambda: clock.now)
    store = modules.store.Store(tmp_path / "cooldown")
    provider = NS(_check_session=Mock(), _request_api=Mock(), _api_limiter=NS(acquire=Mock()),
                  _rate_stats=NS(record=Mock()), _limit_until=0, limit_sleep_seconds=3600,
                  base_url="https://proapi.115.com", session=NS(request=Mock()))
    stop = Event()
    guard = cooldown.Cooldown(store)
    ops = requests.NativeOperations(provider, guard, stop)
    return NS(cooldown=cooldown, requests=requests, store=store, provider=provider,
              stop=stop, guard=guard, ops=ops, clock=clock)


def response(status=200, data=None, headers=None):
    return httpx.Response(status, json=data if data is not None else {"code": 0, "state": True, "data": {}},
                          headers=headers, request=httpx.Request("POST", "https://proapi.115.com/open/upload/init"))


def invoke(api, **kwargs):
    return api.ops.request("POST", "/open/upload/init", data={"fileid": "hash"}, **kwargs)


@pytest.mark.parametrize("reply", [response(429), response(data={"code": 999, "message": "已达到当前访问上限"})])
def test_limit_is_shared_across_routes_and_survives_restart(api, modules, reply):
    api.provider.session.request.return_value = reply
    with pytest.raises(api.cooldown.CloudCooldown) as caught:
        invoke(api)
    assert caught.value.next_at == api.clock.now + 3600
    other_store = modules.store.Store(api.store.path.parent)
    other = api.requests.NativeOperations(api.provider, api.cooldown.Cooldown(other_store), Event())
    with pytest.raises(api.cooldown.CloudCooldown):
        other.request("POST", "/open/folder/add")
    assert api.provider.session.request.call_count == 1
    api.provider._request_api.assert_not_called()
    assert api.provider._limit_until == 0  # We never mutate MP's singleton.


def test_retry_after_extends_but_never_shortens_host_cooldown(api):
    api.provider.session.request.return_value = response(429, headers={"Retry-After": "7200"})
    with pytest.raises(api.cooldown.CloudCooldown) as caught:
        invoke(api)
    assert caught.value.next_at == api.clock.now + 7200
    api.guard.defer(api.clock.now + 10, "shorter")
    assert api.guard.status()["until"] == caught.value.next_at


@pytest.mark.parametrize("retry_after", ["1", "invalid", "nan", "-12"])
def test_retry_after_cannot_shorten_the_native_window(api, retry_after):
    api.provider.session.request.return_value = response(429, headers={"Retry-After": retry_after})
    with pytest.raises(api.cooldown.CloudCooldown) as caught:
        invoke(api)
    assert caught.value.next_at == api.clock.now + 3600


def test_current_mp_cooldown_is_imported_without_auth_or_network(api, modules):
    api.provider._limit_until = api.clock.now + 9000
    with pytest.raises(api.cooldown.CloudCooldown):
        invoke(api)
    api.provider._check_session.assert_not_called()
    api.provider.session.request.assert_not_called()
    api.provider._limit_until = 0
    with pytest.raises(api.cooldown.CloudCooldown):
        api.cooldown.Cooldown(modules.store.Store(api.store.path.parent)).before(api.provider)


def test_host_enters_cooldown_while_waiting_for_qps(api):
    api.provider._api_limiter.acquire.side_effect = lambda: setattr(api.provider, "_limit_until", api.clock.now + 3600)
    with pytest.raises(api.cooldown.CloudCooldown):
        invoke(api)
    api.provider.session.request.assert_not_called()


def test_recovery_allows_one_serial_request_without_erasing_deadline(api):
    api.guard.defer(api.clock.now + 3600, "limit")
    api.clock.now += 3601
    api.provider.session.request.return_value = response(data={"code": 0, "state": True, "data": {"status": 2}})
    assert invoke(api)["data"]["status"] == 2
    assert api.provider.session.request.call_count == 1
    assert api.guard.status()["until"] < api.clock.now


@pytest.mark.parametrize("reply, kind", [(response(401), "authentication"), (response(403), "authentication"),
    (response(503), "service"), (response(400), "http"), (response(data={"code": 998, "message": "secret"}), "unknown"),
    (response(data=[]), "unknown"), (None, "unknown")])
def test_other_failures_do_not_invent_a_rate_limit(api, reply, kind):
    api.provider.session.request.return_value = reply
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        invoke(api)
    assert caught.value.kind == kind
    assert "secret" not in str(caught.value)
    assert api.guard.status().get("until", 0) == 0


def test_network_error_is_not_a_miss_or_cooldown(api):
    api.provider.session.request.side_effect = httpx.ConnectError("Authorization: secret")
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        invoke(api)
    assert caught.value.kind == "network" and "secret" not in str(caught.value)
    assert api.guard.status().get("until", 0) == 0


def test_login_refresh_session_and_qps_are_native_and_current(api):
    original_session = api.provider.session
    next_session = NS(request=Mock(return_value=response()))
    api.provider._check_session.side_effect = lambda: setattr(api.provider, "session", next_session)
    invoke(api, retry_limit=99, no_error_log=True)
    original_session.request.assert_not_called()
    next_session.request.assert_called_once()
    assert "retry_limit" not in next_session.request.call_args.kwargs
    api.provider._api_limiter.acquire.assert_called_once()
    api.provider._rate_stats.record.assert_called_once()
    assert api.ops.native is not api.provider
    api.provider._request_api.assert_not_called()


def test_native_login_failure_is_classified_without_sending(api):
    class NoCheckInException(Exception):
        pass
    api.provider._check_session.side_effect = NoCheckInException("secret")
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        invoke(api)
    assert caught.value.kind == "authentication" and "secret" not in str(caught.value)
    api.provider.session.request.assert_not_called()


def test_unknown_endpoint_and_provider_contract_fail_closed(api):
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        api.ops.request("POST", "/new/upload")
    assert caught.value.review and caught.value.kind == "contract"
    api.provider.session.request.assert_not_called()
    with pytest.raises(api.cooldown.CloudRequestError):
        api.requests.NativeOperations(NS(_request_api=Mock()), api.guard, api.stop)


def test_cancel_does_not_enter_auth_or_request(api, modules):
    api.stop.set()
    with pytest.raises(modules.domain.Stopped):
        invoke(api)
    api.provider._check_session.assert_not_called()
    api.provider.session.request.assert_not_called()


@pytest.mark.parametrize("challenge", [False, True])
def test_bounded_instant_initialization_preserves_challenge_and_non_hit(api, modules, tmp_path, challenge):
    local = tmp_path / "media.mkv"
    local.write_bytes(b"media")
    entry = modules.domain.freeze_file(str(local), local.name, api.stop)
    non_hit = response(data={"code": 0, "state": True, "data": {
        "status": 1, "bucket": "bucket", "object": "object", "callback": "callback"}})
    api.provider.session.request.side_effect = ([response(data={"code": 0, "state": True, "data": {
        "code": 700, "sign_check": "1-3", "sign_key": "key", "pick_code": "pick"}})] if challenge else []) + [non_hit]
    result = modules.instant.try_instant(api.provider, "123", entry, api.stop, request=api.ops.request)
    assert result is None
    if challenge:
        assert api.provider.session.request.call_args.kwargs["data"]["sign_val"] == sha1(b"edi").hexdigest().upper()
    assert api.provider.session.request.call_count == (2 if challenge else 1)
    api.provider._request_api.assert_not_called()


def test_bounded_instant_unknown_shape_keeps_explicit_error_type(api, modules, tmp_path):
    local = tmp_path / "media.mkv"
    local.write_bytes(b"media")
    entry = modules.domain.freeze_file(str(local), local.name, api.stop)
    api.provider.session.request.return_value = response(data={"code": 0, "state": True, "data": {"status": 99}})
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        modules.instant.try_instant(api.provider, "123", entry, api.stop, request=api.ops.request)
    assert caught.value.kind == "unknown"


def audited_provider(api):
    root = os.environ.get("MP_INSTANT_CONTRACT_SOURCE")
    if not root:
        pytest.skip("Set MP_INSTANT_CONTRACT_SOURCE for native method contracts")
    source = ast.parse((Path(root) / "mp-u115.py").read_text(encoding="utf-8"))
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "U115Pan")
    names = {"get_folder", "get_item", "get_item_strict", "__get_info_item", "list", "create_folder", "detail",
             "upload", "_calc_sha1", "__build_uploaded_fileitem"}
    cls = ast.ClassDef(name="U115Pan", bases=[], keywords=[], decorator_list=[],
                       body=[node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names])
    namespace = {"Path": Path, "Optional": Optional, "List": List,
                 "schemas": NS(FileItem=lambda **kw: NS(**{"type": None, "fileid": None, **kw})),
                 "logger": Mock(), "StorageQueryError": RuntimeError, "time": time,
                 "U115_GET_INFO_ACCEPTED_CODES": (0, 20004, 430004)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "native-u115-contract", "exec"), namespace)
    native = namespace["U115Pan"]()
    native.__dict__.update(api.provider.__dict__)
    native.schema = NS(value="u115")
    native._calc_sha1 = lambda path, size=None: sha1(path.read_bytes()[:size]).hexdigest()
    return native


def test_actual_mp_directory_lookup_cannot_swallow_limit_and_then_create(api):
    native = audited_provider(api)
    native.session.request.return_value = response(429)
    ops = api.requests.NativeOperations(native, api.guard, api.stop)
    with pytest.raises(api.cooldown.CloudCooldown):
        ops.call("get_folder", Path("/stage/batch"))
    assert native.session.request.call_count == 1
    native._request_api.assert_not_called()


def test_actual_mp_directory_known_not_found_can_list_and_create(api):
    native = audited_provider(api)
    native.session.request.side_effect = [response(data={"code": 430004, "state": False}),
        response(data={"code": 0, "state": True, "data": []}),
        response(data={"code": 0, "state": True, "data": {"file_id": "123"}})]
    ops = api.requests.NativeOperations(native, api.guard, api.stop)
    result = ops.call("get_folder", Path("/stage"))
    assert result.fileid == "123" and result.path == "/stage/"
    assert native.session.request.call_count == 3
    native._request_api.assert_not_called()


def test_actual_mp_normal_upload_aborts_at_rate_limit_before_oss(api, tmp_path):
    native = audited_provider(api)
    native.session.request.return_value = response(429)
    ops = api.requests.NativeOperations(native, api.guard, api.stop)
    local = tmp_path / "video.mkv"
    local.write_bytes(b"media")
    with pytest.raises(api.cooldown.CloudCooldown):
        ops.call("upload", target_dir=NS(path="/stage", fileid="123"), local_path=local, new_name=local.name)
    assert native.session.request.call_count == 1


def test_actual_mp_normal_upload_keeps_native_oss_algorithm(api, tmp_path):
    native = audited_provider(api)
    local = tmp_path / "video.mkv"
    local.write_bytes(b"media")
    uploaded = []
    def upload_part(bucket_name, upload_id, part_number, data):
        uploaded.append(data.read())
        return NS(etag="etag")
    bucket = NS(init_multipart_upload=Mock(return_value=NS(upload_id="upload-id")),
                upload_part=upload_part,
                complete_multipart_upload=Mock(return_value=NS(status=200, resp=NS(response=NS(json=lambda: {"state": True})))))
    namespace = type(native).upload.__globals__
    namespace.update({"oss2": NS(StsAuth=Mock(), Bucket=Mock(return_value=bucket),
        utils=NS(b64encode_as_string=lambda value: base64.b64encode(value.encode()).decode()),
        exceptions=NS(OssError=RuntimeError)),
        "determine_part_size": lambda file_size, preferred_size: preferred_size,
        "StringUtils": NS(str_filesize=str), "transfer_process": lambda path: Mock(),
        "global_vars": NS(is_transfer_stopped=lambda path: False),
        "SizedFileAdapter": lambda fileobj, size: fileobj, "PartInfo": lambda number, etag: (number, etag)})
    native._U115Pan__get_upload_part_size = lambda size: 1024
    native.session.request.side_effect = [response(data={"code": 0, "state": True, "data": {
        "status": 1, "bucket": "bucket", "object": "object", "callback": {"callback": "a", "callback_var": "b"}}}),
        response(data={"code": 0, "state": True, "data": {
            "endpoint": "https://oss.invalid", "AccessKeyId": "key", "AccessKeySecret": "secret", "SecurityToken": "token"}}),
        response(data={"code": 0, "state": True, "data": {}}),
        response(data={"code": 430004, "state": False})]
    ops = api.requests.NativeOperations(native, api.guard, api.stop)
    result = ops.call("upload", target_dir=NS(path="/stage", fileid="123"), local_path=local, new_name=local.name)
    assert result.path == "/stage/video.mkv" and result.size == 5
    assert uploaded == [b"media"]
    assert native.session.request.call_count == 4
    native._request_api.assert_not_called()


def test_unknown_native_directory_structure_fails_before_network(api):
    native = audited_provider(api)
    type(native).get_folder = lambda self, path: self.session.request("POST", "/unexpected")
    with pytest.raises(api.cooldown.CloudRequestError) as caught:
        api.requests.NativeOperations(native, api.guard, api.stop).call("get_folder", Path("/stage"))
    assert caught.value.kind == "contract" and caught.value.review
    native.session.request.assert_not_called()


def test_host_guard_blocks_directory_instant_and_normal_upload(native, api, monkeypatch):
    # Fixture is intentionally imported locally below: production host, fake only
    # the MP client boundary. No HTTP request or cloud directory is created.
    monkeypatch.setattr(importlib.import_module("app.plugins.symediabatchbridge.host"), "native_provider", lambda: api.provider)
    native.host.bind_cooldown(api.guard, api.stop)
    api.guard.defer(api.clock.now + 3600, "limit")
    for operation in (lambda: native.host.gate_cloud(),
                      lambda: native.host.try_instant({}, "/stage/test.mkv", api.stop),
                      lambda: native.host.upload(native.video, "/stage/test.mkv")):
        with pytest.raises(api.cooldown.CloudCooldown):
            operation()
    native.storage.get_folder.assert_not_called()
    native.storage.upload_file.assert_not_called()
    api.provider.session.request.assert_not_called()


from test_adapters import native  # noqa: E402, F401
