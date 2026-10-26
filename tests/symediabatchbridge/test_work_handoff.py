import importlib
from pathlib import Path, PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock
import time

import pytest
from test_batches import batch
from test_jitter_handoff_cleanup import clock, cleaner


def part(name):
    return importlib.import_module('app.plugins.symediabatchbridge.'+name)


@pytest.fixture
def work(batch):
    newpaths=[]
    for old in batch.paths:
        new=Path(old.replace('同名剧', '同名剧 (2026) {tmdb-123}'))
        new.parent.mkdir(parents=True,exist_ok=True)
        Path(old).rename(new)
        newpaths.append(str(new))
    batch.paths[:]=newpaths
    job=batch.job();job['handoff_layout']='work';batch.store.save(job)
    batch.cloud.ids={}
    original=batch.cloud.move_directory
    def ident(p):
        if p not in batch.cloud.ids:
            batch.cloud.ids[p]=str(100+len(batch.cloud.ids))
        return batch.cloud.ids[p]
    batch.cloud.directory_id=ident
    def move(src,parent):
        target=parent+'/'+PurePosixPath(src).name
        saved=batch.job()
        assert any(u['source']==src and u['phase']=='requested' for u in saved['work_handoffs'])
        if batch.cloud.exists(target): return False
        batch.cloud.ids[target]=batch.cloud.ids.pop(src)
        return original(src,parent)
    batch.cloud.move_directory=move
    return batch


def test_direct_work_move_keeps_season_and_subtitles(work):
    work.run();job=work.job()
    assert job['state']=='moving',job['message']
    assert len(job['work_handoffs'])==1
    unit=job['work_handoffs'][0]
    assert unit['destination']==work.config.inbox+'/同名剧 (2026) {tmdb-123}'
    assert set(work.cloud.tree(unit['destination']))=={'Season 01/S01E01.mkv','Season 01/S01E01.zh.ass'}
    work.run();assert work.job()['state']=='handed_off'
    assert len(work.cloud.moves)==1 and len(work.host.uploads)==2


@pytest.mark.parametrize('marker',['{tmdb-123}','{tmdbid=123}','{TMDB=123}'])
def test_marker_formats_and_folder_names_are_preserved(work,marker):
    for i,p in enumerate(work.paths):
        new=Path(p.replace('{tmdb-123}',marker));new.parent.mkdir(parents=True,exist_ok=True);Path(p).rename(new);work.paths[i]=str(new)
    work.run()
    assert work.job()['work_handoffs'][0]['destination'].endswith(marker)


def test_missing_tmdb_stops_before_any_move(batch):
    job=batch.job();job['handoff_layout']='work';batch.store.save(job)
    batch.run();assert batch.job()['state']=='review'
    assert not batch.cloud.moves


def test_lost_ack_consumed_children_recover_by_id_without_reupload(work):
    def lost():
        for p in list(work.cloud.files):
            if p.startswith(work.config.inbox+'/'): work.cloud.files.pop(p)
        raise part('domain').Awaiting('lost')
    work.cloud.move_hook=lost
    work.run();job=work.job();unit=job['work_handoffs'][0]
    previous=work.cloud.exists
    work.cloud.exists=lambda p:True if p==unit['destination'] else previous(p)
    work.host.collect=Mock(side_effect=AssertionError('No local recollection after move intent'))
    work.run();assert work.job()['state']=='handed_off'
    assert len(work.host.uploads)==2 and len(work.cloud.moves)==1


def test_same_name_directory_never_merges(work):
    target=work.config.inbox+'/同名剧 (2026) {tmdb-123}'
    work.cloud.files[target+'/old.mkv']={'size':3,'sha1':'a'*40}
    work.run();assert not work.cloud.moves
    assert work.job()['state']=='moving' and '同名作品' in work.job()['message']


def test_move_retry_uses_original_id_and_rate_window(work,clock):
    work.cloud.move_directory=Mock(return_value=False)
    work.run();assert work.cloud.move_directory.call_count==1
    work.run();assert work.cloud.move_directory.call_count==1
    clock.now+=61;work.run();assert work.cloud.move_directory.call_count==2
    unit=work.job()['work_handoffs'][0];work.cloud.ids[unit['source']]='999'
    clock.now+=61;work.run();assert work.job()['state']=='review'
    assert work.cloud.move_directory.call_count==2


def test_tampered_unit_cannot_move_outside_staging(work):
    work.cloud.move_directory=Mock(return_value=False);work.run()
    job=work.job();job['work_handoffs'][0]['source']='/115/foreign';work.store.save(job)
    work.run();assert work.job()['state']=='review'
    assert work.cloud.move_directory.call_count==1


