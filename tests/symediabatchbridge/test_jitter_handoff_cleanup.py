"""Durable jitter windows, immutable destinations and empty-only retirement."""
import importlib
import time
from dataclasses import replace
from pathlib import PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_batches import batch
from test_cloud_cooldown import api, invoke, response


def part(name):
    return importlib.import_module('app.plugins.symediabatchbridge.' + name)


@pytest.fixture
def clock(monkeypatch):
    value = NS(now=1_800_000_000.0)
    monkeypatch.setattr(time, 'time', lambda: value.now)
    return value


def test_network_pause_is_shared_persistent_and_exponential(api, modules):
    api.provider.session.request.return_value = response(503)
    with pytest.raises(api.cooldown.CloudRequestError) as failure:
        invoke(api)
    assert failure.value.next_at == api.clock.now + 60
    other = api.cooldown.Cooldown(modules.store.Store(api.store.path.parent))
    with pytest.raises(api.cooldown.TransientPause):
        other.before()
    assert other.transient()['until'] == api.clock.now + 60
    api.clock.now += 61
    assert other.transient()['until'] == api.clock.now + 120
    api.clock.now += 121
    assert other.transient()['until'] == api.clock.now + 240
    api.clock.now += 10000
    assert other.transient()['until'] == api.clock.now + 60
    assert api.provider.session.request.call_count == 1


def test_rate_cooldown_wins_over_transient_pause(api):
    api.guard.transient()
    api.guard.defer(api.clock.now + 3600)
    with pytest.raises(api.cooldown.CloudCooldown) as caught:
        api.guard.before()
    assert caught.value.kind == 'rate_limit' and caught.value.next_at == api.clock.now + 3600


def test_cd2_transient_error_stops_native_requests(api, modules):
    config = NS(cd2_address='http://cd2:19798',cd2_token='secret',cd2_prefix='/115')
    cloud = modules.cd2.CD2(config, Event(),stub=NS(GetSubFiles=Mock()))
    class Unavailable(cloud.grpc.RpcError):
        def code(self): return cloud.grpc.StatusCode.UNAVAILABLE
        def details(self): return 'connection unavailable'
    cloud.stub.GetSubFiles.side_effect=Unavailable()
    cloud.guard=api.guard
    with pytest.raises(api.cooldown.TransientPause):
        cloud._children('/115')
    with pytest.raises(api.cooldown.TransientPause):
        invoke(api)
    api.provider.session.request.assert_not_called()


def test_cd2_explicit_limit_keeps_hourly_cooldown(api,modules):
    cloud=modules.cd2.CD2(NS(cd2_prefix='/115',cd2_token='secret'),Event(),stub=NS(GetSubFiles=Mock()))
    class Limited(cloud.grpc.RpcError):
        def code(self): return cloud.grpc.StatusCode.RESOURCE_EXHAUSTED
        def details(self): return 'HTTP 429 SECRET'
    cloud.stub.GetSubFiles.side_effect=Limited();cloud.guard=api.guard
    with pytest.raises(api.cooldown.CloudCooldown) as caught:
        cloud._children('/115')
    assert caught.value.kind=='rate_limit' and caught.value.next_at==api.clock.now+3600
    assert 'SECRET' not in str(api.store.meta('u115_cloud_cooldown'))


def test_native_receipt_prevents_false_cd2_missing_reupload(batch, clock):
    job=batch.job()
    batch.engine._sync_candidates(job,batch.host.collect(job))
    entry=job['files'][0]
    entry.update(uploaded=True,receipt={'fileid':'123','method':'instant'},remote_missing_at=clock.now-61)
    batch.host.staged_file_present=Mock(return_value=True)
    assert batch.engine._upload_ready(job,entry,'/MP暂存/'+job['id'],{},True)==(0,True)
    assert entry['uploaded'] and entry['receipt']['fileid']=='123'
    assert batch.host.uploads==[]


def test_repair_requires_three_native_absences_separated_by_five_minutes(batch, clock, modules):
    job=batch.job()
    batch.engine._sync_candidates(job,batch.host.collect(job))
    entry=job['files'][0]
    entry.update(uploaded=True,receipt={'fileid':'123'},remote_missing_at=clock.now-61)
    batch.host.staged_file_present=Mock(return_value=False)
    for offset in (0,1,299,300,301,599):
        with_time=1_800_000_000+offset
        clock.now=with_time
        batch.engine._upload_ready(job,entry,'/MP暂存/'+job['id'],{},False)
        assert entry['uploaded']
    # A failed listing cannot count as another absence, and resets the evidence.
    batch.host.staged_file_present.side_effect=modules.domain.Awaiting('unavailable')
    clock.now+=1
    with pytest.raises(modules.domain.Awaiting):
        batch.engine._upload_ready(job,entry,'/MP暂存/'+job['id'],{},False)
    assert 'native_missing_observations' not in entry
    batch.host.staged_file_present.side_effect=None
    for offset in (0,300):
        clock.now+=offset
        batch.engine._upload_ready(job,entry,'/MP暂存/'+job['id'],{},False)
        assert entry['uploaded']
    clock.now+=300
    batch.engine._upload_ready(job,entry,'/MP暂存/'+job['id'],{},False)
    assert not entry['uploaded'] and batch.host.uploads==[]


