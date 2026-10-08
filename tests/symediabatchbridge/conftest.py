"""Offline tests use the production app.plugins namespace and fake only MP boundaries."""
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def modules(monkeypatch, tmp_path):
    class Base:
        def get_data_path(self):
            return tmp_path / "plugin-data" / self.__class__.__name__

    class Response(BaseModel):
        success: bool
        message: str = ""

    class Events:
        def __init__(self):
            self.handlers = {}

        def add_event_listener(self, kind, handler):
            self.handlers[kind, handler] = handler

        def remove_event_listener(self, kind, handler):
            self.handlers.pop((kind, handler), None)

    for name in ("app", "app.plugins", "app.core", "app.core.event", "app.log", "app.schemas", "app.schemas.types"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["app.plugins"]._PluginBase = Base
    sys.modules["app.schemas"].Response = Response
    sys.modules["app.log"].logger = Mock()
    sys.modules["app.core.event"].eventmanager = Events()
    sys.modules["app.schemas.types"].EventType = SimpleNamespace(
        TransferComplete="video", SubtitleTransferComplete="subtitle", AudioTransferComplete="audio")
    name = "app.plugins.symediabatchbridge"
    for key in list(sys.modules):
        if key.startswith(name + "."):
            monkeypatch.delitem(sys.modules, key)
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins.v2/symediabatchbridge/__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, plugin)
    spec.loader.exec_module(plugin)
    result = {"plugin": plugin}
    for part in ("domain", "store", "engine", "host", "cd2"):
        result[part] = importlib.import_module(name + "." + part)
    yield SimpleNamespace(**result)
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


@pytest.fixture
def config_values(tmp_path):
    root = tmp_path / "organized"
    root.mkdir()
    return {"enabled": True, "local_root": str(root), "storage": "115网盘Plus",
            "staging": "/MP暂存", "cd2_prefix": "/115", "inbox": "/115/Symedia待归档",
            "cd2_address": "http://cd2:19798", "cd2_token": "test-secret", "interval": 1}
