from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
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

        def directory_id(self, path):
            return ""  # Older CD2 responses may omit usable cloud identity.

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

        def try_instant(self, entry, remote, stop):
            return self.upload(Path(entry["local"]), remote)

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


@pytest.fixture
def inventory_batch(batch, modules):
    job = batch.job()
    rows = [SimpleNamespace(id=i + 1, dest=p, dest_storage="local", status=True,
                            downloader=job["downloader"], download_hash=job["download_hash"])
            for i, p in enumerate(batch.paths)]
    job.update(origin="inventory", inventory_files=[
        {"local": r.dest, "history_id": r.id, "signature": modules.domain.file_signature(Path(r.dest))}
        for r in rows])
    batch.store.save(job)
    forbidden = Mock(side_effect=AssertionError("Inventory must not query expired download tasks"))
    host = modules.host.MPHost(batch.config,
        chain=SimpleNamespace(torrent_files=forbidden, list_torrents=forbidden),
        storage=SimpleNamespace(), downloads=SimpleNamespace(get_files_by_hash=forbidden),
        transfers=SimpleNamespace(list_by_date=Mock(return_value=rows)), extensions={".mkv", ".ass"})
    host.try_instant = batch.host.try_instant
    host.upload = batch.host.upload
    batch.engine.host = host
    batch.inventory_host = host
    batch.inventory_rows = rows
    return batch


def test_inventory_snapshot_resumes_hashes_and_handoffs_video_with_subtitle(inventory_batch, modules, monkeypatch):
    batch = inventory_batch
    hashed = Mock(wraps=modules.engine.freeze_file)
    monkeypatch.setattr(modules.engine, "freeze_file", hashed)
    # Terminate the first worker after hashing, before any cloud side effect.
    batch.cloud.exists = Mock(side_effect=RuntimeError("temporary service outage"))
    batch.run()
    assert hashed.call_count == 2 and not batch.cloud.moves
    assert len(batch.job()["files"]) == 2
    assert not any(e.get("download_source") for e in batch.job()["files"])
    del batch.cloud.exists
    store = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(store, batch.config, batch.inventory_host, batch.cloud, Event())
    engine.process(store.get(batch.id))
    saved = store.get(batch.id)
    assert saved["state"] == "handed_off", saved["message"]
    assert hashed.call_count == 2
    assert len(batch.cloud.moves) == 1
    assert set(batch.cloud.tree(saved["destination"])) == {e["relative"] for e in saved["files"]}
    assert len(saved["files"]) == 2 and all(Path(p).is_file() for p in batch.paths)


def test_inventory_replacement_between_collect_and_hash_cannot_upload(inventory_batch, modules, monkeypatch):
    batch = inventory_batch
    original = modules.engine.freeze_file
    def replace_then_hash(path, relative, stop):
        Path(path).write_bytes(b"replaced after snapshot validation")
        return original(path, relative, stop)
    monkeypatch.setattr(modules.engine, "freeze_file", replace_then_hash)
    batch.run()
    assert batch.job()["state"] == "review"
    assert "存量文件" in batch.job()["message"]
    assert not batch.host.uploads and not batch.cloud.moves


def test_inventory_retains_optional_hardlink_proof_only_when_original_exists(inventory_batch):
    batch = inventory_batch
    source = Path(batch.paths[0]).with_suffix(".download-source")
    source.hardlink_to(batch.paths[0])
    batch.inventory_rows[0].src = str(source)
    batch.inventory_rows[1].src = str(source.with_suffix(".expired"))
    batch.run()
    saved = batch.job()
    assert saved["state"] == "handed_off", saved["message"]
    assert saved["files"][0]["download_source"] == str(source).replace("\\", "/")
    assert "download_source" not in saved["files"][1]


def missing_staged_file(batch, now):
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    entry = job["files"][0]
    remote = batch.config.staging + "/" + job["id"] + "/" + entry["relative"]
    entry.update(instant_started_at=now - 100, instant_misses=7)
    batch.engine._upload_entry(job, entry, remote)
    del batch.cloud.files[batch.config.cd2_prefix + remote]
    return remote