def test_date_destination_is_pinned_and_legacy_flat_is_preserved(batch, modules, monkeypatch):
    handoff=part('handoff')
    job=batch.job()
    assert handoff.destination(job,batch.config,batch.store)[0]==batch.config.inbox+'/'+job['id']
    job['handoff_layout']='date'
    target,parent=handoff.destination(job,batch.config,batch.store)
    saved=modules.store.Store(batch.store.path.parent).get(job['id'])
    assert saved['destination']==target and PurePosixPath(parent).parent.as_posix()==batch.config.inbox
    class Midnight:
        @staticmethod
        def now(*args): raise AssertionError('A persisted destination must not consult a new date')
    monkeypatch.setattr(handoff,'datetime',Midnight)
    assert handoff.destination(saved,batch.config,batch.store)==(target,parent)
    saved['destination']='/115/foreign/'+job['id']
    with pytest.raises(modules.domain.BridgeError):
        handoff.destination(saved,batch.config,batch.store)


def test_move_ack_without_arrival_is_not_a_handoff(batch, clock):
    batch.cloud.move_directory=Mock(return_value=True)
    batch.run()
    assert batch.job()['state']=='moving' and batch.job()['move_requested']
    assert batch.cloud.move_directory.call_count==1
    batch.run()
    assert batch.cloud.move_directory.call_count==1
    clock.now+=61
    batch.run()
    assert batch.cloud.move_directory.call_count==2 and batch.job()['state']=='moving'


@pytest.fixture
def cleaner(batch,clock):
    config=replace(batch.config,cleanup_inbox=True,inbox_retention_days=7)
    job=batch.job()
    job.update(state='handed_off',handed_off_at=clock.now-8*86400,
               source_directory_id='123',destination=config.inbox+'/'+job['id'])
    batch.store.save(job)
    nodes={job['destination']:{'id':'123','files':{}}}
    cloud=NS(moves=[],deletes=[],nodes=nodes)
    cloud.exists=lambda p:p in nodes
    cloud.directory_id=lambda p:nodes[p]['id']
    cloud.tree=lambda p:nodes[p]['files']
    cloud._children=lambda p:[x for x in nodes if str(PurePosixPath(x).parent)==p]
    def ensure(p): nodes.setdefault(p,{'id':str(1000+len(nodes)),'files':{}});return nodes[p]['id']
    cloud.ensure_directory=ensure
    cloud.lost=False
    def move(source,parent):
        target=parent+'/'+PurePosixPath(source).name
        if target in nodes: return False
        intent=batch.store.get(job['id'])['inbox_cleanup']
        assert any(intent.get(k,{}).get('phase') in ('claiming','restoring') for k in ('batch','date'))
        cloud.moves.append((source,parent))
        nodes[target]=nodes.pop(source)
        if cloud.lost:
            cloud.lost=False
            raise part('domain').Awaiting('lost move response')
        return True
    cloud.move_directory=move
    host=NS(gate_cloud=Mock(),empty_directory=Mock(side_effect=lambda p,i:not nodes[p]['files']))
    def delete(p,ident,bid):
        assert nodes[p]['id']==ident and not nodes[p]['files']
        assert any(batch.store.get(bid)['inbox_cleanup'].get(k,{}).get('phase')=='recycling'
                   for k in ('batch','date'))
        assert p.startswith(config.cd2_staging+'/_空目录清理/'+bid+'/')
        cloud.deletes.append((p,ident));del nodes[p]
    host.delete_retired_empty=delete
    def run():
        job=batch.store.get(batch.id)
        part('housekeeping').process(job,config,host,cloud,batch.store,Event())
        return batch.store.get(batch.id)
    return NS(batch=batch,clock=clock,config=config,job=job,cloud=cloud,host=host,run=run)


def advance(cleaner):
    cleaner.clock.now+=3601
    return cleaner.run()


