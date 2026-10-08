from hashlib import sha1
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
from typing import Optional
import ast
import os

import pytest
from cryptography.hazmat.primitives import hashes


@pytest.fixture
def entry(modules, tmp_path):
    path = tmp_path / "video.mkv"
    path.write_bytes(b"video-content")
    return modules.domain.freeze_file(str(path), "video.mkv", Event())


def test_native_non_hit_never_calls_upload_or_oss(modules, entry):
    provider = NS(_request_api=Mock(return_value={"state": True, "data": {
        "status": 1, "bucket": "bucket", "object": "object", "callback": "callback"}}))
    assert modules.instant.try_instant(provider, "123", entry, Event()) is None
    provider._request_api.assert_called_once_with("POST", "/open/upload/init", retry_limit=0, data={
        "file_name": "video.mkv", "file_size": entry["size"], "target": "U_1_123",
        "fileid": entry["sha1"], "preid": entry["preid"]})


def test_native_second_verification_and_hit(modules, entry):
    requests = []
    responses = iter([{"state": True, "data": {"code": 700, "sign_check": "2-5", "sign_key": "key", "pick_code": "pick"}},
                      {"state": True, "data": {"status": 2, "file_id": 987}}])
    def request(method, endpoint, **kwargs):
        requests.append(dict(kwargs["data"]))
        return next(responses)
    receipt = modules.instant.try_instant(NS(_request_api=request), "123", entry, Event())
    assert receipt == {"size": entry["size"], "fileid": "987", "method": "instant"}
    assert "sign_val" not in requests[0]
    assert requests[1]["sign_val"] == sha1(b"deo-").hexdigest().upper()


@pytest.mark.parametrize("response", [None, {}, {"state": False}, {"state": True, "data": None},
    {"state": True, "data": {"status": 1}}, {"state": True, "data": {"status": 9}},
    {"state": True, "data": {"code": 701, "sign_check": "-2-3", "sign_key": "x"}},
    {"state": True, "data": {"code": 700, "sign_check": "0-9999", "sign_key": "x"}}])
def test_native_unknown_or_error_response_is_not_a_non_hit(modules, entry, response):
    with pytest.raises(modules.domain.BridgeError):
        modules.instant.try_instant(NS(_request_api=Mock(return_value=response)), "123", entry, Event())


def test_native_host_cooldown_does_not_sleep_in_worker(modules, entry):
    provider = NS(_request_api=Mock(), _limit_until=10**12)
    with pytest.raises(modules.domain.BridgeError, match="冷却"):
        modules.instant.try_instant(provider, "123", entry, Event())
    provider._request_api.assert_not_called()


def test_missing_provider_contract_is_held(modules, entry):
    with pytest.raises(modules.domain.BridgeError) as error:
        modules.instant.try_instant(NS(), "123", entry, Event())
    assert error.value.review


def test_stop_prevents_probe(modules, entry):
    stop = Event()
    stop.set()
    provider = NS(_request_api=Mock())
    with pytest.raises(modules.domain.Stopped):
        modules.instant.try_instant(provider, "123", entry, stop)
    provider._request_api.assert_not_called()


@pytest.mark.parametrize("challenge", [False, True])
def test_initialization_matches_actual_mp_v2_upload_source(modules, entry, challenge):
    """Run the audited host upload method through its instant-success branch.

    The host's requests, hashing, and challenge payload are compared with our
    init-only adapter. No MP or 115 server is contacted.
    """
    root = os.environ.get("MP_INSTANT_CONTRACT_SOURCE")
    if not root:
        pytest.skip("Set MP_INSTANT_CONTRACT_SOURCE to the audited upload source cache")
    path = Path(root) / "mp-u115-pinned.py"
    source = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "U115Pan")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "upload")
    namespace = {"Path": Path, "Optional": Optional, "schemas": NS(FileItem=dict), "logger": Mock(), "hashes": hashes}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)

    def boundary():
        calls = []
        def request(method, endpoint, **kwargs):
            calls.append((method, endpoint, dict(kwargs["data"])))
            if challenge and len(calls) == 1:
                return {"state": True, "data": {"code": 701, "sign_check": "2-5", "sign_key": "key", "pick_code": "pick"}}
            return {"state": True, "data": {"status": 2}}
        return calls, request
    expected, native_request = boundary()
    native = NS(_request_api=native_request, _calc_sha1=lambda path, size=None: sha1(path.read_bytes()[:size]).hexdigest(),
                get_item=lambda path: NS(size=entry["size"]))
    namespace["upload"](native, NS(fileid="123", path="/stage"), Path(entry["local"]))
    actual, request = boundary()
    assert modules.instant.try_instant(NS(_request_api=request), "123", entry, Event())
    assert actual == expected