def test_missing_staged_file_repairs_after_confirmation_without_resetting_deadline(batch, modules, monkeypatch):
    now = [1_800_000_000]
    monkeypatch.setattr(modules.engine.time, "time", lambda: now[0])
    remote = missing_staged_file(batch, now[0])
    batch.run()
    assert batch.job()["state"] == "waiting"
    assert len(batch.host.uploads) == 2  # Only the untouched subtitle was uploaded.
    now[0] += 61
    batch.run()
    entry = batch.job()["files"][0]
    assert not entry["uploaded"] and entry["repair_count"] == 1
    assert entry["instant_misses"] == 7 and entry["instant_next_at"] == 1_800_003_600
    assert batch.cloud.moves == []
    # SQLite reload and a new Engine do not reset the hourly schedule.
    store = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(store, batch.config, batch.host, batch.cloud, batch.stop)
    now[0] = 1_800_003_600
    engine.process(store.get(batch.id))
    assert store.get(batch.id)["state"] == "handed_off"
    assert batch.host.uploads.count(remote) == 2 and len(batch.cloud.moves) == 1


def test_missing_staged_file_that_reappears_is_not_uploaded_again(batch, modules, monkeypatch):
    now = [1_800_000_000]
    monkeypatch.setattr(modules.engine.time, "time", lambda: now[0])
    remote = missing_staged_file(batch, now[0])
    batch.run()
    entry = batch.job()["files"][0]
    batch.cloud.files[batch.config.cd2_prefix + remote] = {"size": entry["size"], "sha1": entry["sha1"]}
    now[0] += 61
    batch.run()
    assert batch.job()["state"] == "handed_off" and batch.host.uploads.count(remote) == 1


def test_listing_error_never_invalidates_upload_receipt(batch, modules, monkeypatch):
    missing_staged_file(batch, 1_800_000_000)
    monkeypatch.setattr(batch.cloud, "exists", Mock(side_effect=modules.domain.BridgeError("CD2 读取失败")))
    batch.run()
    entry = batch.job()["files"][0]
    assert entry["uploaded"] and "remote_missing_at" not in entry
    assert len(batch.host.uploads) == 1 and batch.cloud.moves == []


def test_changed_uploaded_cloud_file_is_held_and_never_overwritten(batch, modules):
    remote = missing_staged_file(batch, 1_800_000_000)
    entry = batch.job()["files"][0]
    batch.cloud.files[batch.config.cd2_prefix + remote] = {"size": entry["size"], "sha1": "0" * 40}
    batch.run()
    assert batch.job()["state"] == "review"
    assert len(batch.host.uploads) == 1 and batch.cloud.moves == []


def consumed_without_receipt(batch):
    def consume():
        moved = list(batch.cloud.files.items())
        batch.cloud.files.clear()
        for index, (_, item) in enumerate(moved):
            batch.cloud.files[f"/115/归档/作品/renamed-{index}"] = item
        raise RuntimeError("response lost after Symedia consumed the batch")
    batch.cloud.move_hook = consume
    batch.run()
    batch.run()
    assert batch.job()["state"] == "review"


def test_consumed_directory_requires_complete_content_proof_and_survives_restart(batch, modules):
    consumed_without_receipt(batch)
    job = batch.job()
    job["recovery_directory"] = "/115/归档/作品"
    batch.store.save(job)
    store = modules.store.Store(batch.store.path.parent)
    modules.engine.Engine(store, batch.config, batch.host, batch.cloud, batch.stop).process(store.get(batch.id))
    job = store.get(batch.id)
    assert job["state"] == "handed_off" and job["completion_basis"] == "archive_verified"
    assert len(job["archive_matches"]) == 2
    assert len(batch.host.uploads) == 2 and len(batch.cloud.moves) == 1
    assert any(e["kind"] == "archive_verified" for e in store.events(batch.id))


