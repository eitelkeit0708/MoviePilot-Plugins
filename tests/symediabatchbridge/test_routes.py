from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from hashlib import sha1
import copy
import json
import shutil
import subprocess

import pytest


def route_values(base, tmp_path):
    values = {**base, "route_count": 3}
    for index, name in enumerate(("入库", "追更", "涂佩"), 1):
        prefix = "" if index == 1 else f"route_{index}_"
        root = tmp_path / name
        root.mkdir()
        values.update({prefix + "route_name": name, prefix + "local_root": str(root),
                       prefix + "staging": f"/MP暂存/{name}", prefix + "inbox": f"/115/待归档/{name}"})
    return values


def test_three_distinct_routes_and_legacy_configuration(modules, config_values, tmp_path):
    values = route_values(config_values, tmp_path)
    routes = modules.domain.Config.routes(values)
    assert [r.name for r in routes] == ["入库", "追更", "涂佩"]
    assert len({r.inbox for r in routes}) == 3
    assert len({r.cd2_token for r in routes}) == 1
    assert modules.domain.Config.routes(config_values)[0] == modules.domain.Config.parse(config_values)


@pytest.mark.parametrize("problem", ["local_duplicate", "local_nested", "cross_staging", "cross_inbox", "count"])
def test_ambiguous_or_watched_staging_routes_rejected(modules, config_values, tmp_path, problem):
    values = route_values(config_values, tmp_path)
    if problem == "local_duplicate":
        values["route_2_local_root"] = values["local_root"]
    elif problem == "local_nested":
        values["route_2_local_root"] = str(Path(values["local_root"]) / "sub")
    elif problem == "cross_staging":
        values["route_2_staging"] = "/待归档/入库/暂存"
    elif problem == "cross_inbox":
        values["route_2_inbox"] = "/115/MP暂存/入库/待归档"
    else:
        values["route_count"] = 17
    with pytest.raises(ValueError):
        modules.domain.Config.routes(values)


def test_missing_local_mount_can_start_and_restore_later(modules, config_values, tmp_path):
    values = {**config_values, "local_root": str(tmp_path / "temporarily-offline")}
    config = modules.domain.Config.routes(values)[0]
    assert config.local_root == values["local_root"]


def test_pinned_route_uses_new_token_but_never_new_destination(modules, config_values):
    original = modules.domain.Config.parse(config_values)
    new = replace(original, cd2_token="rotated", inbox="/115/new-inbox", local_root="/another")
    pinned = new.pinned(original.routing())
    assert pinned.routing() == original.routing() and pinned.cd2_token == "rotated"
    with pytest.raises(modules.domain.BridgeError):
        replace(new, cd2_address="http://another:19798").pinned(original.routing())


