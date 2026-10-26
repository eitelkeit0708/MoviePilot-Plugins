"""Recovery must retain receipts, never infer creation or resend unknown writes."""
from pathlib import Path, PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from clouddrive2_client.proto import clouddrive_pb2 as pb

from test_batches import batch
from test_date_generations import dated, prepare_date_cleanup
from test_jitter_handoff_cleanup import cleaner, clock, advance


def test_retried_move_records_recreated_parent_without_reupload(dated, clock, modules):
    b = dated
    move = b.cloud.move_directory
    b.cloud.move_directory = Mock(return_value=False)
    b.run()
    before = b.job()
    assert before['state'] == 'moving' and before['delivery_parent_id'] == '456'
    uploads = list(b.host.uploads)
    b.cloud.created_directories[b.parent] = '789'
    b.cloud.ensure_directory.return_value = '789'
    clock.now += 61
    # Reopen durable state, as after a restart; no reset of HASH/deadlines/receipts.
    b.engine.store = modules.store.Store(b.store.path.parent)
    b.run()
    after = b.job()
    assert after['destination'] == before['destination']
    assert after['delivery_parent_id'] == '789'
    assert b.store.owned_inbox_date(b.parent, '456') == '456'
    assert b.store.owned_inbox_date(b.parent, '789') == '789'
    assert after['files'] == before['files'] and b.host.uploads == uploads
    b.cloud.move_directory = move
    clock.now += 61
    b.run()
    assert b.job()['state'] == 'moving'  # Retry is reconciled on the following pass.
    b.run()
    assert b.job()['state'] == 'handed_off' and b.host.uploads == uploads


@pytest.mark.parametrize('receipt_kind', ['matching', 'different', 'absent', 'invalid', 'wrong_path'])
def test_create_ownership_uses_rpc_identity_not_a_later_path_lookup(modules, config_values, receipt_kind):
    config = modules.domain.Config.parse(config_values)
    path = config.inbox + '/2026-10-18'
    folder = pb.CloudDriveFile(id='123', name='2026-10-18', fullPathName=path, isDirectory=True)
    if receipt_kind == 'invalid':
        folder.id = 'local-placeholder'
    if receipt_kind == 'wrong_path':
        folder.fullPathName = config.inbox + '/unrelated'
    reply = pb.CreateFolderResult(result=pb.FileOperationResult(success=True))
    if receipt_kind != 'absent':
        reply.folderCreated.CopyFrom(folder)
    stub = NS(CreateFolder=Mock(return_value=reply))
    cloud = modules.cd2.CD2(config, Event(), stub=stub)
    cloud._lookup = Mock(side_effect=lambda p: NS(isDirectory=True, id='222') if p == config.inbox else None)
    cloud.directory_id = Mock(return_value='999' if receipt_kind == 'different' else '123')
    if receipt_kind == 'different':
        with pytest.raises(modules.domain.Awaiting):
            cloud.ensure_directory(path)
    else:
        assert cloud.ensure_directory(path) == '123'
    assert cloud.created_directories == ({path:'123'} if receipt_kind == 'matching' else {})
    assert stub.CreateFolder.call_count == 1


def retired_date(c):
    day = prepare_date_cleanup(c, '456')
    c.cloud.nodes[day] = {'id':'456', 'files':{}}
    c.run()
    job = advance(c)
    record = job['inbox_cleanup']['date']
    assert record['phase'] == 'claiming' and day not in c.cloud.nodes
    return day, record['holding']


def test_retired_date_finishes_recycling_with_new_generation_present(cleaner, modules):
    c = cleaner
    day, holding = retired_date(c)
    c.cloud.nodes[day] = {'id':'789', 'files':{'new.mkv':{'size':99}}}
    c.batch.store = modules.store.Store(c.batch.store.path.parent)
    for _ in range(4):
        saved = advance(c)
    assert saved['inbox_cleanup']['done']
    assert c.cloud.deletes == [(holding, '456')]
    assert c.cloud.moves == [(day, str(PurePosixPath(holding).parent))]
    assert c.cloud.nodes[day] == {'id':'789', 'files':{'new.mkv':{'size':99}}}


def test_late_retired_files_never_merge_into_replacement_date(cleaner):
    c = cleaner
    day, holding = retired_date(c)
    c.cloud.nodes[holding]['files'] = {'late.ass':{'size':1}}
    c.cloud.nodes[day] = {'id':'789', 'files':{'new.mkv':{'size':99}}}
    before = list(c.cloud.moves)
    for _ in range(3):
        saved = advance(c)
    assert '两侧均保留' in saved['inbox_cleanup']['error']
    assert not c.cloud.deletes and c.cloud.moves == before
    assert c.cloud.nodes[holding]['files'] == {'late.ass':{'size':1}}
    assert c.cloud.nodes[day]['files'] == {'new.mkv':{'size':99}}


@pytest.mark.parametrize('new_id', ['', '456'])
def test_unconfirmed_or_stale_two_sided_view_never_recycles(cleaner, new_id):
    c = cleaner
    day, holding = retired_date(c)
    c.cloud.nodes[day] = {'id':new_id, 'files':{}}
    for _ in range(3):
        saved = advance(c)
    assert not saved['inbox_cleanup'].get('done') and not c.cloud.deletes
    assert holding in c.cloud.nodes


