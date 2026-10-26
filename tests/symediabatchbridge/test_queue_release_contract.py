import importlib
from dataclasses import replace
from pathlib import Path, PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

import pytest


def part(name):
    return importlib.import_module('app.plugins.symediabatchbridge.' + name)


NAME = "美国恐怖故事 (2011) {tmdb-1413}"


@pytest.fixture
def clock(monkeypatch):
    value = NS(now=1_800_000_000.0)
    monkeypatch.setattr(time, 'time', lambda: value.now)
    return value


class Cloud:
    def __init__(self):
        self.nodes = {}
        self.moves = []
        self.deletes = []

    def exists(self, path):
        return path in self.nodes

    def directory_id(self, path):
        return self.nodes[path]['id'] if path in self.nodes else ''

    def tree(self, path):
        return dict(self.nodes[path]['files'])

    def directory_only_children(self, path):
        return not self.nodes[path]['files']

    def _children(self, path):
        return []

    def ensure_directory(self, path):
        self.nodes.setdefault(path, {'id': str(9000 + len(self.nodes)), 'files': {}})
        return self.nodes[path]['id']

    def move_directory(self, source, parent):
        target = parent + '/' + PurePosixPath(source).name
        if target in self.nodes:
            return False
        self.nodes[target] = self.nodes.pop(source)
        self.moves.append((source, parent))
        return True


class Host:
    def __init__(self, cloud):
        self.cloud = cloud
        self.gate_cloud = Mock()
        self.moves = []
        self.empty = None

    def empty_directory(self, path, ident):
        if self.empty is not None:
            return self.empty(path, ident)
        node = self.cloud.nodes.get(path)
        return bool(node and node['id'] == ident and not node['files'])

    def delete_retired_empty(self, path, ident, batch_id):
        node = self.cloud.nodes.get(path)
        assert node and node['id'] == ident and not node['files']
        assert path.startswith(self.cloud.config.cd2_staging + '/_空目录清理/' + batch_id + '/')
        self.cloud.deletes.append((path, ident))
        self.cloud.nodes.pop(path)


@pytest.fixture
def work(modules, config_values, tmp_path, clock):
    config = modules.domain.Config.parse({**config_values, 'cleanup_inbox': True})
    store = modules.store.Store(tmp_path / 'ledger')
    cloud = Cloud()
    cloud.config = config
    host = Host(cloud)
    counter = [1]

    def make(name=NAME, *, phase='arrived', state='handed_off', ident='123',
             history=None, plan=100.0, second=None, move_requested=True):
        nonlocal_counter = counter[0]
        counter[0] += 1
        history = nonlocal_counter if history is None else history
        job = store.observe(instance='Test', download_hash=f'h-{history}-{name}',
                            downloader='qb', title=name, history_id=history,
                            routing=config.routing())
        prefix = name
        dest = config.inbox + '/' + prefix
        src = modules.domain.child_path(modules.domain.child_path(config.cd2_staging, job['id']), prefix)
        entry = {'relative': 'Season 01/E01.mkv', 'sha1': 'a' * 40, 'size': 1,
                 'local': str(tmp_path / 'E01.mkv')}
        unit = {'source': src, 'destination': dest, 'prefix': prefix, 'id': str(ident),
                'tmdbid': '1413', 'files': [dict(entry)], 'phase': phase}
        if phase in ('requested', 'arrived'):
            unit['requested_at'] = clock.now - 120
        if phase == 'arrived':
            unit['arrived_at'] = clock.now - 120
        files = [{'relative': prefix + '/' + entry['relative'],
                  **{k: v for k, v in entry.items() if k != 'relative'}}]
        units = [unit]
        if second:
            name2, phase2, ident2 = second
            prefix2 = name2
            dest2 = config.inbox + '/' + prefix2
            src2 = modules.domain.child_path(
                modules.domain.child_path(config.cd2_staging, job['id']), prefix2)
            entry2 = {'relative': 'Season 02/E01.mkv', 'sha1': 'b' * 40, 'size': 2,
                      'local': str(tmp_path / 'S02E01.mkv')}
            unit2 = {'source': src2, 'destination': dest2, 'prefix': prefix2, 'id': str(ident2),
                     'tmdbid': '999', 'files': [dict(entry2)], 'phase': phase2}
            units.append(unit2)
            files.append({'relative': prefix2 + '/' + entry2['relative'],
                          **{k: v for k, v in entry2.items() if k != 'relative'}})
        job.update(handoff_layout='work', state=state, move_requested=move_requested,
                   work_plan_at=plan, work_handoffs=units, files=files,
                   destination=dest, next_check=0, attempts=0)
        store.save(job)
        return store.get(job['id'])

    def reload():
        return modules.store.Store(store.path.parent)

    def holding(job, index=0, *, phase='holding', empty=True, ident=None, flags=False,
                retired=False, source_present=False, source_id=None):
        unit = job['work_handoffs'][index]
        path = store.work_holding_path(job, unit, 'work' + str(index))
        cloud.nodes[path] = {'id': ident or unit['id'], 'files': {} if empty else {'late.ass': 1}}
        if source_present:
            cloud.nodes[unit['source']] = {'id': source_id or unit['id'], 'files': {'x.mkv': 1}}
        else:
            cloud.nodes.pop(unit['source'], None)
        record = {'source': unit['destination'], 'id': unit['id'], 'interval': 300,
                  'phase': phase, 'holding': path}
        if retired:
            record['retired_at'] = clock.now - 1000
        if flags:
            record.update(inbox_released_at=clock.now - 50, released_directory_id=unit['id'])
        unit['cleanup'] = record
        store.save(job)
        return record, path

    def process(job, *, cleanup=True, stop=None):
        cfg = config if cleanup else replace(config, cleanup_inbox=False)
        return part('housekeeping').process(store.get(job['id']), cfg, host, cloud, store,
                                            stop or Event())

    return NS(modules=modules, config=config, store=store, cloud=cloud, host=host,
              make=make, reload=reload, holding=holding, process=process, clock=clock,
              house=part('housekeeping'), workhandoff=part('workhandoff'), tmp=tmp_path)


