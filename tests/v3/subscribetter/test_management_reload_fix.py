"""Actual init_plugin races; only private storage and AI construction are doubles."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_management as tm


class ReloadFixTests(unittest.TestCase):
    def test_reload_wins_before_ai_management_lock_and_stale_work_is_rejected(self):
        for kind in ('ai_cache','ai_sessions','ai_prompt'):
            for action in ('preview','apply'):
                with self.subTest(kind=kind,action=action):
                    tm.ManagementAPITests.setUpClass();f=tm.ManagementAPITests();f.setUp()
                    try:self.reload_race(f,kind,action)
                    finally:f.doCleanups()

    def test_actual_reload_none_transitions_keep_old_requests_and_replays_scoped(self):
        for old_present,new_present in ((False,True),(True,False)):
            for kind in ('ai_cache','ai_sessions','ai_prompt') if new_present else ('ai_prompt',):
                for action in ('preview','apply'):
                    with self.subTest(old=old_present,new=new_present,kind=kind,action=action):
                        tm.ManagementAPITests.setUpClass();f=tm.ManagementAPITests();f.setUp()
                        try:self.reload_race(f,kind,action,old_present=old_present,new_present=new_present)
                        finally:f.doCleanups()

    def reload_race(self,f,kind,action,*,old_present=True,new_present=True):
        m=import_module(f.mod.__name__+'.ui');v=m.Views(f.plugin)
        user=f.mod.TokenPayload(username='admin',super_user=True);closed=[];clears=[]
        def make_ai(label,generation):
            ai=NS(lock=threading.RLock(),generation=generation,epoch=0,session_epochs={},
                  cache={},bridge_cache={},queue={},chat_queue={},pending={},sessions={})
            def close():
                with ai.lock:closed.append(label)
            def clear(actor):
                with ai.lock:clears.append(label);ai.epoch+=1
            ai.close=close;ai.clear_sessions=clear;ai.clear_cache=clear
            return ai
        old=make_ai('old',f.plugin.generation) if old_present else None
        f.plugin.ai=old;f.plugin.runtime.ai=old
        current=f.plugin.configuration.view()
        req=m.Fence(config_revision=current['revision'],runtime_generation=f.plugin.generation)
        old_preview=v.preview(kind,{},req,user)
        def body(preview,operation):
            return m.Apply(preview_id=preview.preview_id,preview_digest=preview.preview_digest,operation_id=operation,confirm=True)
        preflight=f.plugin.configuration.preview({'ai_assist':{'enabled':new_present,
            'endpoint_ref':f.store.put('https://fiction.invalid'),'credential_refs':[f.store.put('fiction-key')],
            'model':'fiction'}},current['revision'],current['digest'],'admin')
        self.assertTrue(preflight['valid'])
        captured=threading.Event();resume=threading.Event()
        class BeforeAcquire:
            def __init__(self,lock):self.lock=lock
            def __enter__(self):
                # Old code first captures AI; corrected code first takes lifecycle.
                # Neither version holds a lock while this request is parked.
                if threading.current_thread().name.startswith('stale') and not captured.is_set():
                    captured.set()
                    if not resume.wait(3):raise AssertionError('request was not released')
                self.lock.acquire();return self
            def __exit__(self,*args):self.lock.release()
        if old is not None:old.lock=BeforeAcquire(old.lock)
        else:
            # With no AI lock to capture, park the submitted request before admission.
            authorize=v._auth
            def auth(user):
                authorize(user)
                if threading.current_thread().name.startswith('stale') and not captured.is_set():
                    captured.set();self.assertTrue(resume.wait(3))
            v._auth=auth
        f.plugin.runtime_lock=BeforeAcquire(f.plugin.runtime_lock)
        new=make_ai('new',req.runtime_generation+1) if new_present else None
        original_connection=v.repository.connection
        @contextmanager
        def connection(write=False):
            if write and threading.current_thread().name.startswith('stale') and f.plugin.ai is not None:
                self.assertTrue(f.plugin.ai.lock._is_owned(),'write must hold the CURRENT AI lock, never a retired lock')
            with original_connection(write=write) as db:yield db
        with patch.object(v.repository,'connection',connection),ThreadPoolExecutor(max_workers=1,thread_name_prefix='stale') as pool:
            stale=pool.submit(v.preview,kind,{},req,user) if action=='preview' else pool.submit(v.apply,kind,body(old_preview,'stale'),user)
            try:
                self.assertTrue(captured.wait(2))
                with patch.object(f.mod,'SecretStore',lambda path:f.store),patch.object(f.mod,'AIService',lambda *a,**kw:new):
                    f.plugin.init_plugin(preflight['config'])
                self.assertIs(f.plugin.ai,new);self.assertEqual(['old'] if old_present else [],closed)
                self.assertEqual(req.runtime_generation+1,f.plugin.generation)
                # Fresh operations progress while the old request is still parked.
                current=f.plugin.configuration.view()
                fresh_req=m.Fence(config_revision=current['revision'],runtime_generation=f.plugin.generation)
                fresh=v.preview(kind,{},fresh_req,user);fresh_body=body(fresh,'fresh')
                applied=v.apply(kind,fresh_body,user)
                self.assertEqual('APPLIED',applied.state)
                with f.plugin.repository.connection() as db:before='\n'.join(db.iterdump())
                before_clears=list(clears)
            finally:resume.set()
            with self.assertRaises(m.HTTPException) as error:stale.result(timeout=2)
            self.assertEqual(409,error.exception.status_code)
            self.assertEqual('STALE_CONFIGURATION_OR_RUNTIME' if action=='preview' else 'STALE_PREVIEW',error.exception.detail)
        self.assertEqual(before_clears,clears)
        with f.plugin.repository.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))
        # Prompt restoration is only a preflight; its returned native Save works
        # after the management locks have been released. Other kinds reload unchanged config.
        config=applied.result['configuration']['config'] if kind=='ai_prompt' else current['config']
        third=make_ai('third',f.plugin.generation+1) if new_present else None
        with patch.object(f.mod,'SecretStore',lambda path:f.store),patch.object(f.mod,'AIService',lambda *a,**kw:third):
            f.plugin.init_plugin(config)
        self.assertIs(f.plugin.ai,third)
        self.assertEqual(applied,v.apply(kind,fresh_body,user))
        self.assertEqual(before_clears,clears)


if __name__=='__main__':unittest.main()
