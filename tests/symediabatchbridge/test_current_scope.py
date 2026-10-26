"""A reused torrent hash must not re-adopt previous download occurrences."""
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS

import pytest

from test_adapters import native, current_task
from test_batches import batch


@pytest.mark.parametrize('extension', ['.mp4', '.srt'])
@pytest.mark.parametrize('exists', [False, True])
@pytest.mark.parametrize('successful', [False, True])
def test_readded_hash_excludes_old_paths_without_inspecting_their_output(native, extension, exists, successful):
    native.host.extensions.add('.mp4')
    for row in native.rows:
        row.id += 100
    old_source = str(PurePosixPath(native.rows[0].src).parent.parent / 'PreviousRelease' / ('old' + extension))
    old_output = native.video.with_name('old' + extension)
    if exists:
        old_output.write_bytes(b'old output is not adopted')
    old = NS(**{**vars(native.rows[0]), 'id':1, 'src':old_source, 'dest':str(old_output),
                'date':'2026-03-26 13:59:29', 'status':successful})
    native.rows.append(old)
    current_task(native)
    # Simulate MP retaining original save paths alongside the new download.
    native.downloads.get_files_by_hash.return_value.append(
        NS(downloader='qb', savepath=str(PurePosixPath(old_source).parent), fullpath=old_source))
    result = native.host.collect(native.job)
    assert {r['local'] for r in result} == {str(native.video), str(native.subtitle)}
    assert native.job['excluded_transfers'] == [{'history_id':1, 'source':old_source,
        'local':str(old_output), 'date':old.date, 'basis':'outside_current_download_scope'}]
    assert old_output.exists() is exists
    native.transfers.list_by_hash.side_effect = AssertionError('accepted scope is durable')
    assert native.host.collect(native.job) == result


def test_unlisted_video_in_current_source_directory_is_not_an_attachment(native):
    old = NS(**{**vars(native.rows[0]), 'id':99,
                'src':str(PurePosixPath(native.rows[0].src).with_name('old.mkv')),
                'dest':str(native.video.with_name('old.mkv'))})
    native.rows.append(old)
    result = native.host.collect(native.job)
    assert len(result) == 2
    assert native.job['excluded_transfers'][0]['history_id'] == 99


@pytest.mark.parametrize('subdirectory', ['', 'Subs'])
@pytest.mark.parametrize('successful', [False, True])
def test_current_external_subtitle_is_still_required(native, modules, subdirectory, successful):
    source = PurePosixPath(native.rows[0].src).parent / subdirectory / 'video.zh.ass'
    target = native.subtitle.with_suffix('.ass')
    target.write_bytes(b'additional subtitle')
    extra = NS(**{**vars(native.rows[0]), 'id':99, 'src':str(source), 'dest':str(target), 'status':successful})
    native.rows.append(extra)
    if not successful:
        with pytest.raises(modules.domain.Awaiting, match='还有整理失败'):
            native.host.collect(native.job)
        assert not native.job.get('owned_candidates')
    else:
        result = native.host.collect(native.job)
        assert {r['local'] for r in result} == {str(native.video),str(native.subtitle),str(target)}
        assert native.job['excluded_transfers'] == []


def test_extra_subtitle_outside_selected_media_directory_is_not_adopted(native):
    # Sharing a prefix or torrent hash is insufficient; exact path ancestry rules.
    extra = NS(**{**vars(native.rows[0]), 'id':99,
        'src':str(PurePosixPath(native.rows[0].src).parent.with_name('Release-old') / 'video.srt'),
        'dest':str(native.subtitle.with_name('old.srt'))})
    native.rows.append(extra)
    assert len(native.host.collect(native.job)) == 2
    assert native.job['excluded_transfers'][0]['history_id'] == 99


def test_existing_owned_scope_and_cached_receipt_are_not_rewritten(native, modules):
    owned = native.host.collect(native.job)
    native.job['files'] = [{'local':str(native.video), 'sha1':'a'*40,
                           'receipt':{'fileid':'123'}, 'instant_next_at':1800003600}]
    original = dict(native.job['files'][0])
    native.rows.clear()
    native.transfers.list_by_hash.side_effect = AssertionError('owned scope must not read rewritten MP history')
    assert native.host.collect(native.job) == owned
    assert native.job['files'][0] == original


def test_review_batch_recovers_in_place_and_logs_excluded_history_once(native, batch, modules):
    job = batch.job()
    original_id = job['id']
    job.update(state='review', message='整理目录可访问，但文件已缺失：old.mkv', next_check=0)
    old = NS(**{**vars(native.rows[0]), 'id':99,
                'src':str(PurePosixPath(native.rows[0].src).parent.parent / 'OldRelease/old.mkv'),
                'dest':str(native.video.with_name('old.mkv'))})
    native.rows.append(old)
    batch.store.save(job)
    native.host.try_instant = batch.host.try_instant
    engine = modules.engine.Engine(batch.store,native.config,native.host,batch.cloud,batch.stop)
    engine.process(job)
    saved = batch.store.get(original_id)
    assert saved['state'] == 'handed_off', saved['message']
    assert saved['id'] == original_id and len(saved['files']) == 2
    assert {f['local'] for f in saved['files']} == {str(native.video),str(native.subtitle)}
    assert not any('old.mkv' in p for p in batch.cloud.tree(saved['destination']))
    events = batch.store.events(original_id)
    assert sum(e['kind'] == 'scope_excluded' for e in events) == 1
    assert any(e['kind'] == 'recovered' for e in events)
    batch.store.save(saved)
    assert sum(e['kind'] == 'scope_excluded' for e in batch.store.events(original_id)) == 1