def waiters(env, count=1, plan=300.0):
    return [env.make(history=200 + i, plan=plan + i, state='moving', phase='prepared')
            for i in range(count)]


def test_release_flags_require_holding_id_and_native_empty_then_recycle(work):
    old = work.make()
    record, holding = work.holding(old, phase='holding', empty=True)
    work.process(old)
    saved = work.store.get(old['id'])
    unit = saved['work_handoffs'][0]
    assert unit['cleanup']['released_directory_id'] == '123'
    assert unit['cleanup']['inbox_released_at'] > 0
    new = work.make(history=301, plan=500, state='moving', phase='prepared')
    assert work.store.work_blocker(new, unit['destination']) is None
    # Restart must keep the journal proof and continue physical recycling.
    restarted = work.reload()
    saved = restarted.get(old['id'])
    unit = saved['work_handoffs'][0]
    assert unit['cleanup']['released_directory_id'] == '123'
    assert restarted.work_blocker(new, unit['destination']) is None
    work.clock.now += 301
    work.process(saved)
    assert work.cloud.deletes and holding not in work.cloud.nodes
    work.clock.now += 301
    work.process(work.store.get(old['id']))
    work.clock.now += 301
    work.process(work.store.get(old['id']))
    assert work.store.get(old['id'])['work_handoffs'][0]['cleanup']['done']


@pytest.mark.parametrize('mode', ['native_false', 'holding_unknown'])
def test_no_release_without_native_empty_or_exact_holding_id(work, mode):
    old = work.make()
    record, holding = work.holding(old, phase='holding', empty=True,
                                   ident='999' if mode == 'holding_unknown' else None)
    if mode == 'native_false':
        work.host.empty = lambda path, ident: False if path == holding else not work.cloud.nodes[path]['files']
    work.process(old)
    saved = work.store.get(old['id'])
    unit = saved['work_handoffs'][0]
    assert 'released_directory_id' not in unit['cleanup']
    assert not unit['cleanup'].get('done')
    assert work.store.work_blocker(waiters(work)[0], unit['destination']) == old['id']
    assert not work.cloud.deletes
    assert saved['inbox_cleanup']['error']


def test_legacy_holding_and_recycling_backfill_after_restart(work):
    old = work.make()
    record, holding = work.holding(old, phase='recycling', empty=True, retired=True)
    restarted = work.reload()
    work.process(restarted.get(old['id']))
    unit = work.store.get(old['id'])['work_handoffs'][0]
    assert unit['cleanup']['released_directory_id'] == '123'
    assert work.cloud.deletes  # release did not postpone physical recycle
    assert work.store.work_blocker(waiters(work)[0], unit['destination']) is None