@pytest.mark.parametrize("damage", ["missing_subtitle", "wrong_sha", "missing_sha", "wrong_size"])
def test_archive_recovery_never_accepts_incomplete_proof(batch, damage):
    consumed_without_receipt(batch)
    subtitle = next(p for p, item in batch.cloud.files.items() if item["size"] == len(b"subtitle"))
    if damage == "missing_subtitle":
        del batch.cloud.files[subtitle]
    elif damage == "wrong_sha":
        batch.cloud.files[subtitle]["sha1"] = "0" * 40
    elif damage == "missing_sha":
        batch.cloud.files[subtitle].pop("sha1")
    else:
        batch.cloud.files[subtitle]["size"] += 1
    job = batch.job()
    job["recovery_directory"] = "/115/归档/作品"
    batch.store.save(job)
    batch.run()
    assert batch.job()["state"] == "review" and not batch.job().get("cleanup_done")
    assert len(batch.host.uploads) == 2 and len(batch.cloud.moves) == 1


def test_archive_recovery_counts_identical_attachments_separately(modules):
    entries = [{"relative": p, "size": 5, "sha1": "a" * 40} for p in ("a.srt", "b.srt")]
    with pytest.raises(modules.domain.BridgeError):
        modules.recovery.verify_archive(entries, {"one.srt": {"size": 5, "sha1": "a" * 40}})


def test_archive_recovery_revalidates_new_active_staging_configuration(batch):
    consumed_without_receipt(batch)
    job = batch.job()
    job["recovery_directory"] = "/115/归档/作品"
    batch.store.save(job)
    batch.engine.protected_routings = [{**batch.config.routing(), "staging": "/归档"}]
    batch.run()
    assert batch.job()["state"] == "review"
    assert "不能使用暂存" in batch.job()["message"]
    assert len(batch.host.uploads) == 2 and len(batch.cloud.moves) == 1


@pytest.fixture
def identified_batch(batch, monkeypatch):
    source = batch.config.cd2_staging + "/" + batch.id
    target = batch.config.inbox + "/" + batch.id
    ids = {source: "3535498838548678168"}
    original_exists = batch.cloud.exists
    monkeypatch.setattr(batch.cloud, "exists", lambda path: path in ids or original_exists(path))
    monkeypatch.setattr(batch.cloud, "directory_id", lambda path: ids.get(path, ""))
    batch.directory_ids, batch.source, batch.target = ids, source, target
    return batch


@pytest.mark.parametrize("remaining", [0, 1])
def test_retained_directory_identity_recovers_consumption_after_restart(identified_batch, modules, remaining):
    batch = identified_batch
    def consume_and_lose_reply():
        saved = batch.store.get(batch.id)
        assert saved["move_requested"] and saved["source_directory_id"] == batch.directory_ids[batch.source]
        batch.directory_ids[batch.target] = batch.directory_ids.pop(batch.source)
        for path in list(batch.cloud.files)[remaining:]:
            del batch.cloud.files[path]
        raise modules.domain.BridgeError("response lost")
    batch.cloud.move_hook = consume_and_lose_reply
    batch.run()
    assert batch.job()["state"] == "moving"
    restored = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(restored, batch.config, batch.host, batch.cloud, Event())
    engine.process(restored.get(batch.id))
    saved = restored.get(batch.id)
    assert saved["state"] == "handed_off" and saved["completion_basis"] == "directory_identity"
    assert len(batch.cloud.moves) == 1 and len(batch.host.uploads) == 2
    assert len(batch.cloud.tree(batch.target)) == remaining
    assert "目录 ID" in restored.events(batch.id)[0]["message"]


@pytest.mark.parametrize("replacement", ["999999", ""])
def test_different_or_unavailable_destination_identity_never_confirms_handoff(identified_batch, modules, replacement):
    batch = identified_batch
    def wrong_directory():
        batch.directory_ids.pop(batch.source)
        batch.directory_ids[batch.target] = replacement
        # Even a complete copy of the files cannot override a changed directory ID.
        raise modules.domain.BridgeError("response lost")
    batch.cloud.move_hook = wrong_directory
    batch.run()
    batch.run()
    assert batch.job()["state"] == "review" and "目录身份" in batch.job()["message"]
    assert len(batch.cloud.moves) == 1


