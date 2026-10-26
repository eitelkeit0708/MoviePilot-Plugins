import importlib
import json
from pathlib import PurePosixPath
from unittest.mock import Mock

import pytest

from test_batches import batch
from test_jitter_handoff_cleanup import cleaner, clock


@pytest.fixture
def dated(batch):
    job = batch.job()
    job['handoff_layout'] = 'date'
    handoff = importlib.import_module('app.plugins.symediabatchbridge.handoff')
    _, parent = handoff.destination(job, batch.config, batch.store)
    batch.store.set_meta('inbox_date_owners', {parent:'123'})
    batch.cloud.created_directories = {parent:'456'}
    batch.cloud.ensure_directory = Mock(return_value='456')
    batch.parent = parent
    return batch


def test_recreated_date_is_registered_without_rewriting_legacy_identity(dated, modules):
    b = dated
    b.run()
    saved = b.job()
    assert saved['state'] == 'handed_off', saved['message']
    assert saved['delivery_parent_id'] == '456'
    assert b.store.meta('inbox_date_owners')[b.parent] == '123'
    assert b.store.meta('inbox_date_generations')[b.parent] == ['123','456']
    reopened = modules.store.Store(b.store.path.parent)
    for ident in ('123','456'):
        assert reopened.owned_inbox_date(b.parent,ident) == ident
    assert reopened.owned_inbox_date(b.parent,'789') is None
    assert reopened.owned_inbox_date(b.parent) == '123'
    reopened.remember_inbox_date(b.parent,'456')
    assert reopened.meta('inbox_date_generations')[b.parent] == ['123','456']


def test_multiple_generations_remain_proven_and_unrelated_paths_unchanged(dated):
    b = dated
    b.store.remember_inbox_date(b.parent,'456')
    b.store.remember_inbox_date(b.parent,'789')
    other = b.parent + '-other'
    b.store.remember_inbox_date(other,'999')
    assert b.store.meta('inbox_date_generations')[b.parent] == ['123','456','789']
    assert b.store.owned_inbox_date(other,'456') is None
    assert b.store.meta('inbox_date_owners') == {b.parent:'123',other:'999'}


@pytest.mark.parametrize('identity', ['',None,'local-id','0','1'*41])
def test_missing_or_unknown_date_identity_never_proves_ownership(dated, modules, identity):
    b = dated
    before = b.store.meta('inbox_date_owners')
    with pytest.raises(modules.domain.Awaiting):
        b.store.remember_inbox_date(b.parent,identity)
    assert b.store.meta('inbox_date_owners') == before
    assert b.store.meta('inbox_date_generations') is None


def test_recreated_date_preserves_existing_hash_receipt_and_retry_deadline(dated):
    b = dated
    job = b.job()
    b.engine._sync_candidates(job,b.host.collect(job))
    job = b.job()
    first, second = job['files']
    first.update(uploaded=True,receipt={'size':first['size'],'fileid':'100','method':'instant'})
    b.cloud.files[b.config.cd2_staging+'/'+job['id']+'/'+first['relative']] = {
        'size':first['size'],'sha1':first['sha1']}
    second.update(instant_misses=5,instant_started_at=1,instant_requests=5,
                  instant_next_at=9_000_000_000)
    b.store.save(job)
    original = json.loads(json.dumps(job['files']))
    b.run()
    saved = b.job()
    assert saved['state'] == 'waiting_instant', saved['message']
    assert saved['files'] == original
    assert b.host.uploads == []
    assert b.store.owned_inbox_date(b.parent,'456') == '456'


def prepare_date_cleanup(c, identity):
    job = c.batch.job()
    day = c.config.inbox + '/2026-10-01'
    job.update(handoff_layout='date', destination=day+'/'+job['id'], delivery_parent_id=identity,
               inbox_cleanup={'batch':{'source':day+'/'+job['id'],'id':'123','done':True}})
    c.batch.store.save(job)
    c.batch.store.remember_inbox_date(day,'456')
    return day


