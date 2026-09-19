"""Run fixed host method bodies against temporary local files, without importing MP.

This is a source contract, not a running-host acceptance test. DTO/ORM/runtime
injection boundaries are stubs; the public forwarding, native planning/rename,
and native overwrite/intercept/atomic-copy bodies are compiled unchanged from source.
"""
import ast
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from test_planner import load, NOW, PLUGIN


class Model(SimpleNamespace):
    def model_dump(self, **kwargs):return vars(self).copy()
    def to_dict(self):return vars(self).copy()


class InterceptData(Model):
    cancel=False


def methods(path, name, names, globals_):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    source=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    body=[deepcopy(n) for n in source.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in body}==set(names)
    cls=ast.ClassDef(name=name,bases=[],keywords=[],decorator_list=[],body=body)
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),cls],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),globals_)
    return globals_[name]


def run(source):
    paths=[source/'app/chain/base.py',source/'app/modules/filemanager/transhandler.py',source/'app/modules/filemanager/storages/local.py']
    runtime={'RENAME_FORMAT':lambda kind:'unused','DEFAULT_SUB':None,'RMT_SUBEXT':{'.srt','.idx','.sub'}}
    globals_=dict(Path=Path,deepcopy=deepcopy,re=re,cast=cast,Any=Any,FileItem=Model,TransferPlanCheckpoint=Model,TransferPlanItem=Model,MediaType=Model(TV='TV'),get_runtime_setting=runtime.get,
                  os=os,TransferInterceptEventData=InterceptData,ChainEventType=Model(TransferIntercept='intercept'),logger=Model(info=lambda *a:None,error=lambda *a:None,debug=lambda *a:None))
    Chain=methods(paths[0],'ChainBase',{'plan_transfer','transfer'},globals_)
    Handler=methods(paths[1],'TransHandler',{'plan_transfer','__rename_subtitles','__serialize_fileitem','__serialize_transfer_model','__is_extra_file','__resolve_overwrite','__intercept_transfer'},globals_)
    Storage=methods(paths[2],'LocalStorage',{'copy','_write_atomically'},globals_)
    handler=Handler();chain=Chain();storage=Storage()
    native=handler._TransHandler__rename_subtitles
    assert native(Model(name='Show.S01E02.en.srt',extension='srt'),Path('/out/video.srt'))==native(Model(name='Show.S01E02.zh-Hans.srt',extension='srt'),Path('/out/video.srt'))
    calls=[]
    def public_plan(method,**kw):
        assert method=='plan_transfer'
        directory=kw['target_directory']
        return handler.plan_transfer(Model(source_fileitem=kw['fileitem'].model_dump()),meta=kw['meta'],mediainfo=kw['mediainfo'],source_oper=None,target_storage=kw['target_storage'],target_path=kw['target_path'],transfer_type=kw['transfer_type'],need_scrape=False,need_rename=directory.renaming,need_notify=False,overwrite_mode=directory.overwrite_mode,episodes_info=None,preview=False)
    chain.run_module_strict=public_plan
    def copy(src,partial,**kwargs):
        shutil.copyfile(src,partial);calls.append((src,partial));return True
    globals_.update(fsproxy=Model(copy=copy),runtime_stop_state=Model(consume_transfer_stop=lambda *a:False))
    storage._cleanup_stale_partials=lambda directory:None
    storage._partial_path=lambda dest:dest.with_name(dest.name+'.partial')
    storage._LocalStorage__should_show_progress=lambda src,dest:False
    storage.chunk_size=1024
    guard=None;before_intercept=lambda dest:None;admissions=[]
    def send_event(kind,data):
        event=Model(event_data=data)
        if guard:guard.intercept(event)
        return event
    globals_['eventmanager']=Model(send_event=send_event)
    def command(**kw):
        checkpoint=public_plan('plan_transfer',**kw);dest=Path(checkpoint.final_target_path)
        assert checkpoint.items[0].source_fileitem['path']==kw['fileitem'].path
        before_intercept(dest)
        over_flag,_,refusal=handler._TransHandler__resolve_overwrite(fileitem=kw['fileitem'],meta=kw['meta'],mediainfo=kw['mediainfo'],target_oper=None,target_storage='local',target_file=dest,transfer_type='copy',overwrite_mode=kw['target_directory'].overwrite_mode,need_notify=False)
        assert over_flag and refusal is None and kw['target_directory'].overwrite_mode=='never'
        allowed,reason=handler._TransHandler__intercept_transfer(fileitem=kw['fileitem'],meta=kw['meta'],mediainfo=kw['mediainfo'],target_storage='local',target_path=dest,transfer_type='copy',over_flag=over_flag)
        admissions.append(allowed)
        if not allowed:return Model(success=False,message=reason)
        assert storage.copy(kw['fileitem'],dest.parent,dest.name)
        return Model(success=True,target_item=Model(path=str(dest)))
    chain._legacy_transfer_command=command
    meta=lambda path:Model(begin_episode=2,total_episode=1,total_season=1,end_season=None)
    modules={}
    for name,values in {'app.chain.transfer':{'TransferChain':lambda:chain},'app.sdk.media':{'MetaInfoPath':meta},'app.schemas.file':{'FileItem':Model},'app.schemas.system':{'TransferDirectoryConf':Model}}.items():
        module=ModuleType(name);module.__dict__.update(values);modules[name]=module
    execution,repository,candidates=[load(name) for name in ('execution','repository','candidates')]
    names=['Show.S01E02.en.srt','Show.S01E02.zh-Hans.srt','Show.S01E02.zh-Hant.idx','Show.S01E02.zh-Hant.sub','Show.S01E02.en.forced.srt','Show.S01E02.en.SDH.srt']
    files=candidates.bind_files([('Pack/Show.S01E02.mkv',1)]+[('Pack/'+name,1) for name in names],repository.Target('电视剧','tmdb','42',1))
    with tempfile.TemporaryDirectory() as directory,patch.dict(sys.modules,modules):
        root=Path(directory);src=root/'source';src.mkdir();dest=root/'dest';dest.mkdir()
        video=dest/'Real.Native.Video.S01E02.mkv';video.write_bytes(b'v')
        host=execution.HostOrganization(Model(type='TV'));host.video_outputs={0:video};host.transfer_receipt=lambda *a:True
        outputs=[]
        for i,item in enumerate(files[1:]):
            original=src/Path(item['path']).name;original.write_bytes(bytes([i]))
            output=host.transfer(original,dest,item,dict(torrent_files=files,save_path=str(src)))
            assert output.read_bytes()==original.read_bytes() and original.stem in output.stem
            assert calls[-1][0]==original
            outputs.append(output)
        assert len(set(outputs))==len(names) and outputs[2].stem==outputs[3].stem
    # Exercise the same native bodies during a real plugin durable dispatch.
    from test_execution import ExecutionTests
    case=ExecutionTests('test_shuffled_indices_exact_paused_readback_then_resume');case.setUp()
    try:
        with tempfile.TemporaryDirectory(dir=PLUGIN.parents[1]) as directory,patch.dict(sys.modules,modules):
            root=Path(directory);src=root/'source';src.mkdir();dest=root/'dest';dest.mkdir()
            for item in case.files:(src/item['path']).write_bytes(b's'*item['size'])
            layout=src.as_posix()
            if len(layout)>2 and layout[1]==':':layout=layout[2:]
            case.auth.cancel('plan',case.auth.vector([case.keys[1]]),reason='fixture')
            spec=dict(case.spec,save_path=layout)
            case.auth.prepare('local','round',spec,now=NOW);case.auth.claim('local',case.auth.vector([case.keys[1]]),now=NOW)
            task=case.client.task;case.client.task=lambda h:dict(task(h),save_path=layout) if case.client.exists else None
            assert case.executor.execute('local',b'torrent')['state']=='PAUSED_VERIFIED'
            case.client.stats.update({3:200,7:10})
            video=dest/'Native.Video.S01E02.mkv';shutil.copyfile(src/'E02.mkv',video)
            with case.repo.connection(write=True) as db:
                db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?)',('local',1,str(src/'E02.mkv'),str(video),200,case.e.digest(video),'COMPLETE','{}'))
            guard=case.e.TransferGuard(case.repo)
            host=case.e.HostOrganization(Model(type='TV'),repository=case.repo)
            host.video_outputs={1:video};host.history=lambda *a:True;host.transfer_receipt=lambda *a:True
            collisions=[];foreign=b'PREEXISTING-OTHER-WRITER';copies_before=len(calls)
            def before_intercept(output):
                assert not output.exists()
                output.write_bytes(foreign);collisions.append(output)
            result=case.e.Organizer(case.executor,host).organize('local',dest)
            assert result['state']=='UNKNOWN',result
            assert admissions[-1] is False and len(calls)==copies_before
            assert len(collisions)==1 and collisions[0].read_bytes()==foreign
            assert (src/'E02.srt').read_bytes()==b's'*10
            with case.repo.connection() as db:
                assert db.execute("SELECT state FROM organized_assets WHERE plan_id='local' AND file_index=2").fetchone()[0]=='AUTHORIZED'
            assert case.auth.action(result['action_id'])['state']=='UNKNOWN'
    finally:case.doCleanups()
    return dict(contract='unchanged fixed-source public/native method bodies',native_language_collision_reproduced=True,exact_track_copies=len(calls),idx_sub_stem_preserved=True,original_source_bytes_preserved=True,native_atomic_replace_executed=True,preintercept_collision_refused=True,refused_organizer_state=result['state'],source_sha256={str(p.relative_to(source)):sha256(p.read_bytes()).hexdigest() for p in paths})


if __name__=='__main__':
    print(json.dumps(run(Path(sys.argv[1])),ensure_ascii=False,indent=2))
