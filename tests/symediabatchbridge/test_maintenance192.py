"""Control work advances cooperatively without a second media owner."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

from test_batches import batch
from test_plugin_contract import plugin, add_job


def test_hash_progress_runs_control_work_before_the_whole_file_finishes(batch):
    called = []
    batch.engine.maintenance = lambda: called.append(batch.job()['state'])
    entry = batch.engine._hash_file(batch.job(), batch.paths[0], 'S01E01.mkv')
    assert entry['sha1'] and len(called) >= 2
    assert not batch.host.uploads and not batch.cloud.moves


def test_control_pump_throttles_and_excludes_hash_owner(plugin, monkeypatch):
    p, runtime = plugin.p, plugin.p._runtime
    now = [100.0]
    monkeypatch.setattr(plugin.modules.plugin.time, 'monotonic', lambda: now[0])
    select = Mock(return_value=[])
    runtime.store.maintenance_move_jobs = select
    p._maintain(runtime, exclude=('hash-owner',))
    select.assert_called_once_with(limit=3, exclude=('hash-owner',))
    now[0] += 5
    p._maintain(runtime)
    assert select.call_count == 1
    now[0] += 60
    p._maintain(runtime)
    assert select.call_count == 2 and not runtime.maintaining


def test_control_pump_cleans_before_pending_move_without_hashing(plugin, monkeypatch):
    p, runtime = plugin.p, plugin.p._runtime
    runtime.config = replace(runtime.config, cleanup_inbox=True)
    old = add_job(p)
    old.update(state='handed_off')
    runtime.store.save(old)
    moving = runtime.store.observe(instance='SymediaBatchBridge', download_hash='second', downloader='qb',
        title='next', history_id=2, routing=runtime.config.routing())
    order = []
    runtime.store.work_cleanup_jobs = lambda: [old]
    runtime.store.housekeeping_jobs = lambda _: []
    runtime.store.maintenance_move_jobs = lambda **_: [moving]
    monkeypatch.setattr(plugin.modules.plugin.housekeeping, 'process', lambda *a, **k: order.append('cleanup'))

    class ControlEngine:
        def __init__(self, *a, **k):
            pass
        def process(self, job):
            assert job['id'] == moving['id']
            order.append('move')
            job.update(state='handed_off')
            runtime.store.save(job)
    monkeypatch.setattr(plugin.modules.plugin, 'Engine', ControlEngine)
    p._maintain(runtime, force=True)
    assert order == ['cleanup', 'move']
    assert runtime.store.get(moving['id'])['state'] == 'handed_off'
    plugin.host.collect.assert_not_called()


def test_maintenance_failure_keeps_hash_callback_safe_and_retryable(plugin):
    p, runtime = plugin.p, plugin.p._runtime
    runtime.store.pending_routings = Mock(side_effect=RuntimeError('private error'))
    p._maintain(runtime, force=True)
    assert not runtime.maintaining and runtime.maintenance_at > 0
    assert p.get_state()


def test_disabled_cleanup_never_moves_or_deletes_empty_folders(plugin):
    p, runtime = plugin.p, plugin.p._runtime
    runtime.store.work_cleanup_jobs = Mock()
    runtime.store.maintenance_move_jobs = Mock(return_value=[])
    assert not runtime.config.cleanup_inbox
    p._maintain(runtime, force=True)
    runtime.store.work_cleanup_jobs.assert_not_called()


def test_stop_disables_control_pump(plugin):
    p, runtime = plugin.p, plugin.p._runtime
    runtime.store.maintenance_move_jobs = Mock()
    runtime.stop.set()
    p._maintain(runtime, force=True)
    runtime.store.maintenance_move_jobs.assert_not_called()


def test_board_attention_count_matches_long_blocked_row(plugin, monkeypatch):
    p = plugin.p
    now = 1_900_000_000
    monkeypatch.setattr(plugin.modules.store.time, 'time', lambda: now)
    job = add_job(p)
    job.update(state='moving', blocked_by={'batch': 'previous', 'destination': '/115/inbox/work',
                                         'reason': 'retired_pending_recycle', 'since': now - 1801})
    p._store.save(job)
    board = p._store.board(status='attention')
    assert board['counts']['attention'] == 1 and board['total'] == 1
    assert board['jobs'][0]['id'] == job['id']
    assert plugin.modules.activity.attention(board['jobs'][0])
