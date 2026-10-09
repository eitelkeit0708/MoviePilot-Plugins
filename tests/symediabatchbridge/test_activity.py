import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from test_batches import batch
from test_plugin_contract import plugin, add_job


def test_activity_atomic_with_receipt_and_preserved_after_restart(batch, modules):
    batch.run()
    restored=modules.store.Store(batch.store.path.parent)
    kinds=[e['kind'] for e in restored.events(batch.id,limit=100)]
    assert kinds.count('received')==1 and kinds.count('hash')==2
    assert kinds.count('instant_start')==2 and kinds.count('file_complete')==2 and kinds.count('handoff')==1
    notices=restored.pending_notices()
    assert len(notices)==1 and '移交成功' in notices[0]['title']
    batch.run()
    assert len(restored.pending_notices())==1


def test_notification_failure_retries_without_replaying_success(plugin, monkeypatch):
    p=plugin.p; job=add_job(p)
    now=1_900_000_000
    monkeypatch.setattr(plugin.modules.store.time,'time',lambda:now)
    p.post_message.side_effect=RuntimeError('secret-token')
    job.update(state='handed_off',message='已移交',destination='/115/inbox/batch')
    p._store.save(job)
    assert p._store.get(job['id'])['state']=='handed_off'
    assert p.post_message.call_count == 0
    assert p._store.pending_notices()
    p._flush_notifications()
    assert not p._store.pending_notices()
    p._flush_notifications()
    assert p.post_message.call_count==1
    now+=60
    p.post_message.side_effect=None
    p._flush_notifications()
    assert p.post_message.call_count==2
    assert p.post_message.call_args.kwargs['mtype']=='插件'
    p._flush_notifications()
    assert p.post_message.call_count==2
    assert p._store.notice_counts(job['id'])==(1,1)
    assert 'secret-token' not in json.dumps(p._store.events(job['id']))


def test_fallback_notice_precedes_upload_once_per_file(batch, monkeypatch):
    now=1_900_000_000
    monkeypatch.setattr('time.time',lambda:now)
    batch.engine._sync_candidates(batch.job(),batch.host.collect(batch.job()))
    job=batch.job()
    for e in job['files']:
        e.update(instant_misses=24,instant_started_at=now-86400,instant_next_at=now)
    batch.store.save(job)
    checked=[]
    original=batch.host.upload
    def upload(local,remote):
        notices=batch.store.pending_notices()
        assert any('转普通上传' in n['title'] and local.name in n['text'] for n in notices)
        checked.append(local)
        return original(local,remote)
    batch.host.upload=upload
    batch.run()
    assert len(checked)==2
    notices=batch.store.pending_notices()
    assert len([n for n in notices if '转普通上传' in n['title']])==2
    assert len([n for n in notices if '移交成功' in n['title']])==1


def test_instant_failure_notifies_immediately_but_miss_does_not(batch, modules):
    batch.host.try_instant=Mock(side_effect=RuntimeError('sdk-token=secret'))
    batch.run()
    assert len(batch.store.pending_notices())==1
    assert all(e.get('instant_misses',0)==0 for e in batch.job()['files'])
    events=json.dumps(batch.store.events(batch.id),ensure_ascii=False)
    assert 'secret' not in events and '秒传接口暂不可用' in events


def test_ordinary_non_hits_have_trace_without_failure_notice(batch):
    batch.host.try_instant=Mock(return_value=None)
    batch.run()
    assert not batch.store.pending_notices()
    assert len([e for e in batch.store.events(batch.id) if e['kind']=='instant_miss'])==2


def test_exception_first_query_does_not_lose_old_exception_after_page_limit(plugin):
    p=plugin.p
    old=add_job(p); old.update(state='review',message='token expired')
    p._store.save(old)
    for i in range(60):
        job=p._store.observe(instance='SymediaBatchBridge', download_hash=str(i),downloader='qb',title='new',history_id=i+2,routing=p._runtime.config.routing())
        job.update(state='handed_off',message='done'); p._store.save(job)
    assert p._store.page_jobs()[0]['id']==old['id']
    assert sum(len(p._store.page_jobs(page)) for page in range(6))==61


def test_view_paginates_full_files_and_timeline_no_tokens(plugin):
    p=plugin.p; job=add_job(p)
    job['files']=[{'relative':f'file-{i}.mkv','size':i,'uploaded':True} for i in range(25)]
    p._store.save(job)
    for i in range(35):
        p._store.record(job,plugin.modules.activity.event('test',f'trace-{i}'))
    p.view_records(plugin.modules.plugin.ViewRequest(key=job['id'],files=1,events=1))
    tree=p.get_page()
    page=json.dumps(tree,ensure_ascii=False)
    def titles(items):
        return [node['text'] for node in items if node.get('component')=='h4'] + [title for node in items for title in titles(node.get('content',[]))]
    assert 'file-24.mkv' in titles(tree) and 'file-0.mkv' not in titles(tree)
    assert 'trace-0' in page and 'trace-34' not in page
    assert 'test-secret' not in page and 'SHA1' in page


