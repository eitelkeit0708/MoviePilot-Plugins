"""Upgrade persisted pre-ownership ledgers without resetting or replaying work."""
from copy import deepcopy
from dataclasses import replace
import importlib
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_batches import batch
from test_activity import cleanup_fixture
from test_plugin_contract import plugin


def ownership():
    return importlib.import_module('app.plugins.symediabatchbridge.ownership')


def old_waiting_batch(batch):
    # The pre-1.6 format has a complete media_files scope and per-file HASH /
    # receipts, but no owned_candidates. One file is uploaded, the other waits.
    batch.host.try_instant = lambda entry, remote, stop: (
        batch.host.upload(Path(entry['local']), remote)
        if entry['local'].endswith('.mkv') else None)
    batch.run()
    job = batch.job()
    assert job['state'] == 'waiting_instant' and not job.get('owned_candidates')
    job.update(history_ids=[101, 102], route_name='旧追更路线', cleanup_local=False)
    batch.store.save(job)
    return batch.job()


def without_host_history(batch, modules):
    unavailable = Mock(side_effect=AssertionError('expired host records must not be required'))
    host = modules.host.MPHost(batch.config,
        chain=NS(torrent_files=unavailable, list_torrents=unavailable), storage=NS(),
        downloads=NS(get_files_by_hash=unavailable),
        transfers=NS(list_by_hash=unavailable, list_by_date=unavailable), extensions={'.mkv', '.ass'})
    host.try_instant = Mock(side_effect=lambda entry, remote, stop: batch.host.upload(Path(entry['local']), remote))
    return host, unavailable


def test_old_sealed_ledger_reopens_without_resetting_receipts_deadlines_or_metadata(batch, modules, monkeypatch):
    old = old_waiting_batch(batch)
    deadline = old['files'][1]['instant_next_at']
    metadata = {'activated_at': '2026-10-08 23:56:51', 'history_cursor': '2026-10-09 16:00:00',
                'u115_cloud_cooldown': {'until': deadline, 'reason': 'rate limit', 'at': deadline - 3600}}
    for key, value in metadata.items():
        batch.store.set_meta(key, value)
    batch.store.receive_transfer(103, {'cleanup_local': False})
    for notice in batch.store.pending_notices():
        batch.store.notice_result(notice, True)
    host, unavailable = without_host_history(batch, modules)
    freeze = Mock(side_effect=AssertionError('existing HASH must be reused'))
    monkeypatch.setattr(modules.engine, 'freeze_file', freeze)
    restored = modules.store.Store(batch.store.path.parent)
    engine = modules.engine.Engine(restored, batch.config, host, batch.cloud, Event())
    engine.process(restored.get(batch.id))
    upgraded = restored.get(batch.id)
    assert len(upgraded['owned_candidates']) == 2
    assert upgraded['files'] == old['files']
    for key in ('id', 'routing', 'route_name', 'history_ids', 'cleanup_local', 'created'):
        assert upgraded[key] == old[key]
    assert {key: restored.meta(key) for key in metadata} == metadata
    assert restored.incoming_count() == 1 and not restored.pending_notices()
    assert restored.observe(**batch.observe)['id'] == batch.id and len(restored.jobs()) == 1
    first_owned_at = upgraded['owned_at']
    engine.process(restored.get(batch.id))
    assert restored.get(batch.id)['owned_at'] == first_owned_at
    host.try_instant.assert_not_called()
    unavailable.assert_not_called()
    assert not batch.cloud.moves
    # After the original deadline, only the still-pending subtitle is sent.
    monkeypatch.setattr(modules.engine.time, 'time', lambda: deadline + 1)
    engine.process(restored.get(batch.id))
    assert restored.get(batch.id)['state'] == 'handed_off'
    assert host.try_instant.call_count == 1 and len(batch.host.uploads) == 2
    assert len(batch.cloud.moves) == 1 and restored.get(batch.id)['files'][0] == old['files'][0]


@pytest.mark.parametrize('missing', ['partial_hash', 'media_scope', 'sha1'])
def test_incomplete_old_evidence_is_not_promoted_to_complete_ownership(batch, missing):
    job = old_waiting_batch(batch)
    if missing == 'partial_hash':
        job['files'].pop()
    elif missing == 'media_scope':
        job.pop('media_files')
    else:
        job['files'][0].pop('sha1')
    before = deepcopy(job)
    ownership().migrate_sealed(job)
    assert job == before and not job.get('owned_candidates')