def normal_pending(b, clock):
    job = b.job()
    b.engine._sync_candidates(job, b.host.collect(job))
    job = b.job()
    entry = job['files'][0]
    entry.update(instant_misses=24, instant_started_at=clock.now-25*3600,
                 instant_requests=24, instant_next_at=clock.now-1,
                 normal_started_at=clock.now-3600, normal_requests=1)
    b.store.save(job)
    return job, entry


def try_normal(b, job, entry):
    return b.engine._upload_ready(job, entry, b.config.staging+'/'+job['id'], {}, True)


def test_unknown_normal_upload_with_native_file_waits_without_duplicate(batch, clock):
    b = batch
    job, entry = normal_pending(b, clock)
    b.host.staged_file_present = Mock(return_value=True)
    assert try_normal(b, job, entry) == (1, True)
    for _ in range(2):
        assert try_normal(b, job, entry) == (0, True)
    assert b.host.staged_file_present.call_count == 1
    clock.now += 300
    assert try_normal(b, job, entry) == (1, True)
    assert b.host.uploads == [] and entry['normal_requests'] == 1 and entry['instant_misses'] == 24
    # A later exact fresh CD2 record recovers directly, without more native writes.
    previous = {entry['relative']:{'size':entry['size'], 'sha1':entry['sha1'], 'id':'123'}}
    assert b.engine._upload_ready(job, entry, b.config.staging+'/'+job['id'], previous, True) == (0, False)
    assert entry['uploaded'] and b.host.uploads == []


def test_normal_retry_requires_three_spaced_absences_across_restart(batch, clock, modules):
    b = batch
    job, entry = normal_pending(b, clock)
    b.host.staged_file_present = Mock(return_value=False)
    for _ in range(2):
        assert try_normal(b, job, entry) == (1, True)
        assert try_normal(b, job, entry) == (0, True)
        clock.now += 300
        b.engine.store = modules.store.Store(b.store.path.parent)
        job = b.job()
        entry = job['files'][0]
    assert try_normal(b, job, entry) == (1, False)
    assert entry['normal_requests'] == 2 and entry['uploaded']
    assert len(b.host.uploads) == 1 and b.host.staged_file_present.call_count == 3
    assert entry['instant_next_at'] == clock.now + 3600
    assert 'normal_missing_observations' not in entry


def test_native_failure_resets_absence_evidence_without_consuming_budget(batch, clock, modules):
    b = batch
    job, entry = normal_pending(b, clock)
    b.host.staged_file_present = Mock(return_value=False)
    try_normal(b, job, entry)
    clock.now += 300
    b.host.staged_file_present.side_effect = modules.domain.Awaiting('network unknown')
    with pytest.raises(modules.domain.Awaiting):
        try_normal(b, job, entry)
    assert 'normal_missing_observations' not in b.job()['files'][0]
    assert entry['instant_misses'] == 24 and entry['normal_requests'] == 1 and b.host.uploads == []


def test_new_failed_normal_request_reserves_hour_before_retry(batch, clock, modules):
    b = batch
    job, entry = normal_pending(b, clock)
    entry['normal_requests'] = 0
    b.host.upload = Mock(return_value=None)
    with pytest.raises(modules.domain.BridgeError, match='上传未确认完成'):
        try_normal(b, job, entry)
    saved = b.job()['files'][0]
    assert saved['normal_last_requested_at'] == clock.now
    assert saved['instant_next_at'] == clock.now + 3600 and saved['normal_requests'] == 1
    assert try_normal(b, job, entry) == (0, False) and b.host.upload.call_count == 1


@pytest.mark.parametrize('result', [None, 0, 'missing'])
def test_ambiguous_native_result_is_never_an_absence(batch, clock, modules, result):
    b = batch
    job, entry = normal_pending(b, clock)
    entry['normal_missing_observations'] = {'count':2, 'at':clock.now-600}
    b.host.staged_file_present = Mock(return_value=result)
    with pytest.raises(modules.domain.Awaiting):
        try_normal(b, job, entry)
    assert 'normal_missing_observations' not in b.job()['files'][0]
    assert b.host.uploads == [] and entry['normal_requests'] == 1


def test_unexpected_native_error_does_not_leave_partial_absence_proof(batch, clock):
    b = batch
    job, entry = normal_pending(b, clock)
    entry['normal_missing_observations'] = {'count':2, 'at':clock.now-600}
    b.host.staged_file_present = Mock(side_effect=RuntimeError('unknown response'))
    with pytest.raises(RuntimeError):
        try_normal(b, job, entry)
    assert 'normal_missing_observations' not in b.job()['files'][0]
    assert b.host.uploads == [] and entry['normal_requests'] == 1


def test_native_reconciliation_uses_the_same_bounded_round_budget(batch, clock, modules):
    b = batch
    job, first = normal_pending(b, clock)
    for n in range(8):
        p = Path(b.config.local_root) / ('extra'+str(n)+'.mkv')
        p.write_bytes(b'video')
        b.paths.append(str(p))
    b.engine._sync_candidates(job, b.host.collect(job), hash_limit=20)
    for entry in job['files']:
        entry.update(instant_misses=24, instant_started_at=clock.now-25*3600,
                     normal_requests=1, normal_started_at=clock.now-3600,
                     instant_next_at=clock.now-1)
    b.store.save(job)
    b.host.staged_file_present = Mock(return_value=True)
    b.run()
    assert b.host.staged_file_present.call_count == 4 and b.host.uploads == []
    b.run()  # The first four are throttled, so the next four can be checked.
    assert b.host.staged_file_present.call_count == 8 and b.host.uploads == []