def test_check_updates_user_visible_health_even_without_new_files(plugin):
    plugin.p.check_batches()
    page=json.dumps(plugin.p.get_page(),ensure_ascii=False)
    assert '最近检查' in page and '本轮读取 0 条' in page
    assert plugin.p._store.meta('last_check')
    plugin.modules.plugin.logger.info.assert_called()


def test_once_switch_is_durable_resets_and_imports_only_existing_records(plugin,tmp_path,monkeypatch):
    p=plugin.p
    path=Path(plugin.values['local_root'])/'old.mkv';path.write_bytes(b'old')
    row=NS(id=3,title='old',dest=str(path),status=True,date='2020-01-01 01:00:00',download_hash='oldhash',downloader='qb')
    missing=NS(**{**row.__dict__,'id':4,'dest':str(path.parent/'missing.mkv'),'download_hash':'gone'})
    plugin.host.histories_since.return_value=[row,missing]
    plugin.host.transfers.get.return_value=row
    p.init_plugin({**plugin.values,'scan_existing_once':True})
    assert p._store.meta('inventory_pending') is True
    assert p.update_config.call_args.args[0]['scan_existing_once'] is False
    assert '已接收存量处理请求' in json.dumps(p.get_page(), ensure_ascii=False)
    # Restart before scheduled scan, using the auto-reset config.
    p.init_plugin(plugin.values)
    monkeypatch.setattr(plugin.modules.plugin.Engine,'process',lambda *a:None)
    p.check_batches()
    assert not p._store.meta('inventory_pending')
    assert len(p._store.jobs())==1
    p.check_batches()
    assert len(p._store.jobs())==1
    assert p._store.meta('existing_scan')['imported']==1
    assert p._store.jobs()[0]['origin'] == 'inventory'
    assert p._store.jobs()[0]['inventory_files'][0]['local'] == str(path)
    assert '存量检查完成：接管 1 批' in json.dumps(p.get_page()[0], ensure_ascii=False)


def test_inventory_failure_keeps_request_and_visible_retry_state(plugin, monkeypatch):
    p = plugin.p
    p.init_plugin({**plugin.values, 'scan_existing_once': True})
    monkeypatch.setattr(plugin.modules.plugin, 'scan_inventory', Mock(side_effect=OSError('secret-path-token')))
    p.check_batches()
    assert p._store.meta('inventory_pending')
    page = json.dumps(p.get_page()[0], ensure_ascii=False)
    assert '下轮自动重试' in page and 'secret-path-token' not in page


def test_hashing_progress_visible_in_batch_list_and_details(plugin):
    p = plugin.p
    job = add_job(p)
    job.update(state='hashing', message='正在计算 HASH：视频.mkv',
               hash_progress={'file': '视频.mkv', 'done': 4 * 1024**2, 'total': 8 * 1024**2, 'at': 1})
    p._store.save(job)
    for detail in (False, True):
        if detail:
            p.view_records(plugin.modules.plugin.ViewRequest(key=job['id']))
        page = json.dumps(p.get_page(), ensure_ascii=False)
        assert '50.0%' in page and '4.00 MiB / 8.00 MiB' in page and 'VProgressLinear' in page


def test_inventory_count_does_not_claim_missing_record_as_imported(plugin, monkeypatch):
    p = plugin.p
    p.init_plugin({**plugin.values, 'scan_existing_once': True})
    plugin.host.transfers.get.return_value = None
    monkeypatch.setattr(plugin.modules.plugin, 'scan_inventory', lambda runtime: {
        'at': 1, 'candidates': [{'history_id': 999, 'members': []}], 'routes': [], 'unmatched': []})
    p.check_batches()
    assert p._store.meta('existing_scan')['imported'] == 0
    assert p._store.meta('existing_scan')['skipped'] == 1


@pytest.mark.parametrize('imported,sealed', [(True, False), (False, False), (True, True)])
def test_legacy_origin_restored_only_from_unsealed_import_event(plugin, imported, sealed):
    p = plugin.p
    job = add_job(p)
    job.pop('origin')
    if sealed:
        job['files'] = [{'relative': 'sealed.mkv'}]
    p._store.save(job)
    if imported:
        p._store.record(job, plugin.modules.activity.event('imported', 'existing'))
    p._store.restore_origin(job)
    assert job['origin'] == ('inventory' if imported and not sealed else 'transfer')
    p._store.restore_origin(job)
    assert p._store.get(job['id'])['origin'] == job['origin']


def test_inventory_lists_orphans_and_uses_latest_record(plugin):
    root=Path(plugin.values['local_root'])
    orphan=root/'subtitle.srt';orphan.write_bytes(b'hello')
    video=root/'video.mkv';video.write_bytes(b'hello')
    old=NS(id=1,dest=str(video),status=True,download_hash='old',downloader='qb',title='old',date='2020')
    newer=NS(**{**old.__dict__,'id':2,'status':False})
    plugin.host.histories_since.return_value=[newer,old]
    result=plugin.modules.inventory.scan(plugin.p._runtime)
    assert not result['candidates']
    assert result['routes'][0]['unmatched']==2
    assert len(result['unmatched'])==2