def test_old_job_cannot_infer_identity_from_empty_destination(identified_batch, modules):
    batch = identified_batch
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    job.update(state="moving", move_requested=True, source=batch.source, destination=batch.target)
    batch.store.save(job)  # An old record has no identity captured before moving.
    batch.directory_ids[batch.target] = batch.directory_ids.pop(batch.source)
    batch.run()
    assert batch.job()["state"] == "moving"
    assert not batch.job().get("source_directory_id")
    assert batch.cloud.moves == batch.host.uploads == []


def test_changed_source_identity_blocks_move_retry(identified_batch, modules):
    batch = identified_batch
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    job.update(state="moving", move_requested=True, source=batch.source, destination=batch.target,
               source_directory_id="123456")
    batch.store.save(job)
    batch.run()
    assert batch.job()["state"] == "review" and "源目录身份" in batch.job()["message"]
    assert batch.cloud.moves == batch.host.uploads == []


def test_source_replaced_during_verification_cannot_record_move_intent(identified_batch, monkeypatch):
    batch = identified_batch
    original = batch.cloud.tree
    calls = []
    def replace(path):
        calls.append(path)
        result = original(path)
        if len(calls) == 2:  # Final manifest verification, after capture of the ID.
            batch.directory_ids[batch.source] = "999999"
        return result
    monkeypatch.setattr(batch.cloud, "tree", replace)
    batch.run()
    assert batch.job()["state"] == "review" and not batch.job().get("move_requested")
    assert batch.cloud.moves == []


def test_old_intent_can_capture_identity_only_from_complete_source(identified_batch, modules):
    batch = identified_batch
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    for entry in job["files"]:
        batch.cloud.files[batch.source + "/" + entry["relative"]] = {"size": entry["size"], "sha1": entry["sha1"]}
    job.update(state="moving", move_requested=True, source=batch.source, destination=batch.target)
    batch.store.save(job)
    def lost():
        saved = batch.job()
        assert saved["source_directory_id"] == batch.directory_ids[batch.source]
        batch.directory_ids[batch.target] = batch.directory_ids.pop(batch.source)
        batch.cloud.files.clear()
        raise modules.domain.BridgeError("lost")
    batch.cloud.move_hook = lost
    batch.run()
    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert len(batch.cloud.moves) == 1 and batch.host.uploads == []


def test_both_directories_present_remains_a_conflict_even_with_matching_id(identified_batch):
    batch = identified_batch
    job = batch.job()
    job.update(state="moving", move_requested=True, source=batch.source, destination=batch.target,
               source_directory_id=batch.directory_ids[batch.source])
    batch.directory_ids[batch.target] = batch.directory_ids[batch.source]
    batch.store.save(job)
    batch.run()
    assert batch.job()["state"] == "review" and "同时存在" in batch.job()["message"]
    assert batch.cloud.moves == batch.host.uploads == []


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
    assert len(batch.host.uploads) == 2  # another file still gets its own attempt
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


def test_new_attachment_after_seal_is_added_before_handoff(batch):
    def added(local):
        if len(batch.paths) == 2:
            new = Path(batch.paths[0]).with_suffix(".en.srt")
            new.write_text("new")
            batch.paths.append(str(new))
    batch.host.upload_hook = added
    batch.run()
    assert batch.job()["state"] == "waiting"
    assert not batch.cloud.moves
    assert len(batch.job()["files"]) == 3
    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert len(batch.host.uploads) == 3


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
    {"staging": "/"}, {"staging": "/foo/../bar"}, {"cd2_prefix": "/", "inbox": "/"},
    {"cd2_address": "http://user:password@cd2:19798"}, {"interval": 0},
])
def test_invalid_routing_rejected(modules, config_values, values):
    with pytest.raises(ValueError):
        modules.domain.Config.parse({**config_values, **values})


@pytest.mark.parametrize("relative", ["../x", "/x", "a/../x", "a\\x", "a//x"])
def test_untrusted_remote_paths_cannot_escape_batch(modules, relative):
    with pytest.raises(modules.domain.BridgeError):
        modules.domain.child_path("/115/staging", relative)


