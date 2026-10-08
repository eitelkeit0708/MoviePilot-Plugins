from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import json

import pytest


@pytest.fixture
def batch(modules, config_values, tmp_path):
    config = modules.domain.Config.parse(config_values)
    paths = []
    for relative, data in [("电视剧/同名剧/Season 01/S01E01.mkv", b"video"),
                           ("电视剧/同名剧/Season 01/S01E01.zh.ass", b"subtitle")]:
        path = Path(config.local_root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(str(path))

    class Cloud:
        files = None

        def __init__(self):
            self.files, self.moves = {}, []
            self.move_hook = None

        def exists(self, path):
            return any(p == path or p.startswith(path + "/") for p in self.files)

        def require_inbox(self, path):
            assert path == config.inbox

        def file_at(self, path):
            return self.files.get(path)

        def tree(self, path):
            return {p[len(path) + 1:]: item for p, item in self.files.items() if p.startswith(path + "/")}

        def move_directory(self, source, inbox):
            self.moves.append(source)
            destination = inbox + "/" + source.split("/")[-1]
            for p in list(self.files):
                if p.startswith(source + "/"):
                    self.files[destination + p[len(source):]] = self.files.pop(p)
            if self.move_hook:
                return self.move_hook()
            return True

        def close(self):
            pass

    cloud = Cloud()

    class Host:
        def __init__(self):
            self.uploads, self.ready, self.upload_hook = [], True, None

        def collect(self, job):
            if not self.ready:
                raise modules.domain.Awaiting("等待字幕整理完成")
            return [{"local": p} for p in paths]

        def upload(self, local, remote):
            self.uploads.append(remote)
            data = local.read_bytes()
            receipt = {"size": len(data), "sha1": sha1(data).hexdigest()}
            cloud.files[config.cd2_prefix + remote] = receipt
            if self.upload_hook:
                self.upload_hook(local)
            return receipt

    host, stop = Host(), Event()
    store = modules.store.Store(tmp_path / "ledger")
    observe = dict(instance="SymediaBatchBridge", download_hash="torrent-one", downloader="qb",
                   title="同名剧", history_id=1, routing=config.routing())
    store.observe(**observe)
    job_id = store.jobs()[0]["id"]
    engine = modules.engine.Engine(store, config, host, cloud, stop)
    return SimpleNamespace(config=config, cloud=cloud, host=host, stop=stop, store=store,
                           id=job_id, run=lambda: engine.process(store.get(job_id)),
                           job=lambda: store.get(job_id), engine=engine, observe=observe, paths=paths)


def test_whole_directory_preserves_video_and_subtitles(batch):
    batch.run()
    job = batch.job()
    assert job["state"] == "handed_off", job["message"]
    assert batch.cloud.moves == ["/115/MP暂存/" + job["id"]]
    assert set(batch.cloud.tree(job["destination"])) == {"电视剧/同名剧/Season 01/S01E01.mkv", "电视剧/同名剧/Season 01/S01E01.zh.ass"}
    assert all(Path(p).exists() for p in batch.paths)


def test_subtitle_not_ready_cannot_upload_or_move(batch):
    batch.host.ready = False
    for _ in range(12):
        batch.run()
    assert batch.job()["state"] == "waiting"
    assert batch.job()["attempts"] == 0
    assert batch.host.uploads == batch.cloud.moves == []
    batch.host.ready = True
    batch.run()
    assert batch.job()["state"] == "handed_off"


def test_same_title_different_tasks_and_replayed_event(batch):
    batch.store.observe(**batch.observe)
    batch.store.observe(**{**batch.observe, "download_hash": "torrent-two"})
    jobs = batch.store.jobs()
    assert len(jobs) == 2 and len({j["id"] for j in jobs}) == 2
    for job in jobs:
        batch.engine.process(job)
    assert len(batch.cloud.moves) == 2
    assert len(batch.cloud.files) == 4


def test_config_change_does_not_duplicate_a_task(batch):
    batch.store.observe(**{**batch.observe, "routing": {**batch.config.routing(), "inbox": "/115/other"}})
    assert len(batch.store.jobs()) == 1
    batch.engine.config = replace(batch.config, inbox="/115/other")
    batch.run()
    assert batch.job()["state"] == "review"
    assert batch.cloud.moves == []


def test_crash_after_upload_recovers_without_duplicate(batch):
    def lost_response(local):
        raise RuntimeError("SDK URL including SECRET")
    batch.host.upload_hook = lost_response
    batch.run()
    assert len(batch.host.uploads) == 1
    assert "SECRET" not in json.dumps(batch.job())
    batch.host.upload_hook = None
    batch.run()
    assert len(batch.host.uploads) == 2  # first file recovered via fresh SHA1
    assert batch.job()["state"] == "handed_off"


def test_upload_collision_without_hash_is_held(batch):
    first = "/115/MP暂存/" + batch.id + "/电视剧/同名剧/Season 01/S01E01.mkv"
    batch.cloud.files[first] = {"size": 5}
    batch.run()
    assert batch.job()["state"] == "review"
    assert not batch.host.uploads and not batch.cloud.moves


@pytest.mark.parametrize("symedia_consumes", [False, True])
def test_move_timeout_never_replays_or_reuploads(batch, modules, symedia_consumes):
    def timeout():
        if symedia_consumes:
            batch.cloud.files.clear()
        raise modules.domain.BridgeError("响应丢失")
    batch.cloud.move_hook = timeout
    batch.run()
    assert batch.job()["move_requested"]
    # Use a new engine/store, like an MP restart after an uncertain network result.
    new_store = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(new_store, batch.config, batch.host, batch.cloud, Event())
    engine.process(new_store.get(batch.id))
    assert len(batch.cloud.moves) == 1 and len(batch.host.uploads) == 2
    assert batch.job()["state"] == ("review" if symedia_consumes else "handed_off")


def test_stop_during_upload_cannot_publish(batch):
    batch.host.upload_hook = lambda local: batch.stop.set()
    batch.run()
    assert batch.cloud.moves == []
    assert batch.job()["files"][0]["uploaded"] is False


def test_file_mutated_while_uploading_is_held(batch):
    batch.host.upload_hook = lambda local: local.write_bytes(b"replacement")
    batch.run()
    assert batch.job()["state"] == "review"
    assert not batch.cloud.moves


def test_new_attachment_after_seal_is_held(batch):
    def added(local):
        if len(batch.paths) == 2:
            new = Path(batch.paths[0]).with_suffix(".en.srt")
            new.write_text("new")
            batch.paths.append(str(new))
    batch.host.upload_hook = added
    batch.run()
    assert batch.job()["state"] == "review"
    assert not batch.cloud.moves


def test_os_lock_blocks_reload_and_releases(batch, modules):
    other_store = modules.store.Store(batch.store.path.parent)
    with batch.store.worker_lock() as first:
        with other_store.worker_lock() as second:
            assert first and not second
    with other_store.worker_lock() as third:
        assert third


def test_destination_collision_does_not_merge(batch):
    batch.cloud.files[batch.config.inbox + "/" + batch.id + "/unrelated.mkv"] = {"size": 5}
    batch.run()
    assert batch.job()["state"] == "review"
    assert not batch.host.uploads and not batch.cloud.moves


def test_late_history_warns_without_republishing_and_old_events_are_ignored(batch):
    batch.run()
    batch.store.observe(**batch.observe)
    assert not batch.job().get("late_history_ids")
    batch.store.observe(**{**batch.observe, "history_id": 100})
    batch.run()
    assert batch.job()["late_history_ids"] == [100]
    assert batch.job()["state"] == "handed_off"
    assert len(batch.cloud.moves) == 1


def test_completed_batch_is_not_republished_after_symedia_consumes_directory(batch):
    batch.run()
    batch.cloud.files.clear()
    batch.run()
    assert len(batch.cloud.moves) == 1 and len(batch.host.uploads) == 2


def test_foreign_remote_file_holds_entire_batch(batch):
    def add_foreign(local):
        batch.cloud.files["/115/MP暂存/" + batch.id + "/foreign.srt"] = {"size": 1}
    batch.host.upload_hook = add_foreign
    batch.run()
    assert batch.job()["state"] == "review"
    assert not batch.cloud.moves


@pytest.mark.parametrize("values", [
    {"inbox": "/115/MP暂存/Symedia"}, {"inbox": "/different-account/Symedia"},
    {"staging": "/"}, {"staging": "/foo/../bar"}, {"cd2_prefix": "/"},
    {"cd2_address": "http://user:password@cd2:19798"}, {"interval": 0},
])
def test_invalid_routing_rejected(modules, config_values, values):
    with pytest.raises(ValueError):
        modules.domain.Config.parse({**config_values, **values})


@pytest.mark.parametrize("relative", ["../x", "/x", "a/../x", "a\\x", "a//x"])
def test_untrusted_remote_paths_cannot_escape_batch(modules, relative):
    with pytest.raises(modules.domain.BridgeError):
        modules.domain.child_path("/115/staging", relative)