def test_received_attachment_upgrades_old_scope_before_ack_and_retains_all_history_ids(batch, plugin, modules, monkeypatch):
    old = old_waiting_batch(batch)
    extra = Path(batch.paths[0]).with_suffix('.later.ass')
    extra.write_bytes(b'new completed subtitle')
    p = plugin.p
    p._store = batch.store
    p._runtime = replace(p._runtime, store=batch.store)
    row = NS(id=103, status=True, download_hash=old['download_hash'], downloader=old['downloader'],
             title=old['title'], dest=str(extra), dest_storage='local')
    plugin.host.transfers.get.return_value = row
    batch.store.receive_transfer(103)
    p._receive_pending(p._runtime)
    saved = batch.job()
    assert not batch.store.incoming_count()
    assert {c['local'] for c in saved['owned_candidates']} == {*batch.paths, str(extra)}
    assert saved['files'] == old['files']
    # Replayed events neither drop the new member nor create another batch.
    p._observe(p._runtime, row)
    assert len(batch.job()['owned_candidates']) == 3 and len(batch.store.jobs()) == 1
    host, unavailable = without_host_history(batch, modules)
    monkeypatch.setattr(modules.engine.time, 'time', lambda: old['files'][1]['instant_next_at'] + 1)
    restored = modules.store.Store(batch.store.path.parent)
    modules.engine.Engine(restored, batch.config, host, batch.cloud, Event()).process(restored.get(batch.id))
    completed = restored.get(batch.id)
    assert completed['state'] == 'handed_off', completed['message']
    assert completed['history_ids'] == [101, 102, 103]
    assert len(completed['files']) == 3 and len(batch.cloud.moves) == 1
    unavailable.assert_not_called()


def test_upgrade_reconciles_old_move_intent_even_after_local_output_is_gone(batch, modules):
    batch.cloud.move_hook = lambda: (_ for _ in ()).throw(RuntimeError('response lost'))
    batch.run()
    old = batch.job()
    assert old['move_requested'] and len(batch.cloud.moves) == 1
    for path in batch.paths:
        Path(path).unlink()
    host, unavailable = without_host_history(batch, modules)
    restored = modules.store.Store(batch.store.path.parent)
    modules.engine.Engine(restored, batch.config, host, batch.cloud, Event()).process(restored.get(batch.id))
    completed = restored.get(batch.id)
    assert completed['state'] == 'handed_off'
    assert completed['source'] == old['source'] and completed['destination'] == old['destination']
    assert len(batch.cloud.moves) == 1 and completed['files'] == old['files']
    host.try_instant.assert_not_called()
    unavailable.assert_not_called()


def test_old_cleanup_isolation_intent_resumes_when_seeding_source_has_expired(batch, modules, tmp_path):
    sources = cleanup_fixture(batch, modules, tmp_path)
    job = batch.job()
    entry = job['files'][0]
    path = Path(entry['local'])
    # Same durable path format produced by the 1.5 cleanup implementation.
    folder = path.parent / '.115-helper-clean-old-version'
    folder.mkdir()
    pending = folder / path.name
    entry['cleanup_pending'] = str(pending)
    batch.store.save(job)
    os.rename(path, pending)
    for source in sources:
        source.unlink()
    restored = modules.store.Store(batch.store.path.parent)
    modules.cleanup.cleanup(restored, restored.get(batch.id), batch.config, Event())
    completed = restored.get(batch.id)
    assert completed['cleanup_done'] and all(e['local_removed'] for e in completed['files'])
    assert not pending.exists() and not any(Path(p).exists() for p in batch.paths)
    assert len(batch.cloud.moves) == 1


def test_legacy_missing_cleanup_choice_never_enables_deletion_on_upgrade(batch, modules):
    batch.run()
    old = batch.job()
    old.pop('cleanup_local')
    batch.store.save(old)
    restored = modules.store.Store(batch.store.path.parent)
    modules.cleanup.cleanup(restored, restored.get(batch.id), batch.config, Event())
    assert all(Path(p).exists() for p in batch.paths)
    assert restored.get(batch.id) == old