@pytest.fixture
def hourly(batch, monkeypatch):
    clock = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr("time.time", lambda: clock.now)
    batch.host.try_instant = Mock(return_value=None)
    batch.host.upload = Mock(wraps=batch.host.upload)
    return clock


def test_24_non_hits_wait_a_full_day_before_normal_upload(batch, hourly):
    for hour in range(24):
        hourly.now = 1_800_000_000 + hour * 3600
        batch.run()
        job = batch.job()
        assert job["state"] == "waiting_instant"
        assert all(e["instant_misses"] == hour + 1 for e in job["files"])
        assert job["next_check"] == hourly.now + 3600
        assert job["attempts"] == 0
        batch.host.upload.assert_not_called()
        assert not batch.cloud.moves
    assert batch.host.try_instant.call_count == 48  # 24 per file, video AND subtitle
    hourly.now += 3599
    batch.run()
    batch.host.upload.assert_not_called()
    hourly.now += 1
    batch.run()
    assert batch.host.upload.call_count == 2
    assert batch.host.try_instant.call_count == 48
    assert batch.job()["state"] == "handed_off" and len(batch.cloud.moves) == 1


def test_restart_and_manual_recheck_do_not_reset_or_skip_deadline(batch, hourly, modules):
    batch.run()
    old = batch.job()
    hourly.now += 100
    fresh_store = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(fresh_store, batch.config, batch.host, batch.cloud, Event())
    for _ in range(5):
        job = fresh_store.get(batch.id)
        job.update(state="waiting", attempts=0, next_check=0)  # same API action
        engine.process(job)
    assert batch.host.try_instant.call_count == 2
    assert batch.job()["files"] == old["files"]
    batch.host.upload.assert_not_called()


def test_long_downtime_does_not_catch_up_or_allow_early_fallback(batch, hourly):
    batch.run()
    hourly.now += 7 * 86400
    batch.run()
    assert batch.host.try_instant.call_count == 4
    assert all(e["instant_misses"] == 2 for e in batch.job()["files"])
    batch.host.upload.assert_not_called()


def test_one_non_hit_does_not_block_other_file_and_cannot_handoff(batch, hourly):
    def mixed(entry, remote, stop):
        return batch.host.upload(Path(entry["local"]), remote) if entry["local"].endswith(".ass") else None
    batch.host.try_instant.side_effect = mixed
    batch.run()
    assert sum(e["uploaded"] for e in batch.job()["files"]) == 1
    assert not batch.cloud.moves
    hourly.now += 3600
    batch.host.try_instant.side_effect = lambda entry, remote, stop: batch.host.upload(Path(entry["local"]), remote)
    batch.run()
    assert batch.host.try_instant.call_count == 3  # subtitle not sent a second time
    assert batch.job()["state"] == "handed_off"


def test_network_errors_never_exhaust_instant_budget_or_leak(batch, hourly):
    batch.host.try_instant.side_effect = RuntimeError("https://secret-cookie")
    for _ in range(30):
        batch.run()
        hourly.now += 3600
    job = batch.job()
    assert job["state"] == "waiting_instant" and job["attempts"] == 0
    assert all(e.get("instant_misses", 0) == 0 and e.get("instant_error") for e in job["files"])
    assert "secret-cookie" not in json.dumps(job)
    batch.host.upload.assert_not_called()


def test_crash_reservation_is_durable_before_probe(batch, hourly, modules):
    def interrupted(entry, remote, stop):
        saved = batch.job()["files"][0]
        assert saved["instant_next_at"] == hourly.now + 3600
        assert saved["instant_started_at"] == hourly.now
        raise modules.domain.Stopped()
    batch.host.try_instant.side_effect = interrupted
    batch.run()
    batch.host.try_instant.side_effect = None
    hourly.now += 1
    batch.run()
    assert batch.host.try_instant.call_count == 2  # only the untouched second file
    assert batch.job()["files"][0].get("instant_misses", 0) == 0
    batch.host.upload.assert_not_called()


