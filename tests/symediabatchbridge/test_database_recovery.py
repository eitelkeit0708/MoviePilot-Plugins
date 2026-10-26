"""Real lock contention, rollback, returned notification receipts and old queues."""
from copy import deepcopy
import importlib
import json
import sqlite3
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from test_plugin_contract import plugin, add_job


def parts():
    return (importlib.import_module('app.plugins.symediabatchbridge.database'),
            importlib.import_module('app.plugins.symediabatchbridge.delivery'))


def queued(plugin):
    p = plugin.p
    job = add_job(p)
    job.update(state='handed_off', message='已移交', destination='/115/inbox/batch')
    p._store.save(job)
    return p, job, p._store.pending_notices()[0]


def tracked_connections(monkeypatch):
    real_connect, closed = sqlite3.connect, []
    class Connection(sqlite3.Connection):
        def close(self):
            super().close()
            closed.append(self)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: real_connect(*a, **{**kw, 'factory': Connection}))
    return real_connect, closed


def test_setup_failure_closes_connection_before_body(plugin, monkeypatch):
    p, job, notice = queued(plugin)
    real_connect, closed = tracked_connections(monkeypatch)
    with real_connect(p._store.path) as owner:
        owner.execute('BEGIN EXCLUSIVE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            # Bypass only the outer retry to inspect one failing connection.
            p._store.pending_notices.__wrapped__(p._store)
        assert len(closed) == 1
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            closed[0].execute('SELECT 1')
    assert p._store.pending_notices()[0]['id'] == notice['id']


def test_real_commit_lock_rolls_back_and_retries_notice_atomically(plugin, monkeypatch):
    p = plugin.p
    job = add_job(p)
    before = deepcopy(job)
    job.update(state='handed_off', message='已移交', destination='/115/inbox/batch')
    real_connect, closed = tracked_connections(monkeypatch)
    reader = real_connect(p._store.path)
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM batches').fetchall()
    finished, errors = Event(), []
    def write():
        try:
            p._store.save(job)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    writer = Thread(target=write)
    writer.start()
    try:
        # A separate connection observes the reader-pinned old state. No partial
        # handoff activity or notice can escape the failed commit.
        assert not finished.wait(0.4)
        assert closed
        assert json.loads(reader.execute('SELECT body FROM batches').fetchone()[0]) == before
    finally:
        reader.rollback(); reader.close()
        writer.join(4)
    assert not writer.is_alive() and not errors
    assert p._store.notice_counts(job['id']) == (1, 0)
    assert [e['kind'] for e in p._store.events(job['id'])].count('handoff') == 1


def test_real_exclusive_lock_defers_tick_then_automatically_delivers(plugin, monkeypatch):
    p, job, notice = queued(plugin)
    database, delivery = parts()
    monkeypatch.setattr(database, 'BUSY_TIMEOUT', 0.02)
    with sqlite3.connect(p._store.path) as owner:
        owner.execute('BEGIN EXCLUSIVE')
        p._flush_notifications()
        p.post_message.assert_not_called()
        assert not (p._store.path.parent/'notification-result.json').exists()
    assert not p._notifying.locked()
    p._flush_notifications()
    assert p.post_message.call_count == 1
    assert p._store.notice_counts(job['id']) == (1, 1)
    p._flush_notifications()
    assert p.post_message.call_count == 1


def test_returned_result_survives_database_lock_reload_and_reopen(plugin, monkeypatch):
    p, job, notice = queued(plugin)
    database, delivery = parts()
    monkeypatch.setattr(database, 'BUSY_TIMEOUT', 0.02)
    owner = sqlite3.connect(p._store.path)
    def accepted(**kwargs):
        owner.execute('BEGIN EXCLUSIVE')
    p.post_message.side_effect = accepted
    p._flush_notifications()
    journal = p._store.path.parent/'notification-result.json'
    assert journal.exists() and p.post_message.call_count == 1
    assert json.loads(journal.read_text(encoding='utf-8'))['success'] is True
    owner.rollback(); owner.close()
    p.stop_service()
    # A completely new instance cannot rely on the old Python result cache.
    cls = plugin.modules.plugin.SymediaBatchBridge
    restored = cls()
    restored.init_plugin(plugin.values)
    restored.post_message = Mock()
    restored._flush_notifications()
    restored.post_message.assert_not_called()
    assert not journal.exists()
    assert restored._store.notice_counts(job['id']) == (1, 1)
    assert [e['kind'] for e in restored._store.events(job['id'])].count('notification') == 1


def test_result_is_idempotent_if_commit_finished_before_journal_cleanup(plugin):
    p, job, notice = queued(plugin)
    database, delivery = parts()
    delivery.stage_result(p._store, notice, True, 1_900_000_001)
    receipt = p._store.path.parent/'notification-result.json'
    p._store.notice_result(notice, True, at=1_900_000_001)
    p._flush_notifications()
    p.post_message.assert_not_called()
    assert not receipt.exists()
    assert [e['kind'] for e in p._store.events(job['id'])].count('notification') == 1


def test_failed_result_is_not_counted_twice_after_journal_replay(plugin):
    p, job, notice = queued(plugin)
    database, delivery = parts()
    delivery.stage_result(p._store, notice, False, 1_900_000_001)
    p._store.notice_result(notice, False, at=1_900_000_001)
    delivery.recover_result(p._store)
    with sqlite3.connect(p._store.path) as db:
        assert db.execute('SELECT attempts,next_at,submitted_at FROM notices').fetchone() == (1, 1_900_000_061, 0)
    assert [e['kind'] for e in p._store.events(job['id'])].count('notification') == 1


def test_journal_io_failure_retains_returned_result_without_resending(plugin, monkeypatch):
    p, job, notice = queued(plugin)
    original = plugin.modules.plugin.stage_result
    monkeypatch.setattr(plugin.modules.plugin, 'stage_result', Mock(side_effect=PermissionError('secret-path')))
    p._flush_notifications()
    assert p._notice_result_pending and p.post_message.call_count == 1
    p._flush_notifications()
    assert p.post_message.call_count == 1
    monkeypatch.setattr(plugin.modules.plugin, 'stage_result', original)
    p._flush_notifications()
    assert p._notice_result_pending is None and p.post_message.call_count == 1
    assert p._store.notice_counts(job['id']) == (1, 1)


@pytest.mark.parametrize('content', ['{broken', '[]', '{"schema": 100}'])
def test_corrupt_returned_result_blocks_sending_and_is_not_deleted(plugin, content):
    p, job, notice = queued(plugin)
    path = p._store.path.parent/'notification-result.json'
    path.write_text(content, encoding='utf-8')
    p._flush_notifications()
    p.post_message.assert_not_called()
    assert path.read_text(encoding='utf-8') == content


def test_receipt_for_another_queue_payload_is_never_applied(plugin):
    p, job, notice = queued(plugin)
    database, delivery = parts()
    delivery.stage_result(p._store, {**notice, 'text':'different payload'}, True, 1_900_000_001)
    p._flush_notifications()
    assert p._store.notice_counts(job['id']) == (1, 0)
    assert (p._store.path.parent/'notification-result.json').exists()
    p.post_message.assert_not_called()


def test_non_lock_database_errors_are_not_retried(plugin, monkeypatch):
    database, delivery = parts()
    calls = []
    @database.database_operation
    def broken():
        calls.append(1)
        raise sqlite3.OperationalError('disk I/O error')
    with pytest.raises(sqlite3.OperationalError, match='disk I/O error'):
        broken()
    assert calls == [1]


def test_restart_preserves_old_batches_cooldown_and_unsent_notice_attempts(plugin):
    p, job, notice = queued(plugin)
    p._store.notice_result(notice, False, at=1_900_000_001)
    p._store.set_meta('u115_cloud_cooldown', {'until': 1_900_010_000, 'reason':'limit'})
    p._store.receive_transfer(77, {'cleanup_local': False})
    with sqlite3.connect(p._store.path) as db:
        tables = ['batches','notices','activity','meta','incoming_transfers']
        before = {table:db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
    reopened = plugin.modules.store.Store(p._store.path.parent)
    with sqlite3.connect(reopened.path) as db:
        assert {table:db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables} == before
        assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
