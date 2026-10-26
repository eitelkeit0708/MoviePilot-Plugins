"""Old accepted scope is isolated, not erased or counted as delivered."""
from copy import deepcopy
import importlib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS

import pytest

from test_batches import batch


@pytest.fixture
def repair_case(batch, modules, monkeypatch):
    part = importlib.import_module('app.plugins.symediabatchbridge.scoperepair')
    wrong = Path(batch.config.local_root) / '电视剧/同名剧/Season 02/S02E01.ass'
    wrong.parent.mkdir(parents=True)
    wrong.write_bytes(b'other-season-subtitle')
    candidates = [{'local': p} for p in batch.paths] + [{'local': str(wrong)}]
    job = batch.job()
    batch.engine._sync_candidates(job, candidates)
    job = batch.job()
    ownership = importlib.import_module('app.plugins.symediabatchbridge.ownership')
    ownership.accept(job, candidates)
    entry = next(e for e in job['files'] if e['local'] == str(wrong))
    entry.update(uploaded=True, instant_requests=1, receipt={'fileid': '333', 'method': 'instant'})
    batch.store.save(job)
    # This suite tests migration/transport, independently of the matcher's tests.
    matcher = NS(partition=lambda rows: ([c for c in rows if c['local'] != str(wrong)],
                                        [c for c in rows if c['local'] == str(wrong)]))
    monkeypatch.setitem(__import__('sys').modules, 'app.plugins.symediabatchbridge.subtitle_scope', matcher)
    rows, parent = {}, '100'
    for n, name in enumerate(PurePosixPath(entry['relative']).parent.parts, 101):
        rows[parent] = [{'fid': str(n), 'pid': parent, 'fn': name, 'fc': '0'}]
        parent = str(n)
    rows[parent] = [{'fid': '333', 'pid': parent, 'fn': wrong.name, 'fc': '1',
                     'fs': entry['size'], 'sha1': entry['sha1']}]
    state = NS(part=part, batch=batch, job=job, rows=rows, parent=parent, wrong=wrong,
               candidates=candidates, folders={}, writes=[], lost=False, root_id='100', exists=True,
               collision=False)

    def request(method, endpoint, **kwargs):
        if method == 'GET':
            return {'state': True, 'data': deepcopy(rows.get(str(kwargs['params']['cid']), []))}
        assert endpoint == '/open/ufile/move'
        ident, target = str(kwargs['data']['file_ids']), str(kwargs['data']['to_cid'])
        durable = batch.job()['scope_repair']['items'][0]
        assert durable['state'] == 'moving' and durable['cloud']['id'] == ident
        assert durable['destination_id'] == target
        original = next(r for r in rows[parent] if r['fid'] == ident)
        rows[parent].remove(original)
        rows[target].append({**original, 'pid': target})
        state.writes.append((ident, target))
        if state.lost:
            state.lost = False
            raise modules.domain.Awaiting('lost response')
        return {'state': True}

    def folder(name, *, path):
        assert name == 'get_folder'
        path = path.as_posix()
        if path not in state.folders:
            target = str(500 + len(state.folders))
            state.folders[path] = target
            rows[target] = []
            if state.collision:
                rows[target].append({'fid': '999', 'pid': target, 'fn': wrong.name, 'fc': '1',
                                     'fs': entry['size'], 'sha1': entry['sha1']})
        return NS(type='dir', path=path, fileid=state.folders[path])

    batch.host._native_operations = lambda: NS(request=request, call=folder)
    batch.cloud.exists = lambda p: state.exists
    batch.cloud.directory_id = lambda p: state.root_id

    def run():
        current = batch.job()
        return part.repair(current, current['owned_candidates'], batch.config, batch.host,
                           batch.cloud, batch.store, batch.stop)
    state.run = run
    return state


def test_old_receipt_is_preserved_and_valid_scope_is_delivered_only_after_isolation(repair_case):
    c = repair_case
    old = deepcopy(c.job['files'][-1])
    result = c.run()
    after = c.batch.job()
    assert len(result) == 2 and len(after['files']) == 2
    assert after['scope_repair']['phase'] == 'complete'
    item = after['scope_repair']['items'][0]
    assert item['entry'] == old
    assert item['state'] == 'done' and len(c.writes) == 1
    assert c.wrong.read_bytes() == b'other-season-subtitle'
    assert not any(e['local'] == str(c.wrong) for e in after['owned_candidates'])
    assert item['destination'].startswith(c.batch.config.staging + '/_范围隔离/' + c.batch.id + '/')
    assert c.batch.config.inbox not in item['destination']


