"""Physical 115 duplicates, unstable CD2 aliases, durable isolation and upgrades."""
from copy import deepcopy
from pathlib import PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
import importlib

import pytest

from test_batches import batch


@pytest.fixture
def isolation(batch, modules):
    part = importlib.import_module('app.plugins.symediabatchbridge.duplicates')
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    job = batch.job()
    for index, entry in enumerate(job['files']):
        entry.update(uploaded=True, instant_requests=1, instant_misses=7,
                     instant_next_at=1_900_000_000, receipt={'fileid':str(203 + index),
                     'method':'instant', 'size':entry['size']})
    batch.store.save(job)
    first, second = job['files']
    leaf = str(PurePosixPath(first['relative']).parent)
    rows = {}
    parent = '100'
    for index, name in enumerate(PurePosixPath(leaf).parts, 101):
        rows[parent] = [{'fid':str(index), 'pid':parent, 'fn':name, 'fc':'0'}]
        parent = str(index)
    def file(ident, entry):
        return {'fid':str(ident), 'pid':parent, 'fn':PurePosixPath(entry['relative']).name,
                'fc':'1', 'fs':entry['size'], 'sha1':entry['sha1']}
    rows[parent] = [file(i, first) for i in (201,202,203)] + [file(204, second)]
    targets, writes = {}, []
    fake = NS(part=part, rows=rows, parent=parent, writes=writes, fail_after_move=None,
              fail_before_move=None, aliases=0, batch=batch, job=job)

    def request(method, endpoint, **kwargs):
        if method == 'GET':
            cid = str(kwargs['params']['cid'])
            return {'state':True,'code':0,'data':deepcopy(rows[cid])}
        assert endpoint == '/open/ufile/move'
        fid, target = str(kwargs['data']['file_ids']), str(kwargs['data']['to_cid'])
        journal = batch.job()['duplicate_isolation']
        intent = next(item for item in journal['items'] if item['id']==fid)
        assert intent['state']=='moving' and intent['destination_id']==target
        if fake.fail_before_move:
            raise fake.fail_before_move
        row = next(row for row in rows[parent] if row['fid']==fid)
        rows[parent].remove(row)
        rows[target].append({**row,'pid':target})
        writes.append((fid,target))
        if fake.fail_after_move:
            error, fake.fail_after_move = fake.fail_after_move, None
            raise error
        return {'state':True,'code':0}

    def folder(path):
        if path not in targets:
            fid = str(500+len(targets))
            targets[path] = fid
            rows[fid] = []
        return targets[path]

    gateway = part.Isolation(request, folder, batch.config, batch.stop)
    native_tree = batch.cloud.tree
    def listing(path):
        source = batch.config.cd2_staging+'/'+batch.id
        if path == source:
            for full in list(batch.cloud.files):
                if full.startswith(source+'/'):
                    batch.cloud.files.pop(full)
            grouped = {}
            for row in rows[parent]:
                grouped.setdefault(row['fn'],[]).append(row)
            fake.aliases += 1
            for name, members in grouped.items():
                if fake.aliases % 2:
                    members = list(reversed(members))
                for n,row in enumerate(members):
                    base = PurePosixPath(name)
                    alias = name if n==0 else base.stem+f'({n})'+base.suffix
                    batch.cloud.files[source+'/'+leaf+'/'+alias] = {
                        'id':row['fid'],'size':row['fs'],'sha1':row['sha1']}
        return native_tree(path)
    batch.cloud.tree = listing
    batch.cloud.directory_id = lambda path:'100'
    batch.cloud.files[batch.config.cd2_staging+'/'+batch.id+'/placeholder'] = {}
    batch.host.isolate_duplicates = lambda j, ident, remote, allowed, store: gateway.recover(j,ident,remote,allowed,store)
    fake.gateway, fake.file = gateway, file
    return fake