def test_miss_interval_is_measured_after_slow_request(batch, hourly):
    def slow(entry, remote, stop):
        hourly.now += 180
        return None
    batch.host.try_instant.side_effect = slow
    batch.run()
    assert batch.job()["files"][0]["instant_next_at"] == 1_800_000_000 + 180 + 3600


def test_legacy_pending_file_gets_new_policy_without_losing_receipts(batch, hourly):
    batch.run()
    job = batch.job()
    for entry in job["files"]:
        for key in ("preid", "instant_started_at", "instant_next_at", "instant_misses"):
            entry.pop(key, None)
    first = job["files"][0]
    first["uploaded"] = True
    batch.cloud.files[batch.config.cd2_prefix + batch.config.staging + "/" + batch.id + "/" + first["relative"]] = {
        "size": first["size"], "sha1": first["sha1"]}
    batch.store.save(job)
    batch.host.try_instant.reset_mock()
    batch.run()
    assert batch.host.try_instant.call_count == 1
    pending = batch.job()["files"][1]
    assert pending["instant_misses"] == 1 and pending["preid"] == pending["sha1"]
    assert batch.job()["files"][0]["uploaded"]
    batch.host.upload.assert_not_called()


def test_last_probe_error_cannot_unlock_normal_upload(batch, hourly):
    for _ in range(23):
        batch.run()
        hourly.now += 3600
    batch.host.try_instant.side_effect = RuntimeError("connection lost")
    batch.run()
    hourly.now += 3600
    batch.run()  # 24 hours elapsed, but only 23 confirmed non-hits
    batch.host.upload.assert_not_called()
    assert all(e["instant_misses"] == 23 for e in batch.job()["files"])
    batch.host.try_instant.side_effect = None
    hourly.now += 3600
    batch.run()
    batch.host.upload.assert_not_called()
    hourly.now += 3600
    batch.run()
    assert batch.job()["state"] == "handed_off"


def test_hashes_are_reused_for_hourly_probes_and_restart(batch, hourly, modules, monkeypatch):
    freeze = Mock(wraps=modules.engine.freeze_file)
    monkeypatch.setattr(modules.engine, "freeze_file", freeze)
    batch.run()
    before = [{k: e[k] for k in ("sha1", "preid", "signature")} for e in batch.job()["files"]]
    assert freeze.call_count == 2
    for _ in range(3):
        hourly.now += 3600
        # Recreate the engine and ledger each time, as when MP restarts.
        store = modules.store.Store(batch.store.path.parent)
        modules.engine.Engine(store, batch.config, batch.host, batch.cloud, Event()).process(store.get(batch.id))
    assert freeze.call_count == 2
    for args in batch.host.try_instant.call_args_list:
        e = args.args[0]
        expected = before[0] if e["local"].endswith(".mkv") else before[1]
        assert {k: e[k] for k in expected} == expected


def test_legacy_provider_needs_confirmation_without_sending_files(batch, hourly):
    job = batch.job()
    job["routing"]["storage"] = "115网盘Plus"
    batch.store.save(job)
    batch.run()
    assert batch.job()["state"] == "review"
    batch.host.try_instant.assert_not_called()
    batch.host.upload.assert_not_called()


def test_temporary_outage_recovers_after_more_than_ten_failures(batch, modules):
    collect = batch.host.collect
    batch.host.collect = Mock(side_effect=RuntimeError("secret"))
    for _ in range(15):
        batch.run()
    job = batch.job()
    assert job["state"] == "retrying" and job["attempts"] == 15
    assert job["next_check"] > job["updated"]
    batch.host.collect = collect
    batch.run()
    assert batch.job()["state"] == "handed_off"


def test_crash_before_move_rpc_resumes_original_directory_only(batch, modules):
    def not_sent(source, inbox):
        raise modules.domain.BridgeError("connection lost before send")
    move = batch.cloud.move_directory
    batch.cloud.move_directory = not_sent
    batch.run()
    assert batch.job()["move_requested"] and len(batch.host.uploads) == 2
    batch.cloud.move_directory = move
    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert len(batch.cloud.moves) == 1 and len(batch.host.uploads) == 2