def test_lost_move_ack_restarts_without_upload_or_second_move(repair_case, modules):
    c = repair_case
    c.lost = True
    with pytest.raises(modules.domain.Awaiting):
        c.run()
    before = c.batch.job()
    assert len(before['files']) == 3 and before['scope_repair']['phase'] == 'isolating'
    c.batch.store = modules.store.Store(c.batch.store.path.parent)
    # The closure uses the same durable path; opening another Store simulates reload.
    c.run()
    assert len(c.writes) == 1 and not c.batch.host.uploads
    assert c.batch.job()['scope_repair']['phase'] == 'complete'


def test_unknown_native_receipt_absence_cannot_shrink_scope(repair_case, modules):
    c = repair_case
    c.rows[c.parent] = []
    with pytest.raises(modules.domain.Awaiting):
        c.run()
    assert len(c.batch.job()['files']) == 3 and not c.writes
    assert c.wrong.exists()


def test_cloud_hash_mismatch_blocks_isolation_and_keeps_receipt(repair_case, modules):
    c = repair_case
    c.rows[c.parent][0]['sha1'] = 'f' * 40
    with pytest.raises(modules.domain.BridgeError):
        c.run()
    assert len(c.batch.job()['files']) == 3 and not c.writes


def test_missing_unrelated_local_subtitle_uses_captured_cloud_proof_only(repair_case):
    c = repair_case
    old = deepcopy(c.job['files'][-1])
    c.wrong.unlink()
    c.run()
    item = c.batch.job()['scope_repair']['items'][0]
    assert item['entry'] == old and item['state'] == 'done'
    assert len(c.writes) == 1 and not c.wrong.exists()


def test_target_collision_never_overwrites(repair_case, modules):
    c = repair_case
    c.collision = True
    with pytest.raises(modules.domain.BridgeError):
        c.run()
    assert not c.writes and len(c.batch.job()['files']) == 3


def test_replaced_staging_root_cannot_resume_saved_file_ids(repair_case, modules):
    c = repair_case
    c.lost = True
    with pytest.raises(modules.domain.Awaiting):
        c.run()
    c.root_id = '999'
    with pytest.raises(modules.domain.Awaiting):
        c.run()
    assert len(c.writes) == 1 and len(c.batch.job()['files']) == 3


def test_never_uploaded_exclusion_keeps_local_without_creating_cloud_file(repair_case):
    c = repair_case
    job = c.batch.job()
    job['files'][-1].update(uploaded=False, instant_requests=0)
    job['files'][-1].pop('receipt')
    c.batch.store.save(job)
    c.rows[c.parent] = []
    c.run()
    assert c.batch.job()['scope_repair']['items'][0]['state'] == 'unuploaded'
    assert not c.writes and c.wrong.exists()


def test_stop_does_not_commit_or_touch_cloud(repair_case, modules):
    c = repair_case
    c.batch.stop.set()
    with pytest.raises(modules.domain.Stopped):
        c.run()
    assert not c.writes and 'scope_repair' not in c.batch.job()


def test_completed_migration_is_idempotent_and_does_not_duplicate_notifications(repair_case):
    c = repair_case
    c.run()
    count = c.batch.store.notice_counts(c.batch.id)
    c.run()
    assert len(c.writes) == 1 and c.batch.store.notice_counts(c.batch.id) == count


def test_real_migration_journal_displays_cloud_destination_not_local_candidate(repair_case, modules):
    c = repair_case
    c.run()
    info = modules.activity.scope_repair_info(c.batch.job())
    assert info['isolated'] == 1
    assert info['rows'][0]['original'] == str(c.wrong)
    assert info['rows'][0]['quarantine'] == c.batch.job()['scope_repair']['items'][0]['destination']
    assert info['rows'][0]['quarantine'] != str(c.wrong)