def test_identical_duplicates_keep_receipt_identity_and_resume_handoff(isolation):
    b = isolation.batch
    before = deepcopy(b.job()['files'])
    b.run()
    assert b.job()['state']=='handed_off', b.job()['message']
    assert {fid for fid,target in isolation.writes}=={'201','202'}
    assert len(b.cloud.moves)==1 and b.host.uploads==[]
    for old,new in zip(before,b.job()['files']):
        for key in ('sha1','receipt','instant_requests','instant_misses','instant_next_at'):
            assert new[key]==old[key]
    assert all(item['state']=='done' for item in b.job()['duplicate_isolation']['items'])
    assert all(len(rows)==1 for key,rows in isolation.rows.items() if int(key)>=500)


def test_final_native_manifest_check_catches_duplicates_hidden_by_cd2(isolation):
    b=isolation.batch
    original_tree=b.cloud.tree
    hidden=[True]
    def listing(path):
        result=original_tree(path)
        if hidden[0]:
            return {entry['relative']:next(item for item in result.values() if item['id']==entry['receipt']['fileid'])
                    for entry in b.job()['files']}
        return result
    b.cloud.tree=listing
    b.run()
    assert b.job()['state']=='waiting' and not b.cloud.moves and not isolation.writes
    hidden[0]=False
    b.run()
    assert b.job()['state']=='handed_off' and len(isolation.writes)==2


@pytest.mark.parametrize('error_kind', ['response_lost','stop'])
def test_lost_move_receipt_or_restart_does_not_move_twice(isolation,modules,error_kind):
    b = isolation.batch
    isolation.fail_after_move = (modules.domain.Stopped() if error_kind=='stop' else
                                modules.domain.BridgeError('网络响应丢失'))
    b.run()
    assert len(isolation.writes)==1 and b.cloud.moves==[]
    assert b.job()['duplicate_isolation']['items'][0]['state']=='moving'
    b.store = modules.store.Store(b.store.path.parent)
    b.engine.store = b.store
    b.run()
    assert b.job()['state']=='handed_off',b.job()['message']
    assert len(isolation.writes)==2 and b.host.uploads==[]


def test_network_outage_keeps_plan_and_retries_without_upload(isolation,modules):
    isolation.fail_before_move = modules.domain.BridgeError('网络暂不可用')
    b = isolation.batch
    b.run()
    assert b.job()['duplicate_isolation']['items'][0]['state']=='moving'
    assert not isolation.writes and not b.host.uploads
    isolation.fail_before_move=None
    b.run()
    assert b.job()['state']=='handed_off'
    assert len(isolation.writes)==2


@pytest.mark.parametrize('damage', ['different_hash','unknown_file','wrong_parent','missing_hash','foreign_receipt'])
def test_uncertain_or_different_files_are_not_isolated(isolation,damage):
    b = isolation.batch
    row = isolation.rows[isolation.parent][0]
    if damage=='different_hash':row['sha1']='f'*40
    elif damage=='missing_hash':row['sha1']=''
    elif damage=='unknown_file':row['fn']='another-media.mkv'
    elif damage=='wrong_parent':row['pid']='999'
    else:
        j=b.job();j['files'][0]['receipt']['fileid']='999';b.store.save(j)
    b.run()
    assert b.job()['state']=='review',b.job()['message']
    assert not isolation.writes and not b.cloud.moves and not b.host.uploads


def test_lost_upload_receipt_recovers_known_hash_without_reinit(isolation):
    b=isolation.batch
    j=b.job()
    j['files'][0].update(uploaded=False,instant_requests=1)
    j['files'][0].pop('receipt')
    b.store.save(j)
    b.run()
    assert b.job()['state']=='handed_off',b.job()['message']
    assert b.job()['files'][0]['receipt']['method']=='verified'
    assert b.job()['files'][0]['receipt']['fileid']=='201'
    assert not b.host.uploads


def test_isolation_has_finite_write_budget_and_resumes(isolation):
    b=isolation.batch
    entry=b.job()['files'][0]
    isolation.rows[isolation.parent].extend(isolation.file(i,entry) for i in range(210,214))
    b.run()
    assert len(isolation.writes)==4 and b.job()['state']=='isolating'
    assert not b.cloud.moves
    b.run()
    assert len(isolation.writes)==6 and b.job()['state']=='handed_off'