def test_late_file_clears_release_and_restores_old_generation(work):
    old = work.make()
    record, holding = work.holding(old, phase='holding', empty=True, flags=True)
    work.process(old)
    assert work.store.get(old['id'])['work_handoffs'][0]['cleanup']['released_directory_id'] == '123'
    work.cloud.nodes[holding]['files'] = {'late.ass': 1}
    work.clock.now += 301
    work.process(work.store.get(old['id']))
    saved = work.store.get(old['id'])
    unit = saved['work_handoffs'][0]
    assert unit['cleanup']['phase'] == 'restoring'
    assert 'released_directory_id' not in unit['cleanup']
    assert unit['destination'] in work.cloud.nodes
    assert work.cloud.nodes[unit['destination']]['files'] == {'late.ass': 1}
    assert work.store.work_blocker(waiters(work)[0], unit['destination']) == old['id']
    work.clock.now += 301
    work.process(work.store.get(old['id']))
    saved = work.store.get(old['id'])
    assert saved['work_handoffs'][0]['cleanup']['phase'] == 'observing'
    assert 'released_directory_id' not in saved['work_handoffs'][0]['cleanup']


def test_different_work_id_is_a_conflict_not_a_legacy_release(work):
    old = work.make()
    unit = old['work_handoffs'][0]
    work.cloud.nodes[unit['destination']] = {'id': '999', 'files': {'new.mkv': 1}}
    unit['cleanup'] = {'source': unit['destination'], 'id': unit['id'], 'interval': 300,
                       'phase': 'observing', 'holding': work.store.work_holding_path(old, unit, 'work0')}
    work.store.save(old)
    work.process(old)
    saved = work.store.get(old['id'])
    record = saved['work_handoffs'][0]['cleanup']
    assert not record.get('done') and 'released_directory_id' not in record
    assert '替换' in saved['inbox_cleanup']['error'], saved
    assert work.store.work_blocker(waiters(work)[0], unit['destination']) == old['id']
    assert not work.cloud.moves and not work.cloud.deletes


def test_disabled_cleanup_moves_nothing_and_explains_blocker(work):
    old = work.make()
    unit = old['work_handoffs'][0]
    work.cloud.nodes[unit['source']] = {'id': '123', 'files': {}}
    work.process(old, cleanup=False)
    saved = work.store.get(old['id'])
    assert 'inbox_cleanup' not in saved
    assert not work.cloud.moves and not work.cloud.deletes
    disabled = replace(work.config, cleanup_inbox=False)
    new = work.make(history=401, plan=600, state='moving', phase='prepared')
    with pytest.raises(work.modules.domain.Awaiting) as caught:
        work.workhandoff.reconcile(new, disabled, work.cloud, work.store, Event())
    assert '清理已关闭' in str(caught.value)
    saved = work.store.get(new['id'])
    assert saved['blocked_by']['reason'] == 'cleanup_disabled'
    assert saved['blocked_by']['batch'] == old['id']


def test_arrived_unit_cleans_while_batch_keeps_moving(work):
    old = work.make(state='moving', second=('Another (2025) {tmdb-2}', 'prepared', '456'))
    record, holding = work.holding(old, phase='holding', empty=True)
    selected = work.store.work_cleanup_jobs()
    assert [row['id'] for row in selected] == [old['id']]
    work.process(old)
    saved = work.store.get(old['id'])
    assert saved['work_handoffs'][0]['cleanup']['released_directory_id'] == '123'
    assert saved['work_handoffs'][1]['phase'] == 'prepared'
    assert saved['inbox_cleanup']['done'] is False
    assert old['id'] not in [row['id'] for row in work.store.work_cleanup_jobs()]


def test_per_unit_error_does_not_starve_later_arrived_unit(work):
    old = work.make(second=('Another (2025) {tmdb-2}', 'arrived', '456'))
    first, second = old['work_handoffs']
    work.cloud.nodes[first['destination']] = {'id': first['id'], 'files': {'left.mkv': 1}}
    first['cleanup'] = {'source': first['destination'], 'id': first['id'], 'interval': 300,
                        'phase': 'observing', 'holding': work.store.work_holding_path(old, first, 'work0')}
    record, holding = work.holding(old, 1, phase='holding', empty=True)
    work.store.save(old)
    work.process(old)
    saved = work.store.get(old['id'])
    assert saved['work_handoffs'][1]['cleanup']['released_directory_id'] == '456'
    assert saved['work_handoffs'][0]['cleanup'].get('done') is not True
    assert 'SA 输入目录仍有文件' in saved['inbox_cleanup']['error']
    assert saved['inbox_cleanup']['next_at'] >= work.clock.now + 300


