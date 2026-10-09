"""Single-worker upload readiness without weakening the whole-batch handoff."""

from hashlib import sha1
from pathlib import Path
from unittest.mock import Mock

from test_batches import batch


def add_files(batch, count):
    for index in range(count):
        path = Path(batch.paths[0]).with_name(f"S01E{index + 2:02}.mkv")
        path.write_bytes(f"video {index}".encode())
        batch.paths.append(str(path))


def test_newly_hashed_video_uploads_before_subtitle_hash(batch, monkeypatch):
    original_hash = batch.engine._hash_file
    observed = []

    def traced(job, path, relative, **kwargs):
        observed.append((path, len(batch.host.uploads)))
        return original_hash(job, path, relative, **kwargs)

    monkeypatch.setattr(batch.engine, "_hash_file", traced)
    batch.run()

    assert observed == [(batch.paths[0], 0), (batch.paths[1], 1)]
    assert batch.job()["state"] == "handed_off"
    assert len(batch.cloud.moves) == 1
    assert len(batch.cloud.tree(batch.job()["destination"])) == 2


def test_cached_hash_uploads_before_earlier_unhashed_candidate(batch, monkeypatch):
    job = batch.job()
    batch.engine._sync_candidates(job, [{"local": batch.paths[1]}])
    original_hash = batch.engine._hash_file

    def traced(job, path, relative, **kwargs):
        assert batch.host.uploads == [batch.config.staging + "/" + batch.id + "/" +
                                      batch.config.relative(batch.paths[1])]
        return original_hash(job, path, relative, **kwargs)

    monkeypatch.setattr(batch.engine, "_hash_file", traced)
    batch.run()

    assert batch.job()["state"] == "handed_off"
    assert len(batch.host.uploads) == 2


def test_partial_hash_manifest_can_upload_but_cannot_move(batch, modules, monkeypatch):
    add_files(batch, 9)
    hashed = Mock(wraps=modules.engine.freeze_file)
    monkeypatch.setattr(modules.engine, "freeze_file", hashed)

    for expected in (4, 8):
        batch.run()
        saved = batch.job()
        assert len(saved["files"]) == len(batch.host.uploads) == hashed.call_count == expected
        assert all(entry["uploaded"] for entry in saved["files"])
        assert not batch.cloud.moves and not saved.get("move_requested")

    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert hashed.call_count == len(batch.host.uploads) == 11
    assert len(batch.cloud.moves) == 1


def test_upload_quota_is_shared_by_cached_and_new_hash_files(batch):
    add_files(batch, 7)
    batch.engine._sync_candidates(batch.job(), batch.host.collect(batch.job())[:4])

    batch.run()

    assert len(batch.job()["files"]) == 8
    assert len(batch.host.uploads) == 4
    assert sum(entry["uploaded"] for entry in batch.job()["files"]) == 4
    assert not batch.cloud.moves
    batch.run()
    assert len(batch.host.uploads) == 8 and not batch.cloud.moves
    batch.run()
    assert len(batch.host.uploads) == 9 and len(batch.cloud.moves) == 1


def test_cloud_outage_does_not_block_bounded_local_hash_work(batch):
    add_files(batch, 4)
    batch.cloud.exists = Mock(side_effect=RuntimeError("temporary unavailable"))

    batch.run()

    assert len(batch.job()["files"]) == 4
    batch.cloud.exists.assert_called_once()
    assert not batch.host.uploads and not batch.cloud.moves


def test_account_cooldown_hashes_without_cloud_calls_or_attempts(batch, modules):
    error = modules.engine.CloudCooldown(modules.engine.time.time() + 3600)
    batch.host.gate_cloud = Mock(side_effect=error)
    batch.cloud.exists = Mock(side_effect=AssertionError("cooldown must prevent CD2 calls"))

    batch.run()

    saved = batch.job()
    assert len(saved["files"]) == 2
    assert saved["next_check"] == error.next_at
    assert saved["state"] == "waiting"
    assert all(not entry.get("instant_requests") and not entry.get("instant_misses") for entry in saved["files"])
    batch.cloud.exists.assert_not_called()
    assert not batch.host.uploads and not batch.cloud.moves


