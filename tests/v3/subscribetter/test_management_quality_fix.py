"""A3 quality regressions: local SQLite, mounted HTTP, and MockTransport only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
import json
import threading
import unittest
from types import SimpleNamespace as NS
from urllib.parse import quote
from unittest.mock import patch
from pydantic import TypeAdapter

import test_ai as ta
import test_management as tm
from test_planner import load


class QualityFixTests(unittest.TestCase):
    def fixture(self,cls):
        cls.setUpClass();f=cls();f.setUp();self.addCleanup(f.doCleanups)
        return f

    def ai_fixture(self,repo=None,**config):
        f=ta.AITests();f.setUp();self.addCleanup(f.doCleanups)
        if repo is not None:f.repo=repo
        return f,f.runtime(**config)

    def test_session_clear_fences_dequeued_new_route_and_preserves_cache(self):
        f=self.fixture(tm.ManagementTests)
        route=dict(channel='Telegram',source='bot',userid='7',chat_id='99')
        af,ai=self.ai_fixture(f.repo,chat_enabled=True,chat_routes=[route]);f.plugin.ai=ai
        ai.owner_check=lambda *args:af.m.owner_receipt('receipt',*args,fingerprint='a'*64)
        ai.owner_snapshot=lambda *args:dict(fingerprint='a'*64,overlaps=[],unclassified=[])
        ai.cache['keep']=(9999,{});ai.bridge_cache['keep']=(9999,{})
        dequeued=threading.Event();resume=threading.Event();sent=[];original=ai.chat
        def paused(text,scope,**kw):
            if text=='old question?':
                dequeued.set();self.assertTrue(resume.wait(2))
            return original(text,scope,**kw)
        ai.chat=paused
        ai.enqueue_chat(NS(event_data=dict(route,text='old question?')),lambda **kw:sent.append(kw))
        m=load('ui');view=m.Views(f.plugin);user=NS(username='admin')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(ai.drain,None)
            try:
                self.assertTrue(dequeued.wait(2));self.assertFalse(ai.chat_queue)
                key=af.m.digest(route)
                self.assertNotIn(key,ai.sessions);self.assertNotIn(key,ai.session_epochs);self.assertNotIn(key,ai.chat_busy)
                preview=view.sessions_preview(m.Fence(**f.fence()),user=user)
                self.assertEqual('APPLIED',view.apply_sessions(f.apply_body(preview),user=user).state)
            finally:resume.set()
            pending.result(timeout=2)
        self.assertEqual([],af.requests);self.assertEqual([],sent);self.assertFalse(ai.sessions)
        self.assertEqual(0,ai.epoch);self.assertIn('keep',ai.cache);self.assertIn('keep',ai.bridge_cache)
        af.replies.append('new answer')
        ai.enqueue_chat(NS(event_data=dict(route,text='new question?')),lambda **kw:sent.append(kw));ai.drain(None)
        self.assertEqual(1,len(af.requests));self.assertEqual('new answer',sent[0]['title'])
        self.assertEqual(['new question?','new answer'],[x['content'] for x in ai.sessions[af.m.digest(route)]])
        ai.clear_cache('admin');self.assertFalse(ai.cache);self.assertTrue(ai.sessions)

    def test_ai_preview_apply_order_all_kinds_without_database_timeout(self):
        f=self.fixture(tm.ManagementTests);af,ai=self.ai_fixture(f.repo);f.plugin.ai=ai
        m=load('ui');view=m.Views(f.plugin);user=NS(username='admin');request=m.Fence(**f.fence())
        original_connection=f.repo.connection;original_apply=view._apply
        for kind in ('ai_cache','ai_sessions','ai_prompt'):
            for preview_kind in ('ai_cache','ai_sessions','ai_prompt'):
                with self.subTest(apply=kind,preview=preview_kind):
                    preview=view.preview(kind,{},request,user)
                    body=f.apply_body(preview,kind+'-'+preview_kind)
                    apply_entered=threading.Event();preview_entered=threading.Event();release=threading.Event()
                    owned=threading.local();real_lock=threading.RLock()
                    lifecycle_lock=f.plugin.runtime_lock
                    class LifecycleLock:
                        def __enter__(self):
                            if threading.current_thread().name.startswith('preview'):preview_entered.set()
                            lifecycle_lock.acquire();return self
                        def __exit__(self,*args):lifecycle_lock.release()
                    class Lock:
                        def __enter__(self):
                            if threading.current_thread().name.startswith('preview'):preview_entered.set()
                            real_lock.acquire();owned.depth=getattr(owned,'depth',0)+1
                            return self
                        def __exit__(self,*args):
                            owned.depth-=1;real_lock.release()
                    @contextmanager
                    def connection(write=False):
                        if write and threading.current_thread().name.startswith(('preview','apply')):
                            if threading.current_thread().name.startswith('preview'):preview_entered.set()
                            # Fail before BEGIN IMMEDIATE, never wait for SQLite to break a deadlock.
                            self.assertGreater(getattr(owned,'depth',0),0,'AI lock must precede write transaction')
                        with original_connection(write=write) as db:yield db
                    def gated(*args):
                        apply_entered.set();self.assertTrue(release.wait(2));return original_apply(*args)
                    with patch.object(f.plugin,'runtime_lock',LifecycleLock()),patch.object(ai,'lock',Lock()),patch.object(f.repo,'connection',connection),patch.object(view,'_apply',gated):
                        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='apply') as apool,ThreadPoolExecutor(max_workers=1,thread_name_prefix='preview') as ppool:
                            a=apool.submit(view.apply,kind,body,user)
                            try:
                                self.assertTrue(apply_entered.wait(2))
                                p=ppool.submit(view.preview,preview_kind,{},request,user)
                                self.assertTrue(preview_entered.wait(2))
                            finally:release.set()
                            applied=a.result(timeout=2);observed=p.result(timeout=2)
                    self.assertEqual('APPLIED',applied.state)
                    # The competing preview sees post-apply facts and is immediately consumable.
                    out=view.apply(preview_kind,f.apply_body(observed,'next-'+kind+'-'+preview_kind),user)
                    self.assertEqual('APPLIED',out.state)
        self.assertEqual([],af.requests)

    def test_configured_references_and_encoded_slashes_reach_exact_domain_checks(self):
        f=self.fixture(tm.ManagementAPITests);config=load('configuration')
        names=['电影 分类','电影/类别','长'*256,'长'*257]
        value=config.Config.model_validate({'policy':{'bindings':{n:'外语电影' for n in names}},
            'destination_templates':[{'id':'电影 目录/模板','category_id':names[0],
                                     'downloader':'subscriBetter qB test','save_path':'/downloads'}]})
        state=f.plugin.configuration.view();state['config']=value.model_dump()
        f.plugin.repository.setting(f.plugin.configuration.key,state);f.plugin.config=value
        fence=dict(config_revision=state['revision'],runtime_generation=f.plugin.generation)
        with f.plugin.repository.connection(write=True) as db:
            db.execute('INSERT INTO reconcile_checkpoints VALUES(?,?)',('local:规则/电影',json.dumps({'directories':['entry']})))
        for name in names:
            with self.subTest(category=name):
                response=f.client.get('/policies/'+quote(name,safe=''),headers=f.headers)
                self.assertEqual(200,response.status_code,response.text)
                self.assertEqual(name,response.json()['category_id'])
                response=f.client.get('/policies',params={'category_id':name},headers=f.headers)
                self.assertEqual(200,response.status_code,response.text);self.assertEqual(1,response.json()['total'])
        response=f.client.get('/health/local-scans/'+quote('规则/电影',safe='')+'/directories',headers=f.headers)
        self.assertEqual(200,response.status_code,response.text);self.assertEqual(1,response.json()['total'])
        runtime=load('runtime');execution=import_module(f.mod.__name__+'.execution');m=import_module(f.mod.__name__+'.ui')
        service='subscriBetter Emby test';library='媒体库/电影';mapping='映射/电影';calls=[];errors=[]
        mappings=load('archive').Mappings([dict(id=mapping,revision='1',emby_service=service,library_id=library,
            cloud_scope_id='cloud',local_strm_prefix=str(f.plugin.data_path),emby_prefix='/emby',playback_prefix='/play',cd2_prefix='/cd2')])
        archive=NS(mappings=mappings,reconcile=lambda *args,**kw:(calls.append(args) or dict(state='COMPLETE')))
        r=NS(lock=threading.RLock(),busy=False,config=value,check=lambda:None,candidates=NS(),
             stages=runtime.OwnedStages(lambda:True),reason=lambda e:(errors.append(repr(e)) or runtime.Runtime.reason(e)),delivery=NS(archive=archive),clients={},
             authority=NS(plan=lambda pid:dict(snapshot={'downloader':'subscriBetter qB test','infohash':'a'*40})))
        f.plugin.runtime=r
        with patch.object(m.Views,'_one',return_value={'id':'persisted'}),patch.object(execution.StrictExecutor,'reconcile',return_value={'state':'OBSERVED'}) as reconcile:
            for downloader,status in [('subscriBetter qB test',200),('下载器/一',200),('other/下载器',409)]:
                if status==200:r.authority.plan=lambda pid,name=downloader:dict(snapshot={'downloader':name,'infohash':'a'*40})
                response=f.client.post('/downloads/'+quote(downloader,safe='')+'/'+'a'*40+'/reconcile',headers=f.headers,json=dict(fence,plan_id='plan'))
                self.assertEqual(status,response.status_code,(response.text,errors))
            self.assertEqual(2,reconcile.call_count)
            response=f.client.post('/archive/refresh',headers=f.headers,json=dict(fence,service=service,library=library,target_keys=['unit']))
            self.assertEqual(200,response.status_code,response.text);self.assertEqual([(service,library)],calls)
            response=f.client.post('/archive/refresh',headers=f.headers,json=dict(fence,service=service+'?',library=library,target_keys=['unit']))
            self.assertEqual(409,response.status_code,response.text);self.assertEqual(1,len(calls))

    def test_reference_bounds_follow_defining_types_and_receipts_stay_strict(self):
        f=self.fixture(tm.ManagementTests);m=load('ui');fence=f.fence()
        name='名/ '+'x'*253
        self.assertEqual(256,len(name))
        settings=m.SettingsPreview(**fence,generation=1,opportunity_id='round',target_keys=['unit'],destination_template=name)
        self.assertEqual(name,settings.destination_template)
        self.assertEqual(name,m.MappingTest(**fence,scan_id='scan',item_id=name,mapping_id=name).mapping_id)
        self.assertEqual(name,m.ArchiveRefresh(**fence,service=name,library=name,target_keys=['unit']).library)
        sample='s'+'a'*255
        self.assertEqual(sample,m.Simulation(**fence,category_id='电影/类别',candidate={},sample_key=sample).sample_key)
        source=TypeAdapter(m.SourceId)
        self.assertEqual('s'+'a'*63,source.validate_python('s'+'a'*63))
        for key in ['s'*65,'中文','a/b','a:b','_invalid']:
            with self.subTest(source=key),self.assertRaises(ValueError):source.validate_python(key)
        for payload in [dict(settings.model_dump(),destination_template=name+'x'),
                        dict(m.MappingTest(**fence,scan_id='scan',item_id='item',mapping_id='map').model_dump(),mapping_id=name+'x')]:
            with self.assertRaises(ValueError):(m.SettingsPreview if 'generation' in payload else m.MappingTest).model_validate(payload)
        for key in ['a'*257,'invalid/sample','中文']:
            with self.subTest(sample=key),self.assertRaises(ValueError):m.Simulation(**fence,category_id='movie',candidate={},sample_key=key)
        for key in ['space receipt','中文','a/b','a'*129]:
            with self.subTest(receipt=key),self.assertRaises(ValueError):m.Apply(preview_id=key,preview_digest='a'*64,operation_id='op',confirm=True)


if __name__=='__main__':unittest.main()
