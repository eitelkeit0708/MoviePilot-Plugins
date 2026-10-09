"""Ownership lifetime and destructive recovery, using only disposable temp files."""
import importlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_adapters import native
from test_batches import batch, inventory_batch
from test_activity import cleanup_fixture
from test_plugin_contract import plugin, add_job


def mod(name):
    return importlib.import_module('app.plugins.symediabatchbridge.' + name)


def test_source_unlink_really_preserves_organized_hardlink_and_cleanup(batch, modules, tmp_path):
    sources = cleanup_fixture(batch, modules, tmp_path)
    local = Path(batch.paths[0])
    before = local.read_bytes()
    sources[0].unlink()
    assert local.read_bytes() == before and local.stat().st_nlink == 1
    modules.cleanup.cleanup(batch.store, batch.job(), batch.config, batch.stop)
    assert batch.job()['cleanup_done'] and not local.exists()
    assert sources[1].exists()


def test_owned_manifest_persists_before_first_hash_and_survives_expired_tasks(batch, native, modules, monkeypatch):
    job = batch.job()
    job.update(download_hash='hash', downloader='qb')
    batch.store.save(job)
    native.host.try_instant = batch.host.try_instant
    batch.engine.host = native.host
    original = batch.engine._hash_file
    def crash(*args, **kwargs):
        raise SystemExit('restart before hashing')
    monkeypatch.setattr(batch.engine, '_hash_file', crash)
    with pytest.raises(SystemExit):
        batch.run()
    accepted = batch.job()
    assert len(accepted['owned_candidates']) == 2 and not accepted['files']
    for row in native.rows:
        Path(row.src).unlink()
    for method in (native.chain.torrent_files, native.chain.list_torrents,
                   native.downloads.get_files_by_hash, native.transfers.list_by_hash):
        method.side_effect = AssertionError('accepted files must not depend on expired records')
    monkeypatch.setattr(batch.engine, '_hash_file', original)
    batch.run()
    assert batch.job()['state'] == 'handed_off', batch.job()['message']
    assert len(batch.cloud.moves) == 1 and native.video.exists()


def test_owned_snapshot_reuses_hash_when_history_disappears(inventory_batch, modules, monkeypatch):
    b = inventory_batch
    b.cloud.exists = Mock(side_effect=RuntimeError('network offline'))
    b.run()
    assert len(b.job()['files']) == 2
    b.inventory_host.transfers.list_by_date.side_effect = AssertionError('old MP history gone')
    monkeypatch.setattr(modules.engine, 'freeze_file', Mock(side_effect=AssertionError('reuse HASH')))
    del b.cloud.exists
    b.run()
    assert b.job()['state'] == 'handed_off'


def test_completed_new_attachment_is_added_once_without_changing_old_scope(native, modules):
    native.host.collect(native.job)
    extra = native.video.with_suffix('.later.srt')
    extra.write_bytes(b'new subtitle')
    candidate = dict(local=str(extra), history_id=3, media={})
    ownership = mod('ownership')
    native.job['state'] = 'waiting'
    assert ownership.append_transfer(native.job, native.config, candidate)
    assert not ownership.append_transfer(native.job, native.config, candidate)
    assert len(native.host.collect(native.job)) == 3
    native.job['move_requested'] = True
    assert not ownership.append_transfer(native.job, native.config, dict(local=str(extra) + '.ass', history_id=4))


def test_pending_attachment_is_received_before_directory_handoff(inventory_batch):
    b = inventory_batch
    extra = Path(b.paths[0]).with_suffix('.extra.srt')
    def receive():
        if extra.exists():
            return
        extra.write_bytes(b'late completed attachment')
        latest = b.job()
        assert mod('ownership').append_transfer(latest, b.config, {'local': str(extra), 'history_id': 100})
        b.store.save(latest)
    b.engine.receive_pending = receive
    b.run()
    assert b.job()['state'] == 'waiting' and not b.cloud.moves
    assert len(b.job()['owned_candidates']) == 3
    b.run()
    assert b.job()['state'] == 'handed_off' and len(b.cloud.moves) == 1
    assert len(b.host.uploads) == 3


def test_unresolved_received_event_holds_handoff_but_preserves_uploads(inventory_batch, modules):
    b = inventory_batch
    extra = Path(b.paths[0]).with_suffix('.extra.ass')
    def partial_intake():
        extra.write_bytes(b'accepted before next event failed')
        current = b.job()
        mod('ownership').append_transfer(current, b.config, {'local': str(extra), 'history_id': 101})
        b.store.save(current)
        raise modules.domain.Awaiting('pending transfer event')
    b.engine.receive_pending = partial_intake
    b.run()
    assert not b.cloud.moves and all(e['uploaded'] for e in b.job()['files'])
    assert len(b.job()['owned_candidates']) == 3
    b.engine.receive_pending = lambda: None
    b.run()
    assert b.job()['state'] == 'handed_off' and len(b.host.uploads) == 3