def test_limit_response_stops_cloud_work_and_keeps_hashing(batch, modules):
    error = modules.engine.CloudCooldown(modules.engine.time.time() + 7200)
    batch.host.try_instant = Mock(side_effect=error)
    batch.cloud.tree = Mock(wraps=batch.cloud.tree)

    batch.run()

    saved = batch.job()
    batch.host.try_instant.assert_called_once()
    assert len(saved["files"]) == 2
    assert saved["files"][0]["instant_requests"] == 1
    assert saved["files"][0]["instant_next_at"] == error.next_at
    assert all(not entry.get("instant_misses") for entry in saved["files"])
    assert not saved["files"][1].get("instant_requests")
    assert saved["next_check"] == error.next_at
    assert not batch.cloud.moves
    batch.cloud.tree.assert_not_called()


def test_cooldown_does_not_defer_remaining_local_hashes_for_an_hour(batch, modules):
    add_files(batch, 4)
    error = modules.engine.CloudCooldown(modules.engine.time.time() + 3600)
    batch.host.gate_cloud = Mock(side_effect=error)
    batch.cloud.exists = Mock(side_effect=AssertionError("must remain local"))

    batch.run()
    assert len(batch.job()["files"]) == 4
    assert batch.job()["next_check"] < error.next_at
    batch.run()
    assert len(batch.job()["files"]) == 6
    assert batch.job()["next_check"] == error.next_at
    batch.cloud.exists.assert_not_called()


def test_new_cooldown_blocks_handoff_without_losing_uploaded_receipts(batch, modules):
    error = modules.engine.CloudCooldown(modules.engine.time.time() + 3600)

    def gate():
        if len(batch.host.uploads) == 2:
            raise error

    batch.host.gate_cloud = gate
    batch.run()
    assert all(entry["uploaded"] for entry in batch.job()["files"])
    assert batch.job()["next_check"] == error.next_at
    assert not batch.cloud.moves

    batch.host.gate_cloud = lambda: None
    batch.run()
    assert batch.job()["state"] == "handed_off"
    assert len(batch.host.uploads) == 2
    assert len(batch.cloud.moves) == 1


def test_late_attachment_cannot_exceed_round_hash_budget(batch, modules, monkeypatch):
    add_files(batch, 2)
    original_hash = modules.engine.freeze_file
    hashed = Mock(wraps=original_hash)
    monkeypatch.setattr(modules.engine, "freeze_file", hashed)

    def append_attachment(local):
        if len(batch.host.uploads) == 4:
            path = Path(local).with_suffix(".zh.ass")
            path.write_bytes(b"late subtitle")
            batch.paths.append(str(path))

    batch.host.upload_hook = append_attachment
    batch.run()
    assert hashed.call_count == len(batch.job()["files"]) == 4
    assert not batch.cloud.moves
    batch.run()
    assert hashed.call_count == len(batch.host.uploads) == 5
    assert batch.job()["state"] == "handed_off"


def test_unhashed_candidate_already_in_cloud_recovers_without_duplicate(batch):
    # Simulate an upload receipt and cached HASH both missing locally. A complete
    # manifest permits the path, but its contents still need HASH verification.
    path = Path(batch.paths[1])
    relative = batch.config.relative(str(path))
    remote = batch.config.cd2_staging + "/" + batch.id + "/" + relative
    batch.cloud.files[remote] = {"size": path.stat().st_size, "sha1": sha1(path.read_bytes()).hexdigest()}

    batch.run()

    assert batch.job()["state"] == "handed_off"
    assert len(batch.host.uploads) == 1
    assert len(batch.cloud.moves) == 1


def test_removed_manifest_member_is_detected_before_cached_upload(batch):
    batch.engine._sync_candidates(batch.job(), batch.host.collect(batch.job()))
    batch.paths.pop()

    batch.run()

    assert batch.job()["state"] == "review"
    assert not batch.host.uploads and not batch.cloud.moves