def cleanup_fixture(batch, modules, tmp_path):
    # Replace fixture files with genuine hardlinks BEFORE freezing their signature.
    sources=[]
    for i,path in enumerate(batch.paths):
        local=Path(path);source=tmp_path/f'download-{i}.bin';source.write_bytes(local.read_bytes())
        local.unlink();os.link(source,local);sources.append(source)
    batch.host.collect=lambda job:[{'local':str(p),'source':str(s)} for p,s in zip(batch.paths,sources)]
    job=batch.job();job['cleanup_local']=True;batch.store.save(job)
    batch.run()
    assert batch.job()['state']=='handed_off'
    return sources


def test_cleanup_only_removes_organized_links_retains_seeding_source(batch,modules,tmp_path):
    sources=cleanup_fixture(batch,modules,tmp_path)
    modules.cleanup.cleanup(batch.store,batch.job(),batch.config,batch.stop)
    assert batch.job()['cleanup_done']
    assert all(p.is_file() and p.stat().st_size for p in sources)
    assert not any(Path(p).exists() for p in batch.paths)


@pytest.mark.parametrize('problem',['changed','missing_original','copy','not_handed_off','disabled'])
def test_cleanup_refuses_unsafe_or_unconfirmed_data(batch,modules,tmp_path,problem):
    sources=cleanup_fixture(batch,modules,tmp_path)
    local=Path(batch.paths[0]);job=batch.job()
    if problem=='changed': local.write_bytes(b'changed')
    if problem=='missing_original': sources[0].unlink()
    if problem=='copy':
        data=local.read_bytes();local.unlink();local.write_bytes(data)
    if problem=='not_handed_off': job['state']='review'
    if problem=='disabled': job['cleanup_local']=False
    batch.store.save(job)
    modules.cleanup.cleanup(batch.store,job,batch.config,batch.stop)
    assert local.exists()
    assert not batch.job().get('cleanup_done')


def test_cleanup_recovers_unlink_without_receipt(batch,modules,tmp_path):
    sources=cleanup_fixture(batch,modules,tmp_path)
    Path(batch.paths[0]).unlink()
    modules.cleanup.cleanup(batch.store,batch.job(),batch.config,batch.stop)
    assert batch.job()['cleanup_done'] and all(p.exists() for p in sources)


def test_cleanup_cannot_delete_download_path_outside_route(batch,modules,tmp_path):
    sources=cleanup_fixture(batch,modules,tmp_path)
    job=batch.job(); job['files'][0]['local']=str(sources[0])
    batch.store.save(job)
    modules.cleanup.cleanup(batch.store,job,batch.config,batch.stop)
    assert sources[0].exists() and Path(batch.paths[0]).exists()
    assert batch.job().get('cleanup_error')


def test_cleanup_choice_is_pinned_at_first_observation(batch):
    assert batch.job()['cleanup_local'] is False
    batch.store.observe(**batch.observe,cleanup_local=True)
    assert batch.job()['cleanup_local'] is False


def test_observer_failure_cannot_rollback_uploaded_receipt(batch):
    batch.store.event_sink=Mock(side_effect=RuntimeError('logger down'))
    batch.run()
    assert batch.job()['state']=='handed_off'
    assert batch.store.pending_notices()


def test_cleanup_rechecks_file_replaced_during_isolation(batch,modules,tmp_path,monkeypatch):
    sources=cleanup_fixture(batch,modules,tmp_path)
    original=modules.cleanup.os.rename
    local=Path(batch.paths[0])
    def race(src,dst):
        Path(src).unlink()
        Path(src).write_bytes(b'new version from MP')
        original(src,dst)
    monkeypatch.setattr(modules.cleanup.os,'rename',race)
    modules.cleanup.cleanup(batch.store,batch.job(),batch.config,batch.stop)
    assert local.read_bytes()==b'new version from MP'
    assert sources[0].exists() and batch.job().get('cleanup_error')


def test_cleanup_resumes_after_isolation_crash(batch,modules,tmp_path,monkeypatch):
    sources=cleanup_fixture(batch,modules,tmp_path)
    original=modules.cleanup.os.rename
    def stop_after_rename(src,dst):
        original(src,dst)
        raise SystemExit('simulate process death')
    monkeypatch.setattr(modules.cleanup.os,'rename',stop_after_rename)
    with pytest.raises(SystemExit):
        modules.cleanup.cleanup(batch.store,batch.job(),batch.config,batch.stop)
    pending=batch.job()['files'][0]['cleanup_pending']
    assert Path(pending).exists()
    monkeypatch.setattr(modules.cleanup.os,'rename',original)
    modules.cleanup.cleanup(batch.store,batch.job(),batch.config,batch.stop)
    assert batch.job()['cleanup_done'] and all(p.exists() for p in sources)