def test_stale_top_level_done_is_recomputed(work):
    old = work.make()
    unit = old['work_handoffs'][0]
    work.cloud.nodes[unit['source']] = {'id': unit['id'], 'files': {}}
    old['inbox_cleanup'] = {'done': True, 'next_at': 0}
    work.store.save(old)
    assert old['id'] in [row['id'] for row in work.store.work_cleanup_jobs()]
    work.process(old)
    saved = work.store.get(old['id'])
    assert saved['inbox_cleanup']['done'] is False


def test_blocked_by_stable_since_and_cleared_after_release(work):
    old = work.make()
    old_unit = old['work_handoffs'][0]
    work.cloud.nodes[old_unit['source']] = {'id': old_unit['id'], 'files': {}}
    new = work.make(history=501, plan=700, state='moving', phase='prepared')
    work.clock.now = 1_800_000_000.0
    with pytest.raises(work.modules.domain.Awaiting):
        work.workhandoff.reconcile(new, work.config, work.cloud, work.store, Event())
    first = work.store.get(new['id'])['blocked_by']
    assert first['batch'] == old['id']
    assert first['reason'] == 'arrived_cleanup_pending'
    work.clock.now += 50
    with pytest.raises(work.modules.domain.Awaiting):
        work.workhandoff.reconcile(work.store.get(new['id']), work.config, work.cloud, work.store, Event())
    second = work.store.get(new['id'])['blocked_by']
    assert second['since'] == first['since']
    # A proven release clears the diagnostic and lets reconcile move on.
    record, holding = work.holding(work.store.get(old['id']), phase='holding', empty=True)
    work.process(work.store.get(old['id']))
    with pytest.raises(work.modules.domain.Awaiting) as caught:
        work.workhandoff.reconcile(work.store.get(new['id']), work.config, work.cloud, work.store, Event())
    assert '上一批' not in str(caught.value)
    assert 'blocked_by' not in work.store.get(new['id'])


def test_blocker_reasons_distinguish_phase_and_retirement(work):
    old = work.make()
    new = work.make(history=601, plan=800, state='moving', phase='prepared')
    unit = old['work_handoffs'][0]
    unit['phase'] = 'requested'
    work.store.save(old)
    assert work.store.work_blocker_details(new, unit['destination'])['reason'] == 'requested_move_pending'
    unit['phase'] = 'prepared'
    work.store.save(old)
    assert work.store.work_blocker_details(new, unit['destination'])['reason'] == 'prepared_earlier'
    unit['phase'] = 'arrived'
    unit['cleanup'] = {'source': unit['destination'], 'id': unit['id'], 'phase': 'recycling',
                       'holding': work.store.work_holding_path(old, unit, 'work0')}
    work.store.save(old)
    assert work.store.work_blocker_details(new, unit['destination'])['reason'] == 'retired_pending_recycle'
    with pytest.raises(work.modules.domain.Awaiting) as caught:
        work.workhandoff.reconcile(new, work.config, work.cloud, work.store, Event())
    assert '已移出 SA 输入目录' in str(caught.value)
    assert '尚未消费' not in str(caught.value)
    assert work.store.work_blocker_details(
        new, unit['destination'], cleanup_enabled=False)['reason'] == 'cleanup_disabled'


def test_release_does_not_merge_or_delete_fresh_target(work):
    old = work.make()
    record, holding = work.holding(old, phase='holding', empty=True)
    work.process(old)
    new = work.make(history=701, plan=900, state='moving', phase='prepared')
    unit = new['work_handoffs'][0]
    work.cloud.nodes[unit['destination']] = {'id': '999', 'files': {'new.mkv': 1}}
    with pytest.raises(work.modules.domain.Awaiting) as caught:
        work.workhandoff.reconcile(new, work.config, work.cloud, work.store, Event())
    assert '同名作品' in str(caught.value)
    assert work.cloud.nodes[unit['destination']]['id'] == '999'
    assert not work.cloud.moves and not work.cloud.deletes


