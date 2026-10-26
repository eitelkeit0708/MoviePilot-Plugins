from types import SimpleNamespace as NS
from pathlib import Path
import json

import pytest

from test_batches import batch
from test_adapters import native
from test_cloud_cooldown import api, response


@pytest.mark.parametrize('stopped',[False,True])
def test_protocol_checkpoint_is_durable_before_http_and_survives_stop(batch,native,api,modules,monkeypatch,stopped):
    job=batch.job()
    batch.engine._sync_candidates(job,batch.host.collect(job))
    job=batch.job(); entry=job['files'][0]
    native.host.bind_cooldown(api.guard,batch.stop)
    native.host._native_operations=lambda:api.ops
    native.host._upload_parent=lambda path,ops:NS(fileid='123')
    monkeypatch.setattr(modules.host,'native_provider',lambda:api.provider)
    batch.engine.host=native.host
    def send(*args,**kwargs):
        persisted=batch.store.get(batch.id)['files'][0]
        assert persisted['instant_requests']==1
        assert persisted['instant_protocol'][-1]['stage']=='request'
        if stopped:batch.stop.set()
        return response(data={'state':True,'code':0,'data':{'status':2,'file_id':'123','callback':'PRIVATE'}})
    api.provider.session.request.side_effect=send
    if stopped:
        with pytest.raises(modules.domain.Stopped):
            batch.engine._upload_entry(job,entry,'/MP暂存/test/video.mkv')
    else:
        batch.engine._upload_entry(job,entry,'/MP暂存/test/video.mkv')
    saved=batch.store.get(batch.id)['files'][0]
    assert [event['stage'] for event in saved['instant_protocol']]==['request','http','result']
    assert saved['uploaded'] is (not stopped)
    assert saved['instant_requests']==1 and not saved.get('instant_misses')
    assert native.host._instant_checkpoint is None
    assert 'PRIVATE' not in json.dumps(saved)
    assert any(e['kind']=='instant_protocol' for e in batch.store.events(batch.id))


@pytest.mark.parametrize('directory_code', [430004, 998])
def test_directory_preparation_is_checkpointed_before_upload_initialization(batch,native,api,modules,monkeypatch,directory_code):
    job = batch.job()
    batch.engine._sync_candidates(job, batch.host.collect(job))
    job = batch.job(); entry = job['files'][0]
    original_sha = entry['sha1']
    native.host.bind_cooldown(api.guard, batch.stop)
    native.host._native_operations = lambda: api.ops
    def parent(path, ops):
        answer = ops.request('POST', '/open/folder/get_info', data={'path': '/MP暂存/test'}, accepted_codes=(0,20004))
        assert answer['code'] == 430004 and not answer['state']
        ops.request('POST', '/open/folder/add', data={'pid': '1', 'file_name': 'test'})
        return NS(fileid='123')
    native.host._upload_parent = parent
    monkeypatch.setattr(modules.host, 'native_provider', lambda: api.provider)
    batch.engine.host = native.host
    endpoints = []
    def send(method, url, **kwargs):
        endpoint = url.removeprefix(api.provider.base_url)
        endpoints.append(endpoint)
        persisted = batch.store.get(batch.id)['files'][0]
        checkpoint = persisted['instant_protocol'][-1]
        assert checkpoint['stage'] == 'request' and checkpoint['endpoint'] == endpoint
        assert persisted['sha1'] == original_sha
        assert persisted['instant_next_at'] == api.clock.now + 3600
        if endpoint == '/open/folder/get_info':
            return response(data={'state':False, 'code':directory_code, 'message':'PRIVATE', 'data':[]})
        if endpoint == '/open/folder/add':
            return response(data={'state':True, 'code':0, 'data':{'file_id':'123'}})
        return response(data={'state':True, 'code':0, 'data':{'status':2, 'file_id':'456'}})
    api.provider.session.request.side_effect = send
    if directory_code == 998:
        with pytest.raises(api.cooldown.CloudRequestError):
            batch.engine._upload_entry(job, entry, '/MP暂存/test/video.mkv')
    else:
        batch.engine._upload_entry(job, entry, '/MP暂存/test/video.mkv')
    saved = batch.store.get(batch.id)['files'][0]
    assert saved['sha1'] == original_sha and not saved.get('instant_misses')
    assert saved['instant_requests'] == 1
    assert 'PRIVATE' not in json.dumps(saved)
    assert native.host._instant_checkpoint is None
    events = [e for e in batch.store.events(batch.id) if e['kind'] == 'instant_protocol']
    assert any('查询暂存目录' in e['message'] and str(directory_code) in e['message'] for e in events)
    if directory_code == 998:
        assert endpoints == ['/open/folder/get_info']
        assert not saved['uploaded'] and not saved.get('remote_parent_id')
        assert saved['instant_error_kind'] == 'unknown'
        assert saved['instant_next_at'] == api.clock.now + 3600
    else:
        assert endpoints == ['/open/folder/get_info', '/open/folder/add', '/open/upload/init']
        assert saved['uploaded'] and saved['receipt']['fileid'] == '456'
        assert saved['remote_parent_id'] == '123'
        assert len(saved['instant_protocol']) == 9
        assert any('创建暂存目录' in e['message'] for e in events)
        assert any('初始化响应' in e['message'] for e in events)
