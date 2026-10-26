"""Main-content policy excludes explicit extras without fabricating MP receipts."""
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from test_adapters import native
from test_batches import batch


def extra(n, folder='Featurettes', extension='.mkv', progress=1, history=None):
    name='Release/'+folder+'/Obsession - Behind the Scenes'+extension
    source=PurePosixPath(n.rows[0].src).parent / folder / ('Obsession - Behind the Scenes'+extension)
    n.files.append(NS(name=name, size=5, priority=1, progress=progress))
    target=n.video.parent/'Extras'/('Obsession - Behind the Scenes'+extension)
    target.parent.mkdir(exist_ok=True)
    original=Path(str(source));original.parent.mkdir(parents=True,exist_ok=True)
    original.write_bytes(b'extra')
    target.hardlink_to(original)
    if history is not None:
        n.rows.append(NS(**{**vars(n.rows[0]),'id':3,'src':str(source),'dest':str(target),'status':history}))
    return str(source), target


@pytest.mark.parametrize('folder',['Featurettes','Extras','FEATURETTES','Extras/Subs'])
@pytest.mark.parametrize('history',[None,False,True])
def test_explicit_supplementary_folder_cannot_block_verified_main(native,folder,history):
    n=native
    source,target=extra(n,folder,history=history)
    result=n.host.collect(n.job)
    assert {r['local'] for r in result}=={str(n.video),str(n.subtitle)}
    assert n.job['excluded_downloads']==[{'source':source,'size':5,'basis':'supplementary_directory',
        'directory':folder.split('/')[0], 'anchor_source':n.rows[0].src}]
    assert source in n.job['download_manifest'] and target.read_bytes()==b'extra'


def test_unfinished_extras_do_not_delay_the_completed_main(native):
    n=native
    extra(n,progress=0)
    assert len(n.host.collect(n.job))==2


@pytest.mark.parametrize('folder',['Ordinary','Featurettes-old','MyExtras'])
def test_unmatched_video_outside_exact_extra_folders_still_waits(native,modules,folder):
    n=native
    extra(n,folder)
    with pytest.raises(modules.domain.Awaiting,match='等待 MP 整理完成'):
        n.host.collect(n.job)
    assert not n.job.get('owned_candidates')


def test_behind_the_scenes_in_filename_is_not_permission_to_drop(native,modules):
    n=native
    n.files.append(NS(name='Release/Obsession - Behind the Scenes.mkv',size=4,priority=1,progress=1))
    with pytest.raises(modules.domain.Awaiting):
        n.host.collect(n.job)


def test_no_successful_in_scope_main_cannot_authorize_exclusions(native,modules):
    n=native
    extra(n)
    n.rows[0].status=False
    with pytest.raises(modules.domain.Awaiting):
        n.host.collect(n.job)
    assert not n.job.get('owned_candidates')


def test_ordinary_missing_subtitle_still_blocks_even_with_extras(native,modules):
    n=native
    extra(n)
    n.rows.pop(1)
    with pytest.raises(modules.domain.Awaiting,match='video.zh.srt'):
        n.host.collect(n.job)


def test_main_incomplete_still_blocks(native,modules):
    n=native
    extra(n,progress=0)
    n.files[0].progress=.5
    with pytest.raises(modules.domain.Awaiting,match='下载完成'):
        n.host.collect(n.job)


def test_already_hashed_extra_is_not_removed_from_legacy_scope(native):
    n=native
    _,target=extra(n,history=True)
    n.rows[-1].dest=str(target)
    n.files[-1].size=target.stat().st_size
    n.job['files']=[{'local':str(target),'sha1':'a'*40,'receipt':{'fileid':'123'}}]
    result=n.host.collect(n.job)
    assert str(target) in {r['local'] for r in result}
    assert n.job['excluded_downloads']==[]


def test_owned_scope_is_immutable_after_upgrade(native):
    n=native
    n.job['owned_candidates']=[{'local':str(n.video),'accepted_signature':[5,1,1,1,1]}]
    n.host.transfers.list_by_hash=Mock(side_effect=AssertionError('owned scope must not change'))
    assert n.host.collect(n.job)==n.job['owned_candidates']


def test_external_subtitle_under_extras_is_preserved_but_not_reintroduced(native):
    n=native
    target=n.video.parent/'Extras'/'bonus.srt'
    target.parent.mkdir();target.write_bytes(b'bonus subtitles')
    source=str(PurePosixPath(n.rows[0].src).parent/'Extras'/'bonus.srt')
    n.rows.append(NS(**{**vars(n.rows[0]),'id':99,'src':source,'dest':str(target),'status':False}))
    assert len(n.host.collect(n.job))==2
    assert n.job['excluded_transfers'][0]['basis']=='supplementary_directory'
    assert target.exists()


def test_original_release_directory_named_extras_is_not_dropped(native):
    n=native
    for f in n.files:f.name=f.name.replace('Release/','Extras/')
    for r in n.rows:r.src=r.src.replace('Release/','Extras/')
    result=n.host.collect(n.job)
    assert len(result)==2 and n.job['excluded_downloads']==[]


def test_old_waiting_batch_recovers_and_cleanup_preserves_unmanaged_extras(native,batch,modules):
    n=native;b=batch
    extra_source,target=extra(n)
    b.store.observe(instance='SymediaBatchBridge',download_hash='hash',downloader='qb',title='Obsession',
                    history_id=1,routing=n.config.routing(),cleanup_local=True)
    job=next(j for j in b.store.jobs() if j['download_hash']=='hash')
    original=job['id']
    job.update(message='等待 MP 整理完成：Obsession - Behind the Scenes.mkv',
               download_manifest={n.rows[0].src:5,n.rows[1].src:3,extra_source:5})
    # No hashes yet: the old raw selection is retained rather than rewritten.
    b.store.save(job)
    n.host.try_instant=b.host.try_instant
    engine=modules.engine.Engine(b.store,n.config,n.host,b.cloud,b.stop)
    engine.process(job)
    saved=b.store.get(original)
    assert saved['state']=='handed_off',saved['message']
    assert saved['id']==original and len(saved['files'])==2 and len(saved['excluded_downloads'])==1
    assert saved['download_manifest'][extra_source]==5
    assert len(b.host.uploads)==2 and 'Behind the Scenes' not in json.dumps(b.cloud.tree(saved['destination']))
    assert sum(e['kind']=='supplementary_excluded' for e in b.store.events(original))==1
    b.store.save(saved)
    assert sum(e['kind']=='supplementary_excluded' for e in b.store.events(original))==1
    modules.cleanup.cleanup(b.store,saved,n.config,b.stop)
    assert b.store.get(original)['cleanup_done']
    assert target.read_bytes()==Path(extra_source).read_bytes()==b'extra'
    assert target.samefile(extra_source) and not n.video.exists() and not n.subtitle.exists()


def test_excluded_scope_and_owner_survive_restart_and_history_expiry(native,modules):
    n=native
    extra(n)
    result=n.host.collect(n.job)
    saved=json.loads(json.dumps(n.job))
    n.rows.clear()
    n.chain.torrent_files=Mock(side_effect=AssertionError('do not reread expired task'))
    assert n.host.collect(saved)==result
    assert saved['excluded_downloads']==n.job['excluded_downloads']
