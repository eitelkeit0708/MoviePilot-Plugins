import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_adapters import native
from test_batches import batch
from test_plugin_contract import plugin, add_job


@pytest.fixture
def overlap(native):
    for row in native.rows:
        row.id += 100
    source = str(Path(native.rows[0].src).with_name('older-episode.mkv')).replace('\\', '/')
    native.files.append(NS(name='Release/older-episode.mkv', size=7, priority=1, progress=1))
    prior = NS(**{**vars(native.rows[0]), 'id': 50, 'src': source,
                 'dest': str(native.video.with_name('older-episode.mkv')),
                 'download_hash': 'single-episode', 'date': '2026-10-01 00:00:00',
                 'src_fileitem': {'path': source, 'storage': 'local', 'size': 7}})
    native.transfers.get_by_src = Mock(return_value=prior)
    native.prior = prior
    return native


@pytest.mark.parametrize('destination_exists', [False, True])
def test_season_does_not_wait_for_or_reown_previous_episode(overlap, destination_exists):
    if destination_exists:
        Path(overlap.prior.dest).write_bytes(b'earlier')
    current = overlap.host.collect(overlap.job)
    assert {r['local'] for r in current} == {str(overlap.video), str(overlap.subtitle)}
    assert len(overlap.job['download_manifest']) == 3
    assert overlap.job['historical_transfers'] == [{
        'source': overlap.prior.src, 'size': 7, 'history_id': 50,
        'download_hash': 'single-episode', 'local': overlap.prior.dest,
        'date': overlap.prior.date, 'basis': 'mp_source_history'}]
    overlap.transfers.get_by_src.assert_called_once_with(src=overlap.prior.src, storage='local')
    # Once accepted, restarting and removal of MP history cannot change scope.
    overlap.transfers.get_by_src.side_effect = AssertionError('scope is already owned')
    assert overlap.host.collect(json.loads(json.dumps(overlap.job))) == current


@pytest.mark.parametrize('change', ['failed', 'current_hash', 'different_source', 'different_downloader',
                                  'cloud_source', 'cloud_output', 'other_root', 'newer',
                                  'size', 'missing_metadata', 'bad_json', 'metadata_path', 'metadata_storage'])
def test_ambiguous_previous_record_cannot_shrink_scope(overlap, modules, change):
    prior = overlap.prior
    if change == 'failed': prior.status = False
    elif change == 'current_hash': prior.download_hash = overlap.job['download_hash']
    elif change == 'different_source': prior.src = prior.src.replace('Release', 'Different')
    elif change == 'different_downloader': prior.downloader = 'different'
    elif change == 'cloud_source': prior.src_storage = 'u115'
    elif change == 'cloud_output': prior.dest_storage = 'u115'
    elif change == 'other_root': prior.dest = str(Path(overlap.config.local_root).parent / 'outside.mkv')
    elif change == 'newer': prior.id = 999
    elif change == 'size': prior.src_fileitem['size'] += 1
    elif change == 'missing_metadata': prior.src_fileitem = None
    elif change == 'bad_json': prior.src_fileitem = '{'
    elif change == 'metadata_path': prior.src_fileitem['path'] = 'elsewhere.mkv'
    elif change == 'metadata_storage': prior.src_fileitem['storage'] = 'u115'
    with pytest.raises(modules.domain.Awaiting):
        overlap.host.collect(overlap.job)
    assert not overlap.job.get('owned_candidates')
    assert not overlap.job.get('historical_transfers')


def test_failed_current_transfer_does_not_fall_back_to_old_success(overlap, modules):
    overlap.rows.append(NS(**{**vars(overlap.prior), 'id': 103, 'status': False,
                             'download_hash': overlap.job['download_hash']}))
    with pytest.raises(modules.domain.Awaiting):
        overlap.host.collect(overlap.job)
    overlap.transfers.get_by_src.assert_not_called()


def test_partially_hashed_legacy_scope_cannot_drop_history(overlap, modules):
    overlap.job['files'] = [{'local': overlap.prior.dest}]
    with pytest.raises(modules.domain.Awaiting):
        overlap.host.collect(overlap.job)
    overlap.transfers.get_by_src.assert_not_called()


def test_full_cycle_delivers_only_current_files_and_keeps_history_audit(overlap, batch, modules):
    batch.store.observe(instance='SymediaBatchBridge', download_hash='hash', downloader='qb',
        title='season', history_id=101, routing=overlap.config.routing())
    job = next(j for j in batch.store.jobs() if j['download_hash'] == 'hash')
    overlap.host.try_instant = batch.host.try_instant
    engine = modules.engine.Engine(batch.store, overlap.config, overlap.host, batch.cloud, batch.stop)
    engine.process(job)
    saved = batch.store.get(job['id'])
    assert saved['state'] == 'handed_off', saved['message']
    assert len(saved['files']) == 2 and len(saved['historical_transfers']) == 1
    assert saved['history_ids'] == [101, 102]
    assert not any('older-episode' in p for p in batch.cloud.tree(saved['destination']))
    assert any(e['kind'] == 'historical_scope' for e in batch.store.events(job['id']))
    count = len(batch.host.uploads)
    engine.process(saved)
    assert len(batch.host.uploads) == count


def test_detail_separates_historical_files_from_upload_receipts(plugin):
    job = add_job(plugin.p)
    job['historical_transfers'] = [{'source': '/old/E03.mkv', 'local': '/organized/E03.mkv',
                                  'history_id': 5, 'date': '2026-10-01'}]
    plugin.p._store.save(job)
    plugin.p.view_records(plugin.modules.plugin.ViewRequest(key=job['id']))
    page = json.dumps(plugin.p.get_page(), ensure_ascii=False)
    assert '历史整理文件' in page and 'E03.mkv' in page and 'MP 整理记录 5' in page
    assert '不代表已上传或已归档' in page


@pytest.mark.parametrize('error_type', ['awaiting', 'failure', 'unexpected'])
def test_manual_recheck_always_records_real_result_even_if_still_waiting(batch, modules, error_type):
    job = batch.job()
    job.update(retry_requested=1, message='仍在等待', next_check=0)
    batch.store.save(job)
    error = {'awaiting': modules.domain.Awaiting('仍在等待'),
             'failure': modules.domain.BridgeError('仍在等待', review=True),
             'unexpected': RuntimeError('SECRET')}[error_type]
    batch.host.collect = Mock(side_effect=error)
    batch.run()
    saved = batch.job()
    assert 'retry_requested' not in saved
    assert saved['recheck_result']['at'] >= saved['recheck_started_at']
    assert saved['recheck_result']['message'] == saved['message']
    events = batch.store.events(batch.id)
    assert sum(e['kind'] == 'recheck_completed' for e in events) == 1
    assert not any(e['kind'] == 'recovered' for e in events)
    assert 'SECRET' not in json.dumps(events)
    batch.run()
    assert sum(e['kind'] == 'recheck_completed' for e in batch.store.events(batch.id)) == 1


def test_interrupted_recheck_remains_pending_for_restart(batch):
    job = batch.job()
    job['retry_requested'] = 1
    batch.store.save(job)
    batch.stop.set()
    batch.run()
    assert batch.job()['retry_requested'] == 1
    assert not batch.job().get('recheck_result')