def test_cleanup_observes_retires_then_recycles_only_after_an_hour(cleaner):
    c=cleaner
    original_notices=c.batch.store.notice_counts(c.batch.id)
    assert c.run()['inbox_cleanup']['batch'].get('empty_since')
    assert c.cloud.moves==c.cloud.deletes==[]
    advance(c)  # second empty observation; durable claim before moving
    assert len(c.cloud.moves)==1 and not c.cloud.deletes
    advance(c)  # destination identity confirmed, start isolated holding hour
    assert not c.cloud.deletes
    advance(c)  # recycle only in the isolated holding path
    assert len(c.cloud.deletes)==1
    advance(c); saved=advance(c)
    assert saved['inbox_cleanup']['done']
    assert saved['state']=='handed_off' and c.batch.store.notice_counts(c.batch.id)==original_notices


@pytest.mark.parametrize('kind',['media','subtitle','other'])
def test_cleanup_preserves_every_nonempty_tree(cleaner,kind):
    c=cleaner
    c.cloud.nodes[c.job['destination']]['files']={'x.'+kind:{'size':1}}
    for _ in range(8):
        c.clock.now+=7*3600;c.run()
    assert c.cloud.moves==c.cloud.deletes==[]
    assert c.batch.store.board(status='attention')['total']==1


def test_cleanup_never_uses_an_empty_cd2_cache_as_delete_evidence(cleaner):
    c=cleaner;c.host.empty_directory.return_value=False;c.host.empty_directory.side_effect=None
    for _ in range(4):c.clock.now+=7*3600;c.run()
    assert c.cloud.moves==c.cloud.deletes==[]


def test_cleanup_lost_move_receipt_recovers_by_id_and_retains_late_files(cleaner,modules):
    c=cleaner;c.run();c.cloud.lost=True
    saved=advance(c)
    record=saved['inbox_cleanup']['batch'];holding=record['holding']
    assert record['phase']=='claiming' and holding in c.cloud.nodes
    # Reload the actual ledger, then encounter a late subtitle in retired storage.
    c.batch.store=modules.store.Store(c.batch.store.path.parent)
    c.cloud.nodes[holding]['files']={'late.ass':{'size':100}}
    saved=advance(c)
    assert saved['inbox_cleanup']['batch']['phase']=='restoring'
    assert c.cloud.nodes[c.job['destination']]['files']['late.ass']
    advance(c)
    assert not c.cloud.deletes and len(c.cloud.moves)==2


def test_cleanup_recovers_a_missing_holding_parent_before_retrying_the_saved_move(cleaner):
    c=cleaner;c.run()
    original=c.cloud.move_directory
    c.cloud.move_directory=Mock(side_effect=part('domain').Awaiting('not sent'))
    job=advance(c);record=job['inbox_cleanup']['batch']
    assert record['phase']=='claiming'
    parent=str(PurePosixPath(record['holding']).parent)
    del c.cloud.nodes[parent]
    def move(source,target):
        assert target in c.cloud.nodes
        return original(source,target)
    c.cloud.move_directory=move
    advance(c)
    assert c.cloud.nodes[record['holding']]['id']==record['id']
    assert not c.cloud.deletes


def test_cleanup_id_replacement_and_old_jobs_without_id_are_preserved(cleaner):
    c=cleaner;c.cloud.nodes[c.job['destination']]['id']='999'
    assert '替换' in c.run()['inbox_cleanup']['error']
    assert not c.cloud.moves
    job=c.batch.job();job.pop('source_directory_id');c.batch.store.save(job)
    saved=advance(c)
    assert saved['inbox_cleanup']['done'] and not c.cloud.deletes


def test_cleanup_ignores_young_batches_and_respects_shared_pause(cleaner,modules):
    c=cleaner;job=c.batch.job();job['handed_off_at']=c.clock.now;c.batch.store.save(job)
    assert not c.batch.store.housekeeping_jobs(7)
    assert 'inbox_cleanup' not in c.run()
    job['handed_off_at']-=8*86400;c.batch.store.save(job)
    c.host.gate_cloud.side_effect=part('cooldown').TransientPause(c.clock.now+600)
    assert c.run()['inbox_cleanup']['next_at']>=c.clock.now+600
    assert not c.cloud.moves and not c.cloud.deletes


def test_cleanup_rejects_old_staging_reused_as_a_new_sa_inbox(cleaner):
    c=cleaner;job=c.batch.job()
    other={**c.config.routing(),'inbox':c.config.cd2_staging+'/new-sa-input'}
    part('housekeeping').process(job,c.config,c.host,c.cloud,c.batch.store,Event(),protected_routings=[other])
    assert '重叠' in c.batch.job()['inbox_cleanup']['error']
    assert not c.cloud.moves and not c.cloud.deletes and not c.host.gate_cloud.called


