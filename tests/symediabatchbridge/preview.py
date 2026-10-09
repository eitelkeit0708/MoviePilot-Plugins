"""Local visual harness: real plugin JSON/actions, fake MP boundaries and data.

No production configuration, credentials, scheduler or cloud connections are used.
Run with the test venv; serves only an explicit allowlist on 127.0.0.1:4186.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlsplit
import json
import importlib
import mimetypes
import sys
import time

import pytest
from conftest import modules as modules_fixture
from test_plugin_contract import plugin as plugin_fixture

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "dist/symedia-ui-preview/vendor"
TMP = TemporaryDirectory(prefix="sbb-design-preview-")
base = Path(TMP.name)
patch = pytest.MonkeyPatch()
gen = modules_fixture.__wrapped__(patch, base)
modules = next(gen)
local = base / "追更"
local.mkdir()
values = {"enabled": True, "local_root": str(local), "route_name": "追更", "storage": "u115", "staging": "/MP暂存/追更",
          "cd2_prefix": "/115", "inbox": "/115/transfer/115upload", "cd2_address": "http://preview.invalid:19798", "cd2_token": "preview-only",
          "route_count": 3, "route_2_route_name": "入库", "route_2_local_root": str(base / "入库"), "route_2_staging": "/MP暂存/入库",
          "route_2_inbox": "/115/transfer/LYZ", "route_3_route_name": "涂佩", "route_3_local_root": str(base / "涂佩"),
          "route_3_staging": "/MP暂存/涂佩", "route_3_inbox": "/115/transfer/tupei", "interval": 1}
p = plugin_fixture.__wrapped__(modules, values, patch).p
for route in p._runtime.routes:
    Path(route.local_root).mkdir(exist_ok=True)
p._runtime.host.histories_since.return_value = []
p._runtime.cloud.directories = lambda path: [path.rstrip("/") + "/最终作品目录"] if path == "/115" else []
names = ["花儿与少年", "魔女嘉莉", "大理石厅谋杀案", "消失的裂痕", "长安的荔枝", "星际穿越"]
for i in reversed(range(31)):
    route = p._runtime.routes[0 if i < 20 else 1 if i < 27 else 2]
    state = "review" if i < 3 else "hashing" if i == 3 else "waiting_instant" if i < 15 else "handed_off"
    title = names[i] if i < len(names) else f"媒体批次 {i+1:02}"
    job = p._store.observe(instance="SymediaBatchBridge", download_hash=str(i), downloader="qb", title=title, history_id=i+1,
                           routing=route.routing(), route_name=route.name)
    files = [{"relative": f"{title}/Season 1/S01E{k+1:02}.mkv", "local": str(Path(route.local_root)/title/f"S01E{k+1:02}.mkv"),
              "size": 7623566950, "uploaded": state=="handed_off", "sha1": "1"*40, "instant_misses": 1,
              "instant_next_at": time.time()+2700} for k in range(6 if i==3 else 3 if i==1 else 1)]
    job.update(state=state, next_check=time.time()+2340 if i!=3 and state!="handed_off" else 0, files=files,
               message="整理文件不存在或不在配置的本地目录中" if i<2 else "现存批次只有字幕，没有对应媒体文件" if i==2 else
                       "正在计算 HASH" if i==3 else "等待秒传重试" if i<15 else "整目录已移交 Symedia 待归档目录")
    if i == 2:
        subtitle = Path(route.local_root)/title/"字幕.ass"
        subtitle.parent.mkdir(parents=True, exist_ok=True)
        subtitle.write_text('Disposable local UI preview subtitle', encoding='utf-8')
        job.update(origin="inventory", files=[], inventory_files=[{"local": str(subtitle), "history_id": i+1,
                   "signature": modules.domain.file_signature(subtitle)}])
    if i == 3:
        job["hash_progress"] = {"file": files[0]["relative"], "done": 3529711497, "total": 7623566950, "at": time.time()}
    p._store.save(job)
    if i == 3:
        p._view = modules.plugin.ViewRequest(expanded=job["id"])
p._store.set_meta("last_check", time.time())
p._store.set_meta("last_check_status", "检查完成")
p._store.set_meta("last_scan", {"read": 0, "matched": 0, "new": 0})
p._store.set_meta("inventory_status", {"at": time.time()-3600, "message": "已接管 17 批"})


class Handler(BaseHTTPRequestHandler):
    def json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/page":
            for module in ("remedies", "page", "dashboard"):
                importlib.reload(importlib.import_module("app.plugins.symediabatchbridge." + module))
            return self.json(p.get_page())
        if path == "/api/meta":
            return self.json({"title": p.plugin_name, "version": p.plugin_version})
        allowed = {"/": Path(__file__).with_name("preview.html"),
                   "/logo.png": ROOT/"icons/115InstantUpload.png",
                   "/vendor/vue.js": VENDOR/"vue.js", "/vendor/vuetify.js": VENDOR/"vuetify.js",
                   "/vendor/vuetify.css": VENDOR/"vuetify.css", "/vendor/mdi.css": VENDOR/"mdi.css",
                   "/fonts/materialdesignicons-webfont.woff2": VENDOR/"mdi.woff2"}
        target = allowed.get(path)
        if target is None or not target.is_file():
            return self.json({"error": "Not found"}, 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            if self.headers.get("Origin", "http://127.0.0.1:4186") != "http://127.0.0.1:4186":
                return self.json({"error": "Origin rejected"}, 403)
            count = int(self.headers.get("Content-Length", "0"))
            if not 0 <= count <= 16000:
                return self.json({"error": "Too large"}, 400)
            payload = json.loads(self.rfile.read(count))
            endpoint = self.path.rsplit("/", 1)[-1]
            if endpoint == "view":
                result = p.view_records(modules.plugin.ViewRequest(**payload))
            elif endpoint == "retry":
                result = p.retry_batch(modules.plugin.RetryRequest(**payload))
            elif endpoint == "recovery":
                result = p.recover_batch(modules.plugin.RecoveryRequest(**payload))
            elif endpoint == "scan":
                result = p.scan_existing()
            elif endpoint == "dispose":
                result = p.dispose_batch(modules.plugin.DisposalRequest(**payload))
            else:
                return self.json({"error": "Preview action unavailable"}, 400)
            self.json(result.model_dump())
        except Exception as error:
            self.json({"error": type(error).__name__}, 400)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print("Native PageRender preview at http://127.0.0.1:4186 (synthetic data)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 4186), Handler).serve_forever()