def test_partial_multi_work_restart_only_moves_remaining_units(work,clock):
    new=Path(work.config.local_root)/'电影/外语电影/另一作品 (2025) {tmdbid=456}/movie.mkv'
    new.parent.mkdir(parents=True);new.write_bytes(b'movie');work.paths.append(str(new))
    work.run();assert len(work.job()['work_handoffs'])==2
    work.run();work.run()
    assert work.job()['state']=='handed_off' and len(work.cloud.moves)==2
    assert len(work.host.uploads)==3


def test_arrived_receipt_blocks_next_generation_until_cleanup_done(work):
    work.run();work.run();first=work.job();target=first['work_handoffs'][0]['destination']
    second=work.store.observe(**{**work.observe,'download_hash':'different','history_id':2})
    second.update(work_plan_at=time.time()+1)
    assert work.store.work_blocker(second,target)==first['id']
    first['work_handoffs'][0]['cleanup']={'done':True};work.store.save(first)
    assert work.store.work_blocker(second,target) is None


def test_prepared_plans_order_without_mutual_deadlock(work):
    work.cloud.move_directory=Mock(return_value=False);work.run();first=work.job()
    first['work_handoffs'][0]['phase']='prepared';work.store.save(first)
    target=first['work_handoffs'][0]['destination']
    second=work.store.observe(**{**work.observe,'download_hash':'next','history_id':2})
    second.update(work_plan_at=first['work_plan_at']+1,work_handoffs=[{**first['work_handoffs'][0]}]);work.store.save(second)
    assert work.store.work_blocker(first,target) is None
    assert work.store.work_blocker(second,target)==first['id']


def test_sweep_counts_unknown_files_and_never_selects_root(batch):
    inbox=batch.config.inbox
    rows={inbox:[NS(isDirectory=True,fullPathName=inbox+'/empty',id='11'),NS(isDirectory=True,fullPathName=inbox+'/keep',id='12')],
          inbox+'/empty':[NS(isDirectory=True,fullPathName=inbox+'/empty/season',id='13')],
          inbox+'/empty/season':[], inbox+'/keep':[NS(isDirectory=False,fullPathName=inbox+'/keep/.hidden')]}
    items,count,nonempty=part('inboxsweep').discover(batch.config,NS(_children=lambda p:rows[p]))
    assert count==3 and nonempty==1
    assert len(items)==1 and items[0]['source']==inbox+'/empty' and items[0]['directory_count']==2
    assert not part('inboxsweep').discover(batch.config,NS(_children=lambda p:rows[p]),[inbox+'/empty/inflight'])[0]


def test_work_cleanup_waits_two_checks_then_retires_exact_identity(cleaner,clock):
    c=cleaner;house=part('housekeeping')
    c.stop=Event()
    record={'source':c.job['destination'],'id':'123','interval':300}
    # Exercise the real state machine with a journal-compatible fake sink.
    store=NS(save=Mock())
    c.cloud.move_directory=lambda src,parent:c.cloud.nodes.__setitem__(parent+'/'+PurePosixPath(src).name,c.cloud.nodes.pop(src))
    house._step(c.job,record,c.config,c.host,c.cloud,store,c.stop,'work0')
    assert record['empty_since']==clock.now and not record.get('claim_requested_at')
    clock.now+=299;house._step(c.job,record,c.config,c.host,c.cloud,store,c.stop,'work0')
    assert not record.get('claim_requested_at')
    clock.now+=1;house._step(c.job,record,c.config,c.host,c.cloud,store,c.stop,'work0')
    assert record['phase']=='claiming' and record['holding'] in c.cloud.nodes
    clock.now+=300;house._step(c.job,record,c.config,c.host,c.cloud,store,c.stop,'work0')
    assert record['phase']=='holding'
    # A new directory generation at the old name must never be deleted or merged.
    c.cloud.nodes[record['source']]={'id':'999','files':{'new.mkv':{'size':1}}}
    c.host.delete_retired_empty=Mock(side_effect=lambda p,i,b:c.cloud.nodes.pop(p))
    clock.now+=300;house._step(c.job,record,c.config,c.host,c.cloud,store,c.stop,'work0')
    assert record['phase']=='recycling' and c.cloud.nodes[record['source']]['id']=='999'


def test_sweep_new_pending_target_is_protected(batch,clock):
    sweep=part('inboxsweep');record={'source':batch.config.inbox+'/old','id':'11','interval':300,'routing':batch.config.routing()}
    batch.store.set_meta(sweep.KEY,{'schema':1,'id':'test','authorized_at':clock.now,'entries':[record]})
    job=batch.job();job['destination']=record['source']+'/work';batch.store.save(job)
    sweep.advance(batch.store,[batch.config],NS(),NS(),batch.stop)
    saved=batch.store.meta(sweep.KEY)['entries'][0]
    assert '尚未完成' in saved['error'] and not saved.get('holding')