def test_cleanup_transient_failure_retries_without_upload_or_original(batch, modules, tmp_path, monkeypatch):
    sources = cleanup_fixture(batch, modules, tmp_path)
    for source in sources:
        source.unlink()
    real = mod('localfiles').os.rename
    monkeypatch.setattr(mod('localfiles').os, 'rename', Mock(side_effect=PermissionError('busy')))
    modules.cleanup.cleanup(batch.store, batch.job(), batch.config, batch.stop)
    saved = batch.job()
    assert saved['cleanup_next'] > saved['updated'] and not saved.get('cleanup_done')
    assert all(Path(p).exists() for p in batch.paths)
    monkeypatch.setattr(mod('localfiles').os, 'rename', real)
    modules.cleanup.cleanup(batch.store, batch.job(), batch.config, batch.stop)
    assert batch.job()['cleanup_done'] and len(batch.cloud.moves) == 1


@pytest.fixture
def orphan(plugin):
    p = plugin.p
    root = Path(plugin.values['local_root'])
    folder = root / 'test-work'
    folder.mkdir()
    subtitle = folder / 'episode.zh.ass'
    subtitle.write_bytes(b'disposable subtitle')
    job = add_job(p)
    job.update(state='review', origin='inventory', message='现存批次只有字幕，没有对应媒体文件',
               inventory_files=[dict(local=str(subtitle), history_id=1,
                                     signature=plugin.modules.domain.file_signature(subtitle))])
    p._store.save(job)
    return p, job, subtitle, plugin.modules


def request(p, modules, job, action, token=''):
    return p.dispose_batch(modules.plugin.DisposalRequest(key=job['id'], action=action, token=token))


def test_delete_requires_exact_preview_confirmation_then_scheduler_completes(orphan):
    p, job, subtitle, modules = orphan
    assert not request(p, modules, job, 'confirm', 'invented').success
    assert request(p, modules, job, 'delete').success
    plan = p._store.get(job['id'])['disposal_plan']
    assert plan['files'][0]['local'] == str(subtitle) and subtitle.exists()
    page = json.dumps(p.get_page(), ensure_ascii=False)
    assert '确认删除字幕' in page and str(subtitle).replace('\\', '\\\\') in page
    assert request(p, modules, job, 'confirm', plan['token']).success
    # Confirmation persists intent, but never performs slow work inside the API.
    assert subtitle.exists() and p._store.get(job['id'])['state'] == 'deleting'
    p.check_batches()
    saved = p._store.get(job['id'])
    assert not subtitle.exists() and saved['state'] == 'cancelled'
    assert saved['deletion_files'][0]['deleted']
    assert p._store.board(status='closed')['total'] == 1
    assert not p._store.due() and not p._store.pending_notices()
    assert request(p, modules, job, 'confirm', plan['token']).success
    replay = add_job(p)
    assert replay['id'] == job['id'] and replay['state'] == 'cancelled'
    assert not p.retry_batch(modules.plugin.RetryRequest(key=job['id'])).success


@pytest.mark.parametrize('change', ['media', 'replacement', 'expired', 'outside'])
def test_delete_confirmation_revalidates_scope_and_never_deletes_new_files(orphan, change):
    p, job, subtitle, modules = orphan
    assert request(p, modules, job, 'delete').success
    saved = p._store.get(job['id'])
    token = saved['disposal_plan']['token']
    if change == 'media':
        subtitle.with_suffix('.mkv').write_bytes(b'video arrived')
    elif change == 'replacement':
        subtitle.write_bytes(b'a different subtitle')
    elif change == 'expired':
        saved['disposal_plan']['expires'] = 1
        p._store.save(saved)
    else:
        outside = subtitle.parent.parent.parent / 'outside.ass'
        outside.write_bytes(b'outside')
        saved['inventory_files'][0]['local'] = str(outside)
        p._store.save(saved)
    assert not request(p, modules, job, 'confirm', token).success
    assert subtitle.exists() and p._store.get(job['id'])['state'] == 'review'


def test_no_media_scan_error_is_not_treated_as_empty_directory(orphan, monkeypatch):
    p, job, subtitle, modules = orphan
    monkeypatch.setattr(mod('disposal').os, 'scandir', Mock(side_effect=PermissionError('unreadable')))
    assert not request(p, modules, job, 'delete').success
    assert subtitle.exists() and 'disposal_plan' not in p._store.get(job['id'])