def test_maintenance_move_jobs_are_fresh_ordered_and_actionable(work):
    requested = work.make(history=801, plan=100, state='moving', phase='requested')
    prepared = work.make(history=802, plan=200, state='waiting', phase='prepared')
    future = work.make(history=803, plan=300, state='moving', phase='prepared')
    handed = work.make(history=804, plan=400, state='handed_off', phase='arrived')
    cancelled = work.make(history=805, plan=500, state='cancelled', phase='prepared')
    deleting = work.make(history=806, plan=600, state='deleting', phase='prepared')
    review = work.make(history=807, plan=700, state='review', phase='prepared')
    review_future = work.make(history=808, plan=800, state='review', phase='prepared')
    no_move = work.make(history=809, plan=900, state='moving', phase='prepared', move_requested=False)
    for job in (requested, prepared, future, handed, cancelled, deleting, review, review_future, no_move):
        job = work.store.get(job['id'])
        job['next_check'] = 0
        work.store.save(job)
    future = work.store.get(future['id']); future['next_check'] = work.clock.now + 3600; work.store.save(future)
    review_future = work.store.get(review_future['id']); review_future['next_check'] = work.clock.now + 3600; work.store.save(review_future)
    rows = work.store.maintenance_move_jobs(limit=4)
    ids = [row['id'] for row in rows]
    assert ids[0] == requested['id']
    assert prepared['id'] in ids
    assert review['id'] in ids
    for absent in (future, handed, cancelled, deleting, review_future, no_move):
        assert absent['id'] not in ids
    assert len(rows) <= 4
    # Exclusion and fresh reload (not a shared mutable cache).
    rows = work.store.maintenance_move_jobs(limit=4, exclude=(requested['id'],))
    assert requested['id'] not in [row['id'] for row in rows]
    rows[0]['state'] = 'mutated'
    assert work.store.get(rows[0]['id'])['state'] != 'mutated'


def test_cooldown_and_stop_never_release_or_move(work):
    old = work.make()
    unit = old['work_handoffs'][0]
    work.cloud.nodes[unit['source']] = {'id': unit['id'], 'files': {}}
    cooldown = part('cooldown').CloudCooldown(work.clock.now + 600)
    work.host.gate_cloud.side_effect = cooldown
    work.process(old)
    saved = work.store.get(old['id'])
    assert 'released_directory_id' not in saved['work_handoffs'][0]['cleanup']
    assert saved['inbox_cleanup']['next_at'] >= work.clock.now + 600
    assert not work.cloud.moves and not work.cloud.deletes
    work.host.gate_cloud.side_effect = None
    work.host.gate_cloud.reset_mock()
    work.clock.now += 601
    stop = Event(); stop.set()
    with pytest.raises(work.modules.domain.Stopped):
        work.process(work.store.get(old['id']), stop=stop)
    assert not work.cloud.moves and not work.cloud.deletes
    assert 'released_directory_id' not in work.store.get(old['id'])['work_handoffs'][0]['cleanup']


def test_late_file_never_overwrites_replacement_generation(work):
    old = work.make()
    record, holding = work.holding(old, phase='holding', empty=True, flags=True)
    unit = old['work_handoffs'][0]
    work.process(old)
    work.cloud.nodes[holding]['files'] = {'late.ass': 1}
    work.cloud.nodes[unit['destination']] = {'id': '999', 'files': {'new.mkv': 1}}
    unit = work.store.get(old['id'])['work_handoffs'][0]
    former = unit['id']
    work.clock.now += 301
    work.process(work.store.get(old['id']))
    saved = work.store.get(old['id'])
    assert work.cloud.nodes[unit['destination']]['id'] == '999'
    assert work.cloud.nodes[unit['destination']]['files'] == {'new.mkv': 1}
    assert holding in work.cloud.nodes and work.cloud.nodes[holding]['files'] == {'late.ass': 1}
    assert not work.cloud.deletes
    assert saved['work_handoffs'][0]['cleanup']['released_directory_id'] == former


def test_multiple_queued_seasons_release_in_fifo_order(work):
    old = work.make()
    work.holding(old, phase='holding', empty=True)
    work.process(old)
    first = work.make(history=901, plan=1000, state='moving', phase='prepared')
    second = work.make(history=902, plan=1001, state='moving', phase='prepared')
    target = first['work_handoffs'][0]['destination']
    assert work.store.work_blocker(first, target) is None
    assert work.store.work_blocker(second, target) == first['id']
    saved_first = work.store.get(first['id'])
    saved_first['work_handoffs'][0]['phase'] = 'requested'
    work.store.save(saved_first)
    assert work.store.work_blocker(second, target) == first['id']
    assert work.store.work_blocker_details(second, target)['reason'] == 'requested_move_pending'


def test_cancelled_and_deleting_work_are_not_cleanup_candidates(work):
    cancelled = work.make(history=910, state='cancelled')
    deleting = work.make(history=911, state='deleting')
    unit = cancelled['work_handoffs'][0]
    work.cloud.nodes[unit['source']] = {'id': unit['id'], 'files': {}}
    ids = [row['id'] for row in work.store.work_cleanup_jobs()]
    assert cancelled['id'] not in ids and deleting['id'] not in ids