def test_shallow_cd2_hint_never_overrides_native_descendant_files(cleaner,clock):
    c=cleaner;house=part('housekeeping');record={'source':c.job['destination'],'id':'123','interval':300}
    c.cloud.directory_only_children=Mock(return_value=True)
    c.host.empty_directory=Mock(return_value=False)
    with pytest.raises(part('domain').Awaiting):
        house._step(c.job,record,c.config,c.host,c.cloud,NS(save=Mock()),Event(),'sweep')
    assert not record.get('empty_since') and not c.cloud.moves and not c.cloud.deletes
    assert record['next_at']==clock.now+300  # New work must not inherit the legacy six-hour wait.


@pytest.mark.parametrize('rows,expected', [([],True),([NS(isDirectory=True)],True),([NS(isDirectory=False,name='.hidden')],False)])
def test_cd2_hint_includes_unknown_hidden_files(batch,rows,expected):
    cloud=NS(_children=Mock(return_value=rows))
    assert part('cd2').CD2.directory_only_children(cloud,batch.config.inbox)==expected


def test_unreadable_cd2_hint_is_never_empty(batch):
    with pytest.raises(part('domain').Awaiting):
        part('cd2').CD2.directory_only_children(NS(_children=lambda p:None),batch.config.inbox)


@pytest.fixture
def scanner(batch,clock,monkeypatch):
    s=part('inboxsweep');root=batch.config.inbox
    monkeypatch.setattr(s,'SCAN_DIRECTORIES_PER_ROUND',3)
    scan={'pending':[{'path':root,'depth':0,'routing':batch.config.routing()}], 'nodes':{},'ids':{},'files':{},'protected':[]}
    plan={'schema':1,'id':'acceptance','authorized_at':clock.now,'entries':[],'discovery':scan}
    batch.store.set_meta(s.KEY,plan)
    rows={'1':[{'name':'A','id':'2','directory':True}], '2':[{'name':'B','id':'3','directory':True}],
          '3':[{'name':'C','id':'4','directory':True}], '4':[]}
    calls=[]
    def files(self,ident):calls.append(ident);return rows[ident]
    monkeypatch.setattr(part('duplicates').Isolation,'files',files)
    host=NS(gate_cloud=Mock(),_native_operations=lambda:NS(request=Mock()))
    host.for_config=lambda config:host
    cloud=NS(directory_id=Mock(return_value='1'))
    return NS(s=s,b=batch,clock=clock,rows=rows,calls=calls,host=host,cloud=cloud,root=root,
              run=lambda:s.advance(batch.store,[batch.config],host,cloud,batch.stop))


def test_inventory_checkpoints_three_directories_then_resumes_after_restart(scanner,modules):
    x=scanner;x.run();saved=x.b.store.meta(x.s.KEY)
    assert x.calls==['1','2','3'] and not saved.get('inventory_complete')
    assert saved['discovery']['pending'][0]['path']==x.root+'/A/B/C'
    x.run();assert len(x.calls)==3
    x.clock.now+=61
    x.b.store=modules.store.Store(x.b.store.path.parent)
    x.s.advance(x.b.store,[x.b.config],x.host,x.cloud,x.b.stop)
    saved=x.b.store.meta(x.s.KEY)
    assert x.calls==['1','2','3','4'] and saved['inventory_complete']
    assert len(saved['entries'])==1 and saved['entries'][0]['source']==x.root+'/A'
    assert saved['entries'][0]['directory_count']==3
    assert x.cloud.directory_id.call_count==1


def test_inventory_rate_limit_preserves_progress_and_deadline(scanner):
    x=scanner;x.run();x.clock.now+=61
    error=part('cooldown').CloudCooldown(x.clock.now+3600)
    x.host.gate_cloud.side_effect=error
    x.run();saved=x.b.store.meta(x.s.KEY)
    assert saved['discovery']['next_at']==error.next_at
    assert len(saved['discovery']['pending'])==1 and len(x.calls)==3
    x.host.gate_cloud.side_effect=None
    x.clock.now+=60;x.run();assert len(x.calls)==3
    x.clock.now=error.next_at+1;x.run();assert x.b.store.meta(x.s.KEY)['inventory_complete']


def test_inventory_read_failure_never_advances_to_empty_cleanup(scanner,monkeypatch):
    x=scanner
    monkeypatch.setattr(part('duplicates').Isolation,'files',Mock(side_effect=part('domain').Awaiting('unreadable')))
    x.run();saved=x.b.store.meta(x.s.KEY)
    assert saved['entries']==[] and len(saved['discovery']['pending'])==1
    assert not saved.get('inventory_complete') and 'unreadable' in saved['discovery']['error']