def test_delete_survives_process_death_after_isolation_without_repeating_side_effect(orphan, monkeypatch):
    p, job, subtitle, modules = orphan
    request(p, modules, job, 'delete')
    token = p._store.get(job['id'])['disposal_plan']['token']
    request(p, modules, job, 'confirm', token)
    original = mod('localfiles').os.rename
    def crash(src, dest):
        original(src, dest)
        raise SystemExit('process died after rename')
    monkeypatch.setattr(mod('localfiles').os, 'rename', crash)
    with pytest.raises(SystemExit):
        p.check_batches()
    saved = p._store.get(job['id'])
    assert Path(saved['deletion_files'][0]['delete_pending']).exists()
    monkeypatch.setattr(mod('localfiles').os, 'rename', original)
    # Reopen SQLite to model a restarted worker.
    store = modules.store.Store(p._store.path.parent)
    mod('disposal').process(store, store.get(job['id']), p._runtime.config, p._runtime.stop)
    assert store.get(job['id'])['state'] == 'cancelled' and not subtitle.exists()


def test_delete_race_preserves_replacement_and_retries_only_confirmed_files(orphan, monkeypatch):
    p, job, subtitle, modules = orphan
    request(p, modules, job, 'delete')
    request(p, modules, job, 'confirm', p._store.get(job['id'])['disposal_plan']['token'])
    original = mod('localfiles').os.rename
    def race(src, dest):
        Path(src).write_bytes(b'new subtitle from another operation')
        original(src, dest)
    monkeypatch.setattr(mod('localfiles').os, 'rename', race)
    p.check_batches()
    assert subtitle.read_bytes() == b'new subtitle from another operation'
    saved = p._store.get(job['id'])
    assert saved['state'] == 'deleting' and saved['disposal_error'] and saved['next_check'] > saved['updated']


def test_delete_permission_retry_resumes_completed_files(orphan, monkeypatch):
    p, job, subtitle, modules = orphan
    extra = subtitle.with_suffix('.srt')
    extra.write_bytes(b'second subtitle')
    job['inventory_files'].append(dict(local=str(extra), history_id=2, signature=modules.domain.file_signature(extra)))
    p._store.save(job)
    request(p, modules, job, 'delete')
    request(p, modules, job, 'confirm', p._store.get(job['id'])['disposal_plan']['token'])
    original = Path.unlink
    def deny(path, *args, **kwargs):
        if path.name == extra.name:
            raise PermissionError('temporary file lock')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', deny)
    p.check_batches()
    saved = p._store.get(job['id'])
    assert saved['state'] == 'deleting' and sum(e.get('deleted', False) for e in saved['deletion_files']) == 1
    assert not subtitle.exists() and saved['next_check'] > saved['updated']
    monkeypatch.setattr(Path, 'unlink', original)
    mod('disposal').process(p._store, saved, p._runtime.config, p._runtime.stop)
    assert p._store.get(job['id'])['state'] == 'cancelled'
    assert not list(subtitle.parent.rglob('*.srt'))


def test_deleting_route_failure_does_not_convert_into_upload_retry(orphan):
    p, job, subtitle, modules = orphan
    request(p, modules, job, 'delete')
    request(p, modules, job, 'confirm', p._store.get(job['id'])['disposal_plan']['token'])
    saved = p._store.get(job['id'])
    saved['routing']['cd2_prefix'] = '/changed'
    p._store.save(saved)
    p.check_batches()
    saved = p._store.get(job['id'])
    assert saved['state'] == 'deleting' and saved['disposal_error']
    assert subtitle.exists()


def test_terminate_keeps_files_and_history_and_never_reports_handoff(orphan):
    p, job, subtitle, modules = orphan
    request(p, modules, job, 'stop')
    token = p._store.get(job['id'])['disposal_plan']['token']
    assert request(p, modules, job, 'confirm', token).success
    assert subtitle.exists() and not p._store.due()
    assert not modules.activity.attention(p._store.get(job['id']))
    assert not any(item['kind'] in ('handoff', 'recovered') for item in p._store.events(job['id']))


def test_notification_prefetched_before_termination_is_not_submitted_afterward(orphan, monkeypatch):
    p, job, subtitle, modules = orphan
    pending = p._store.pending_notices()
    assert pending
    request(p, modules, job, 'stop')
    request(p, modules, job, 'confirm', p._store.get(job['id'])['disposal_plan']['token'])
    monkeypatch.setattr(p._store, 'pending_notices', lambda: pending)
    p._flush_notifications()
    p.post_message.assert_not_called()


def test_uncertain_move_cannot_be_terminated_or_delete_subtitles(orphan):
    p, job, subtitle, modules = orphan
    job['move_requested'] = True
    p._store.save(job)
    assert not request(p, modules, job, 'stop').success
    assert not request(p, modules, job, 'delete').success
    assert subtitle.exists()


def test_successful_move_reconciliation_clears_queued_retry_attention(batch, modules):
    batch.cloud.move_hook = Mock(side_effect=RuntimeError('lost receipt'))
    batch.run()
    job = batch.job()
    assert job['move_requested']
    job['retry_requested'] = 1
    batch.store.save(job)
    batch.run()
    assert batch.job()['state'] == 'handed_off'
    assert not modules.activity.attention(batch.job())