def test_old_date_cleanup_preserves_replacement_and_old_identity(cleaner):
    c = cleaner
    day = prepare_date_cleanup(c,'456')
    c.batch.store.remember_inbox_date(day,'789')
    c.cloud.nodes[day] = {'id':'789','files':{'new.mkv':{'size':99}}}
    saved = c.run()
    record = saved['inbox_cleanup']['date']
    assert record['id'] == '456' and record['phase'] == 'replaced'
    assert record['observed_id'] == '789' and saved['inbox_cleanup']['done']
    assert c.cloud.moves == c.cloud.deletes == []
    assert c.cloud.nodes[day]['files'] == {'new.mkv':{'size':99}}
    assert '新目录' in saved['inbox_cleanup']['note']


def test_current_date_cleanup_uses_its_proven_generation_and_keeps_media(cleaner):
    c = cleaner
    day = prepare_date_cleanup(c,'789')
    c.batch.store.remember_inbox_date(day,'789')
    c.cloud.nodes[day] = {'id':'789','files':{'new.srt':{'size':99}}}
    saved = c.run()
    assert saved['inbox_cleanup']['date']['id'] == '789'
    assert saved['inbox_cleanup'].get('error') and not saved['inbox_cleanup'].get('done')
    assert c.cloud.moves == c.cloud.deletes == []


def test_current_unproven_date_is_preserved_without_adopting_legacy_owner(cleaner):
    c = cleaner
    day = prepare_date_cleanup(c,'789')
    c.cloud.nodes[day] = {'id':'789','files':{}}
    saved = c.run()
    assert 'date' not in saved['inbox_cleanup']
    assert saved['inbox_cleanup']['done']
    assert c.cloud.moves == c.cloud.deletes == [] and day in c.cloud.nodes


def test_unknown_current_date_id_is_not_marked_replaced_or_deleted(cleaner):
    c = cleaner
    day = prepare_date_cleanup(c,'456')
    c.cloud.nodes[day] = {'id':'','files':{}}
    saved = c.run()
    assert saved['inbox_cleanup']['date'].get('phase') != 'replaced'
    assert not saved['inbox_cleanup'].get('done') and saved['inbox_cleanup'].get('error')
    assert c.cloud.moves == c.cloud.deletes == []


def test_unexpected_registration_error_records_step_without_raw_sdk_text(dated, modules):
    b = dated
    job = b.job()
    b.engine._sync_candidates(job,b.host.collect(job))
    hashes = [e['sha1'] for e in b.job()['files']]
    remember = b.store.remember_inbox_date
    b.store.remember_inbox_date = Mock(side_effect=ValueError('https://secret.invalid?token=SECRET'))
    b.run(); b.run()
    saved = b.job()
    assert saved['state'] == 'retrying'
    assert saved['processing_error']['stage'] == '登记日期目录'
    assert saved['processing_error']['type'] == 'ValueError'
    assert saved['processing_error']['frames']
    assert saved['message'].startswith('处理步骤异常：登记日期目录')
    events = b.store.events(b.id,limit=100)
    assert 'SECRET' not in json.dumps(saved) + json.dumps(events)
    assert sum(e['kind']=='processing_error' for e in events) == 1
    assert all(not e.get('instant_requests') and not e.get('instant_misses') for e in saved['files'])
    remedies=importlib.import_module('app.plugins.symediabatchbridge.remedies')
    assert remedies.remedy(saved)['title']=='处理步骤发生异常'
    b.store.remember_inbox_date = remember
    b.run()
    assert b.job()['state']=='handed_off'
    assert [e['sha1'] for e in b.job()['files']]==hashes


def test_unexpected_intake_error_is_logged_without_network_diagnosis(batch):
    batch.host.collect=Mock(side_effect=RuntimeError('Bearer SECRET'))
    batch.run()
    saved=batch.job()
    assert saved['processing_error']['stage']=='处理批次'
    assert saved['processing_error']['type']=='RuntimeError'
    assert 'SECRET' not in json.dumps(saved) + json.dumps(batch.store.events(batch.id))