def test_already_moving_batch_does_not_start_isolation(isolation):
    b=isolation.batch
    j=b.job();j.update(move_requested=True,source=b.config.cd2_staging+'/'+b.id,
                       destination=b.config.inbox+'/'+b.id,source_directory_id='100')
    b.store.save(j)
    b.run()
    assert not isolation.writes and not b.host.uploads and not b.cloud.moves
    assert b.job()['state']=='review'


def test_completed_isolation_id_cannot_loop_back_into_source(isolation):
    b=isolation.batch
    b.run()
    j=b.job();j.update(state='review',move_requested=False)
    b.store.save(j)
    item=j['duplicate_isolation']['items'][0]
    target=isolation.rows[item['destination_id']]
    row=target.pop();row['pid']=isolation.parent
    isolation.rows[isolation.parent].append(row)
    b.cloud.moves.clear()
    b.run()
    assert b.job()['state']=='review'
    assert len(isolation.writes)==2


def test_isolation_detects_root_identity_change_after_restart(isolation,modules):
    b=isolation.batch
    isolation.fail_after_move=modules.domain.Stopped()
    b.run()
    b.cloud.directory_id=lambda path:'999'
    b.run()
    assert b.job()['state']=='review' and len(isolation.writes)==1


def test_eventual_source_and_target_views_wait_without_replaying_move(isolation,modules):
    b=isolation.batch
    isolation.fail_after_move=modules.domain.Stopped()
    b.run()
    item=b.job()['duplicate_isolation']['items'][0]
    stale=deepcopy(isolation.rows[item['destination_id']][0])
    stale['pid']=isolation.parent
    isolation.rows[isolation.parent].append(stale)
    b.run()
    assert b.job()['state']=='isolating' and len(isolation.writes)==1
    isolation.rows[isolation.parent].remove(stale)
    b.run()
    assert b.job()['state']=='handed_off' and len(isolation.writes)==2


def test_shared_cooldown_keeps_isolation_intent_and_upload_budget(isolation,modules):
    b=isolation.batch
    error=modules.engine.CloudCooldown(modules.engine.time.time()+7200)
    isolation.fail_before_move=error
    b.run()
    assert b.job()['state']=='isolating' and b.job()['next_check']==error.next_at
    assert all(entry['instant_misses']==7 for entry in b.job()['files'])
    assert not isolation.writes and not b.host.uploads


def test_legacy_manual_isolation_records_are_not_rewritten(isolation):
    b=isolation.batch
    # A prior repair has already left the single original, without a new journal.
    isolation.rows[isolation.parent]=[row for row in isolation.rows[isolation.parent] if row['fid'] in ('203','204')]
    legacy={'completed':[{'id':'201','destination':'/previous/isolation/201'}],'isolation':'/previous/isolation'}
    job=b.job();job['cloud_duplicate_isolation']=deepcopy(legacy);b.store.save(job)
    b.run()
    assert b.job()['state']=='handed_off' and b.job()['cloud_duplicate_isolation']==legacy
    assert not isolation.writes


def test_old_review_is_automatically_queued_once_without_resetting_files(isolation):
    b=isolation.batch
    job=b.job();job.update(state='review',message='云端清单尚未一致：缺少 0 个，多出 2 个',next_check=2_000_000_000)
    b.store.save(job)
    original=deepcopy(b.job()['files'])
    b.store.queue_duplicate_reviews()
    assert b.job()['next_check']==0 and b.job()['files']==original
    requested=b.job()['retry_requested']
    b.store.queue_duplicate_reviews()
    assert b.job()['retry_requested']==requested
    b.run()
    assert b.job()['state']=='handed_off' and b.job()['files']==original


def test_upgrade_does_not_queue_move_intents_or_foreign_errors(isolation):
    b=isolation.batch
    j=b.job();j.update(state='review',message='云端清单尚未一致',move_requested=True,next_check=2_000_000_000)
    b.store.save(j)
    b.store.queue_duplicate_reviews()
    assert b.job()['next_check']==2_000_000_000 and not b.job().get('retry_requested')