def test_recovery_cannot_move_tampered_source(batch, modules):
    batch.cloud.move_directory = Mock(side_effect=modules.domain.BridgeError("not sent"))
    batch.run()
    batch.cloud.files[next(iter(batch.cloud.files))]["sha1"] = "0" * 40
    batch.run()
    assert batch.job()["state"] == "review"
    assert batch.cloud.move_directory.call_count == 1


def test_temporarily_missing_move_paths_recover_without_reupload(batch, modules):
    batch.cloud.move_hook = lambda: (_ for _ in ()).throw(modules.domain.BridgeError("lost"))
    batch.run()
    files = batch.cloud.files
    batch.cloud.files = {}
    batch.run()
    assert batch.job()["state"] == "review" and batch.job()["next_check"] > 0
    batch.cloud.files = files
    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert len(batch.cloud.moves) == 1 and len(batch.host.uploads) == 2


def test_restored_identical_file_recovers_without_resetting_retries(batch, hourly):
    batch.run()
    entry = batch.job()["files"][0]
    path = Path(entry["local"])
    original = path.read_bytes()
    path.write_bytes(b"xxxxx")
    batch.run()
    assert batch.job()["state"] == "review"
    assert batch.job()["files"][0]["instant_misses"] == 1
    path.write_bytes(original)
    hourly.now += 3600
    batch.host.try_instant.side_effect = lambda e, remote, stop: batch.host.upload(Path(e["local"]), remote)
    batch.run()
    assert batch.job()["state"] == "handed_off"


def test_long_hash_manifest_is_checkpointed_and_work_is_bounded(batch, modules, monkeypatch):
    for i in range(9):
        path = Path(batch.paths[0]).with_name(f"S01E{i + 2:02}.mkv")
        path.write_bytes(b"video")
        batch.paths.append(str(path))
    freeze = Mock(wraps=modules.engine.freeze_file)
    monkeypatch.setattr(modules.engine, "freeze_file", freeze)
    batch.run()
    assert len(batch.job()["files"]) == 4 and not batch.host.uploads
    batch.run()
    assert len(batch.job()["files"]) == 8 and freeze.call_count == 8
    batch.run()
    assert freeze.call_count == 11 and len(batch.host.uploads) == 4
    batch.run()
    assert freeze.call_count == 11 and len(batch.host.uploads) == 8
    batch.run()
    assert batch.job()["state"] == "handed_off" and len(batch.host.uploads) == 11


def test_same_size_overwrite_before_hash_is_detected_against_download(batch, modules, tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"other")  # same size as video, different content
    original_collect = batch.host.collect
    batch.host.collect = lambda job: [{**c, "source": str(source)} if c["local"].endswith(".mkv") else c
                                      for c in original_collect(job)]
    batch.run()
    assert batch.job()["state"] == "review" and "同名覆盖" in batch.job()["message"]
    assert not batch.cloud.moves and not batch.host.uploads


@pytest.mark.parametrize("partial_column", [None, "state", "next_check"])
def test_old_sqlite_ledger_migrates_without_losing_due_jobs(batch, modules, tmp_path, partial_column):
    import sqlite3
    root = tmp_path / "old-ledger"
    root.mkdir()
    original = batch.job()
    with sqlite3.connect(root / "batches.sqlite3") as db:
        db.execute("CREATE TABLE batches(id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL)")
        db.execute("INSERT INTO batches VALUES(?, ?, ?, ?)", (original["id"], "old", json.dumps(original), original["updated"]))
        if partial_column == "state":
            db.execute("ALTER TABLE batches ADD COLUMN state TEXT NOT NULL DEFAULT 'waiting'")
        if partial_column == "next_check":
            db.execute("ALTER TABLE batches ADD COLUMN next_check REAL NOT NULL DEFAULT 0")
    restored = modules.store.Store(root)
    assert restored.get(original["id"]) == original
    assert restored.due()[0]["id"] == original["id"]
    original.update(state="review", next_check=0)
    restored.save(original)
    assert len(restored.due()) == 1
    original.update(state="handed_off")
    restored.save(original)
    assert restored.due() == [] and restored.pending_routings() == []