def test_native_form_add_remove_and_hidden_fields_preserve_routes(modules):
    node = shutil.which("node")
    assert node, "Node is required to validate native FormRender expressions"
    form, defaults = modules.plugin.SymediaBatchBridge().get_form()
    # Execute the same expression/event wrappers used by MP's FormRender.vue.
    script = r'''
const fs = require('fs'); const {form, defaults} = JSON.parse(fs.readFileSync(0,'utf8'));
const nodes=[]; function visit(n){nodes.push(n);for(const c of n.content||[])visit(c)}; form.forEach(visit);
const model=structuredClone(defaults);
const invoke = code => new Function('model','event',`with(model){return (${code})(event)}`)(model);
const evaluate = code => new Function('model',`with(model){return ${code.slice(2,-2)}}`)(model);
const add=nodes.find(n=>n.text==='添加路线'); const removes=nodes.filter(n=>n.text==='移除路线');
const cards=nodes.filter(n=>n.component==='VCard');
const fields=nodes.filter(n=>n.props?.model).map(n=>n.props.model);
if(new Set(fields).size!==fields.length || fields.some(k=>!(k in model)))throw Error('Invalid flat models');
invoke(add.props.onClick);invoke(add.props.onClick);
if(model.route_count!==3 || cards.filter(n=>evaluate(n.props.show)).length!==3)throw Error('Add failed');
model.route_name='入库';model.local_root='/local/a';model.staging='/stage/a';model.inbox='/115/in/a';
model.route_2_route_name='追更';model.route_2_local_root='/local/b';model.route_2_staging='/stage/b';model.route_2_inbox='/115/in/b';
model.route_3_route_name='涂佩';model.route_3_local_root='/local/c';model.route_3_staging='/stage/c';model.route_3_inbox='/115/in/c';
invoke(removes[1].props.onClick);
if(model.route_count!==2 || model.route_name!=='入库' || model.route_2_route_name!=='涂佩' || model.route_2_inbox!=='/115/in/c')throw Error('Remove shifted route incorrectly');
invoke(add.props.onClick);
if(model.route_3_local_root!=='' || model.route_3_inbox!=='')throw Error('Removed stale paths returned');
invoke(removes[0].props.onClick);
if(model.route_name!=='涂佩' || model.local_root!=='/local/c' || model.staging!=='/stage/c')throw Error('First legacy slot failed');
for(let i=0;i<30;i++)invoke(add.props.onClick);
if(model.route_count!==16 || !evaluate(add.props.disabled))throw Error('Limit failed');
process.stdout.write('PASS');
'''
    result = subprocess.run([node, "-e", script], input=json.dumps({"form": form, "defaults": defaults}),
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert result.stdout == "PASS"


def test_three_routes_run_and_pending_job_keeps_old_destination_after_edit(modules, config_values, tmp_path, monkeypatch):
    values = route_values(config_values, tmp_path)
    routes = modules.domain.Config.routes(values)
    rows, torrent_files, downloads = [], {}, {}
    for i, route in enumerate(routes, 1):
        dest = Path(route.local_root) / "作品" / "video.mkv"
        dest.parent.mkdir()
        dest.write_bytes(f"video{i}".encode())
        source_root = tmp_path / f"downloads-{i}"
        source_root.mkdir()
        source = source_root / "video.mkv"
        source.write_bytes(dest.read_bytes())
        rows.append(NS(id=i, title=route.name, download_hash=f"hash{i}", downloader="qb", status=True,
                       src_storage="local", src=source.as_posix(), dest=str(dest), dest_storage="local"))
        torrent_files[f"hash{i}"] = [NS(name="video.mkv", size=6, priority=1, progress=1)]
        downloads[f"hash{i}"] = [NS(downloader="qb", savepath=source_root.as_posix())]
    actual_host = modules.host.MPHost
    chain = NS(torrent_files=lambda tid, downloader: torrent_files[tid],
               list_torrents=lambda **kw: [NS(progress=100)])
    transfers = NS(get=lambda key: next(row for row in rows if row.id == key),
                   list_by_hash=lambda key: [row for row in rows if row.download_hash == key],
                   list_by_date=lambda date: [])
    def host_factory(config):
        return actual_host(config, chain=chain, storage=NS(),
                           downloads=NS(get_files_by_hash=lambda key, state: downloads[key]),
                           transfers=transfers, extensions={".mkv", ".srt"})
    cloud = NS(files={}, moves=[], close=Mock())
    cloud.exists = lambda path: any(p.startswith(path + "/") for p in cloud.files)
    cloud.require_inbox = lambda path: None
    cloud.tree = lambda path: {p[len(path)+1:]: v for p,v in cloud.files.items() if p.startswith(path + "/")}
    def move(source, inbox):
        cloud.moves.append((source, inbox))
        destination = inbox + "/" + source.split("/")[-1]
        for path in list(cloud.files):
            if path.startswith(source + "/"):
                cloud.files[destination + path[len(source):]] = cloud.files.pop(path)
        return True
    cloud.move_directory = move
    def instant(self, entry, remote, stop):
        cloud.files[self.config.cd2_prefix + remote] = {"size": entry["size"], "sha1": entry["sha1"]}
        return {"size": entry["size"]}
    monkeypatch.setattr(modules.plugin, "MPHost", host_factory)
    monkeypatch.setattr(modules.plugin, "CD2", lambda config, stop: cloud)
    p = modules.plugin.SymediaBatchBridge()
    p.init_plugin(values)
    assert p.get_state()
    for row in rows:
        p.on_transfer(NS(event_data={"transfer_history_id": row.id}))
    assert len(p._store.jobs()) == 3
    # A config edit must not retarget the pending entry for 追更.
    p.init_plugin({**values, "route_2_inbox": "/115/new-追更"})
    monkeypatch.setattr(actual_host, "try_instant", instant)
    p.check_batches()
    jobs = p._store.jobs()
    assert all(job["state"] == "handed_off" for job in jobs), jobs
    assert {inbox for _,inbox in cloud.moves} == {r.inbox for r in routes}
    assert {j["route_name"] for j in jobs} == {r.name for r in routes}
    p.check_batches()
    assert len(cloud.moves) == 3
    page = json.dumps(p.get_page(), ensure_ascii=False)
    assert all(name in page for name in ("入库", "追更", "涂佩"))
