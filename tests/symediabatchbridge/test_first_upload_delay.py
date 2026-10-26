"""Initial local wait is separate from hourly misses and durable across restarts."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import importlib
import time

import pytest

from test_batches import batch


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr(time, 'time', lambda: clock.now)
    return clock


def enable(batch, minutes=60):
    batch.engine.config = replace(batch.config, first_upload_delay=True, first_upload_delay_minutes=minutes)


def test_defaults_are_enabled_sixty_minutes_and_pinned(modules,config_values):
    config=modules.domain.Config.parse({k:v for k,v in config_values.items() if k!='first_upload_delay'})
    assert config.first_upload_delay and config.first_upload_delay_minutes==60
    changed=replace(config,first_upload_delay_minutes=125)
    assert changed.pinned(config.routing()).first_upload_delay_minutes==125
    form,defaults=modules.plugin.SymediaBatchBridge().get_form()
    assert defaults['first_upload_delay'] is True and defaults['first_upload_delay_minutes']==60
    assert 'first_upload_delay_minutes' in str(form)


@pytest.mark.parametrize('value',[0,-1,10081,None,True,1.5,'nan','1.2',float('inf')])
def test_invalid_delay_is_rejected(modules,config_values,value):
    with pytest.raises(ValueError,match='首次上传延迟'):
        modules.domain.Config.parse({**config_values,'first_upload_delay_minutes':value})


def test_local_wait_hashes_without_contacting_cloud_and_starts_exactly_at_deadline(batch,clock):
    enable(batch)
    exists=Mock(wraps=batch.cloud.exists);batch.cloud.exists=exists
    batch.run();saved=batch.job()
    assert saved['state']=='waiting_upload' and saved['next_check']==clock.now+3600
    assert len(saved['files'])==2 and all(e['sha1'] for e in saved['files'])
    assert all(e['first_upload_after']==e['hash_completed_at']+3600 for e in saved['files'])
    assert not any(e.get('instant_requests') or e.get('instant_misses') for e in saved['files'])
    exists.assert_not_called();assert not batch.host.uploads
    clock.now+=3599;batch.run();exists.assert_not_called()
    clock.now+=1;batch.run()
    assert batch.job()['state']=='handed_off' and len(batch.host.uploads)==2


def test_disabled_wait_uploads_immediately(batch,clock):
    batch.run()
    assert batch.job()['state']=='handed_off'
    assert all(e['first_upload_after']==e['hash_completed_at'] for e in batch.job()['files'])


def test_custom_duration_and_each_file_has_its_own_completion_time(batch,clock):
    enable(batch,minutes=5)
    original=batch.engine._hash_file
    def hashing(*args,**kwargs):
        result=original(*args,**kwargs);clock.now+=20;return result
    batch.engine._hash_file=hashing
    batch.run();entries=batch.job()['files']
    assert entries[1]['first_upload_after']-entries[0]['first_upload_after']==20
    clock.now=entries[0]['first_upload_after'];batch.run()
    assert sum(e['uploaded'] for e in batch.job()['files'])==1 and not batch.cloud.moves
    clock.now=entries[1]['first_upload_after'];batch.run()
    assert batch.job()['state']=='handed_off'


def test_restart_recheck_and_setting_changes_never_reset_a_saved_wait(batch,clock,modules):
    enable(batch);batch.run();old=batch.job()
    store=modules.store.Store(batch.store.path.parent)
    engine=modules.engine.Engine(store,replace(batch.config,first_upload_delay=False,
        first_upload_delay_minutes=1),batch.host,batch.cloud,Event())
    old['retry_requested']=clock.now;store.save(old)
    clock.now+=300;engine.process(store.get(batch.id));saved=store.get(batch.id)
    assert saved['recheck_result']['state']=='waiting_upload'
    assert [e['first_upload_after'] for e in saved['files']]==[e['first_upload_after'] for e in old['files']]
    assert not batch.host.uploads
    clock.now=old['files'][0]['first_upload_after'];engine.process(store.get(batch.id))
    assert store.get(batch.id)['state']=='handed_off'


def test_legacy_hashes_and_retry_deadlines_are_not_retroactively_delayed(batch,clock,modules):
    job=batch.job();batch.engine._sync_candidates(job,batch.host.collect(job))
    for entry in job['files']:
        entry.pop('first_upload_after');entry.pop('hash_completed_at')
        entry.update(instant_started_at=clock.now-7200,instant_next_at=clock.now+600,
                     instant_requests=2,instant_misses=2)
    batch.store.save(job);enable(batch)
    batch.run();saved=batch.job()
    assert saved['state']=='waiting_instant' and saved['next_check']==clock.now+600
    assert all(e['instant_misses']==2 and 'first_upload_after' not in e for e in saved['files'])
    clock.now+=600;batch.run()
    assert batch.job()['state']=='handed_off'


def test_initial_wait_does_not_consume_one_of_twenty_four_attempts(batch,clock):
    enable(batch);batch.host.try_instant=Mock(return_value=None)
    start=clock.now;batch.run()
    for hour in range(1,25):
        clock.now=start+hour*3600;batch.run()
        assert all(e['instant_misses']==hour for e in batch.job()['files'])
        assert not batch.host.uploads
    assert batch.host.try_instant.call_count==48
    clock.now=start+25*3600-1;batch.run();assert not batch.host.uploads
    clock.now+=1;batch.run()
    assert batch.job()['state']=='handed_off' and len(batch.host.uploads)==2


def test_existing_cloud_receipt_remains_valid_even_with_new_delay_enabled(batch,clock):
    job=batch.job();batch.engine._sync_candidates(job,batch.host.collect(job))
    for entry in job['files']:
        batch.cloud.files[batch.config.cd2_staging+'/'+job['id']+'/'+entry['relative']]={
            'size':entry['size'],'sha1':entry['sha1']}
        entry.update(uploaded=True,receipt={'fileid':'123','method':'instant'})
        entry.pop('first_upload_after');entry.pop('hash_completed_at')
    batch.store.save(job);enable(batch);batch.run()
    assert batch.job()['state']=='handed_off' and not batch.host.uploads
    assert all(e['receipt']['fileid']=='123' for e in batch.job()['files'])


def test_wait_and_deadline_are_visible_in_activity_and_detail(batch,clock,modules):
    enable(batch);batch.run();job=batch.job()
    events=batch.store.events(batch.id)
    assert sum(e['kind']=='first_upload_wait' for e in events)==2
    assert all(e['next_at']==clock.now+3600 for e in events if e['kind']=='first_upload_wait')
    page=importlib.import_module('app.plugins.symediabatchbridge.page')
    assert page.state_label(job)=='等待首次上传'