def test_date_directory_is_owned_but_never_retired_while_it_has_children(cleaner):
    c=cleaner;job=c.batch.job();day=c.config.inbox+'/2026-10-01'
    job['destination']=day+'/'+job['id'];job['handoff_layout']='date'
    c.cloud.nodes[job['destination']]=c.cloud.nodes.pop(c.job['destination'])
    c.cloud.nodes[day]={'id':'456','files':{}}
    c.cloud.nodes[day+'/foreign']={'id':'999','files':{}}
    c.batch.store.remember_inbox_date(day,'456')
    c.batch.store.save(job)
    for _ in range(8):advance(c)
    assert day in c.cloud.nodes
    assert all(source!=day for source,parent in c.cloud.moves)
    assert c.cloud.nodes[day+'/foreign']['id']=='999'


def test_owned_empty_date_is_retired_only_after_the_batch_is_recycled(cleaner):
    c=cleaner;job=c.batch.job();day=c.config.inbox+'/2026-10-01'
    job['destination']=day+'/'+job['id'];job['handoff_layout']='date'
    c.cloud.nodes[job['destination']]=c.cloud.nodes.pop(c.job['destination'])
    c.cloud.nodes[day]={'id':'456','files':{}}
    c.batch.store.remember_inbox_date(day,'456');c.batch.store.save(job)
    for _ in range(12):saved=advance(c)
    assert day not in c.cloud.nodes and saved['inbox_cleanup']['done']
    assert len(c.cloud.deletes)==2 and c.cloud.moves[1][0]==day


def test_notification_grace_does_not_suppress_persistent_or_auth_failure(batch,clock):
    job=batch.job();job['state']='waiting'
    job['files']=[{'instant_error_since':clock.now,'instant_error_kind':'network'}]
    assert part('activity').notification_grace(job)
    clock.now+=301
    assert not part('activity').notification_grace(job)
    job['files'][0].update(instant_error_since=clock.now,instant_error_kind='auth')
    assert not part('activity').notification_grace(job)
    job['files'][0]['instant_error_kind']='network';job['attempts']=1
    assert not part('activity').notification_grace(job)


def test_native_receipt_reads_existing_parent_without_creating_or_uploading(batch,modules):
    job=batch.job();batch.engine._sync_candidates(job,batch.host.collect(job));entry=job['files'][0]
    remote='/MP暂存/'+job['id']+'/'+entry['relative']
    parent=PurePosixPath(remote).parent.as_posix()
    ops=NS(_validate_operation=Mock(),check=Mock(),
        native=NS(get_item=Mock(return_value=NS(type='dir',path=parent,fileid='456'))),request=Mock())
    ops.request.return_value={'state':True,'data':[{'fid':'123','pid':'456','fn':PurePosixPath(remote).name,
        'fc':'1','fs':entry['size'],'sha1':entry['sha1']}]}
    host=modules.host.MPHost.__new__(modules.host.MPHost)
    host.config=batch.config;host.stop=Event();host._native_operations=lambda:ops
    entry['receipt']={'fileid':'123'}
    assert host.staged_file_present(entry,remote) and entry['remote_parent_id']=='456'
    assert ops.request.call_args.args==('GET','/open/ufile/files')
    ops.request.return_value['data'][0]['sha1']='0'*40
    with pytest.raises(modules.domain.BridgeError):host.staged_file_present(entry,remote)


def test_native_cleanup_rechecks_scope_identity_and_contents_before_soft_delete(batch,modules):
    path=batch.config.cd2_staging+'/_空目录清理/'+batch.id+'/batch/'+batch.id
    native_path=path[len(batch.config.cd2_prefix):]
    request=Mock(return_value={'state':True,'data':[]})
    ops=NS(_validate_operation=Mock(),check=Mock(),request=request,
        native=NS(get_item=Mock(return_value=NS(type='dir',path=native_path,fileid='123'))))
    host=modules.host.MPHost.__new__(modules.host.MPHost)
    host.config=batch.config;host.stop=Event();host._native_operations=lambda:ops
    host.delete_retired_empty(path,'123',batch.id)
    assert request.call_args.args==('POST','/open/ufile/delete')
    assert request.call_args.kwargs['data']=={'file_ids':123}
    request.reset_mock()
    with pytest.raises(modules.domain.BridgeError):
        host.delete_retired_empty(batch.config.inbox+'/'+batch.id,'123',batch.id)
    assert not request.called
    request.return_value={'state':True,'data':[{'fid':'200','pid':'123','fn':'late.ass',
        'fc':'1','fs':1,'sha1':'a'*40}]}
    with pytest.raises(modules.domain.Awaiting):host.delete_retired_empty(path,'123',batch.id)
    assert all(call.args[0]=='GET' for call in request.call_args_list)
