"""Bounded management contracts against real disposable SQLite, no host IO."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from test_planner import load
from test_configuration import PrivateFixture


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_ownership import PluginTests
        PluginTests.setUpClass()

    def setUp(self):
        self.r=load('repository');self.c=load('configuration')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=self.r.Repository(Path(self.tmp.name)/'state.db')
        self.config=self.c.Configuration(self.repo,'SubscriBetter',PrivateFixture(),lambda x:None)
        self.config.initialize({})
        self.plugin=SimpleNamespace(repository=self.repo,configuration=self.config,generation=4,runtime=None,runtime_lock=threading.RLock(),
            config=self.c.Config.model_validate(self.config.view()['config']),ai=None,errors=[],ai_errors=[],
            _authorize=lambda u:None,_ordinary_work_active=lambda:False,lifecycle_active=True)

    def test_server_page_and_stable_huge_ids_and_failure(self):
        ui=load('ui').Views(self.plugin)
        for i in range(57):self.repo.submit(str(i),self.r.Target('电影','themoviedb',str(10**24+i)),{'name':'x'},'test')
        one=ui.tasks(limit=25,offset=0,state=None,media_type=None,sort='id',user=None)
        two=ui.tasks(limit=25,offset=25,state=None,media_type=None,sort='id',user=None)
        self.assertEqual((57,25,True),(one.total,one.next_offset,one.truncated))
        self.assertEqual(str(10**24),one.items[0].media_id)
        self.assertFalse({v.id for v in one.items}&{v.id for v in two.items})
        self.assertEqual(4,one.snapshot.runtime_generation)
        self.repo.path=Path(self.tmp.name)/'missing'/'state.db'
        with self.assertRaises(Exception) as error:ui.tasks(limit=25,offset=0,state=None,media_type=None,sort='id',user=None)
        self.assertEqual(503,error.exception.status_code)

    def test_history_preview_stale_idempotent_and_visibility_independent_metrics(self):
        ui=load('ui').Views(self.plugin);now=self.r.utcnow()
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','OK','',0,0,now))
            db.execute('INSERT INTO discovery_records VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',('s','item','r','{}','READY','',0,0,'f','{"identity":{"id":"999"}}',1,now,now))
        before=load('discovery').DiscoveryService.statistics(SimpleNamespace(repository=self.repo))
        request=load('ui').HistoryPreview(record_ids=[1],config_revision=self.config.view()['revision'],runtime_generation=4)
        receipt=ui.history_preview(request,user=SimpleNamespace(username='admin'))
        apply=load('ui').Apply(preview_id=receipt.preview_id,preview_digest=receipt.preview_digest,operation_id='op',confirm=True)
        result=ui.apply_history(apply,user=SimpleNamespace(username='admin'))
        self.assertEqual(result,ui.apply_history(apply,user=SimpleNamespace(username='admin')))
        self.assertEqual(before,load('discovery').DiscoveryService.statistics(SimpleNamespace(repository=self.repo)))
        with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT visible FROM discovery_records').fetchone()[0])

    def test_immutable_sanitized_decision_survives_reload(self):
        e=load('evidence');raw={'title':'<b>film</b> https://site.invalid/?passkey=SENTINEL','cookie':'SENTINEL'}
        ref=e.append(self.repo,'site:1:2',['unit'],{'plans':[],'status':'REJECT','reason':'IDENTITY'},observed=raw)
        e.append(self.repo,'site:1:2',['unit'],{'plans':[],'status':'DEFER','reason':'META'},observed=raw)
        with self.repo.connection() as db:
            row=db.execute('SELECT * FROM candidate_decisions WHERE id=?',(ref['decision_id'],)).fetchone()
            self.assertEqual('REJECT',row['status']);self.assertNotIn('SENTINEL',row['data']);self.assertNotIn('<b>',row['data'])
            self.assertEqual(2,db.execute('SELECT count(*) FROM candidate_decisions').fetchone()[0])

    def fixture(self,cls):
        cls.setUpClass();f=cls();f.setUp();self.addCleanup(f.doCleanups)
        self.repo=f.repo;self.plugin.repository=f.repo
        self.config=self.c.Configuration(self.repo,'SubscriBetter',PrivateFixture(),lambda x:None);self.config.initialize({})
        self.plugin.configuration=self.config
        return f

    def test_decision_joins_actual_plan_id_and_digest_without_raw_file_table(self):
        from test_planner import AuthorityTests,NOW
        f=self.fixture(AuthorityTests);snapshot=f.spec()
        ref=load('evidence').append(f.repo,'A',f.keys,{'plans':[snapshot]},task_id=f.task_id,opportunity_id='round')
        snapshot.update(ref);f.auth.prepare('actual-plan','round',snapshot,now=NOW)
        page=load('ui').Views(self.plugin).decision_plans(ref['decision_id'],limit=1,offset=0,user=None)
        self.assertEqual(1,page.total);self.assertEqual('actual-plan',page.items[0].id)
        self.assertEqual(ref['decision_digest'],page.items[0].data['snapshot']['decision_digest'])
        self.assertEqual('PREPARED',page.items[0].data['authorization'])
        self.assertNotIn('torrent_files',page.items[0].data['snapshot'])

    def fence(self):return dict(config_revision=self.config.view()['revision'],runtime_generation=4)

    def apply_body(self,p,op='op'):
        return load('ui').Apply(preview_id=p.preview_id,preview_digest=p.preview_digest,operation_id=op,confirm=True)

    def test_cancel_never_cleans_and_cleanup_permissions_are_independent(self):
        from test_delivery import DeliveryTests
        f=self.fixture(DeliveryTests);bid=f.prepared();m=load('ui');user=SimpleNamespace(username='admin')
        self.plugin.config=self.plugin.config.model_copy(update={'delivery':{'rules':list(f.worker.rules.values())}})
        self.plugin.runtime=SimpleNamespace(lock=threading.RLock(),busy=False,stages=load('runtime').OwnedStages(lambda:True),scope_worker=lambda identity:f.worker,authority=f.auth,check=lambda:None)
        view=m.Views(self.plugin);before={p.name:p.read_bytes() for p in f.local.iterdir()}
        p=view.cancel_preview(bid,m.BundlePreview(**self.fence(),revision=f.worker.bundle(bid)['revision']),user=user)
        self.assertEqual([],p.blockers)
        result=view.apply_cancel(bid,self.apply_body(p),user=user)
        self.assertEqual('APPLIED',result.state);self.assertEqual('ABANDONED',f.worker.bundle(bid)['state'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in f.local.iterdir()});self.assertEqual([],f.cloud.calls)
        self.assertEqual(result,view.apply_cancel(bid,self.apply_body(p),user=user))
        permissions={'monitor':'cleanup_abandoned','staging':'cleanup_staging','downloader_task':'remove_downloader_task_enabled','downloader_data':'delete_downloader_data_enabled'}
        for scope,permission in permissions.items():
            p=view.cleanup_preview(bid,m.CleanupPreview(**self.fence(),revision=f.worker.bundle(bid)['revision'],scope=scope),user=user)
            self.assertFalse(p.permissions[permission]);self.assertIn('CLEANUP_PERMISSION_DISABLED',p.blockers)
            with self.assertRaises(Exception) as error:view.apply_cleanup(bid,self.apply_body(p,scope),user=user)
            self.assertEqual(409,error.exception.status_code)
        self.assertEqual(before,{p.name:p.read_bytes() for p in f.local.iterdir()})
        self.plugin.config.permissions.cleanup_abandoned=True
        self.plugin.config.delivery['rules'][0]['cleanup_abandoned']=True
        p=view.cleanup_preview(bid,m.CleanupPreview(**self.fence(),revision=f.worker.bundle(bid)['revision'],scope='monitor'),user=user)
        self.assertTrue(p.permissions['cleanup_abandoned'])
        p=view.cleanup_preview(bid,m.CleanupPreview(**self.fence(),revision=f.worker.bundle(bid)['revision'],scope='downloader_data'),user=user)
        self.assertFalse(p.permissions['delete_downloader_data_enabled'])

    def test_logical_archive_invalidation_preserves_file_and_no_exclusion(self):
        from test_archive import ArchiveTests
        f=self.fixture(ArchiveTests);f.archive.reconcile('test','10')
        with f.repo.connection() as db:vid=db.execute('SELECT id FROM archive_versions').fetchone()[0]
        self.plugin.delivery_worker=SimpleNamespace(archive=f.archive)
        view=load('ui').Views(self.plugin);user=SimpleNamespace(username='admin')
        before={p.name:p.read_bytes() for p in f.strms.iterdir()};calls=list(f.sources.calls)
        detail=view.version(vid,limit=1,offset=0,user=user)
        self.assertEqual(vid,detail.version.id);self.assertEqual(calls,f.sources.calls)
        p=view.invalidate_preview(load('ui').InvalidatePreview(**self.fence(),version_ids=[vid],reason='wrong metadata'),user=user)
        result=view.apply_archive(self.apply_body(p),user=user)
        self.assertEqual('APPLIED',result.state)
        with f.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT active FROM archive_versions WHERE id=?',(vid,)).fetchone()[0])
            self.assertEqual(0,db.execute('SELECT count(*) FROM exclusions').fetchone()[0])
            self.assertEqual('INVALID',db.execute('SELECT state FROM archive_targets WHERE target_key=?',(f.key,)).fetchone()[0])
        self.assertEqual(before,{p.name:p.read_bytes() for p in f.strms.iterdir()});self.assertEqual(calls,f.sources.calls)

    def test_ai_clear_fences_and_prompt_restore_use_no_model_or_chat(self):
        from test_ai import AITests
        f=AITests();f.setUp();self.addCleanup(f.doCleanups);ai=f.runtime();self.plugin.ai=ai
        m=load('ui');view=m.Views(self.plugin);user=SimpleNamespace(username='admin');request=m.Fence(**self.fence())
        ai.cache['old']=(0,{'name':'expired'});ai.sessions['session']=[{'role':'user','content':'hello'}]
        ai.stats();self.assertIn('old',ai.cache)
        p=view.cache_preview(request,user=user);ai.cache['new']=(0,{})
        with self.assertRaises(Exception) as error:view.apply_cache(self.apply_body(p),user=user)
        self.assertEqual(409,error.exception.status_code)
        p=view.cache_preview(request,user=user);self.assertEqual('APPLIED',view.apply_cache(self.apply_body(p),user=user).state)
        self.assertFalse(ai.cache);self.assertTrue(ai.sessions)
        p=view.sessions_preview(request,user=user);self.assertEqual('APPLIED',view.apply_sessions(self.apply_body(p,'sessions'),user=user).state)
        self.assertFalse(ai.sessions);self.assertEqual(1,ai.session_epochs['session'])
        p=view.prompt_preview(request,user=user);out=view.apply_prompt(self.apply_body(p,'prompt'),user=user)
        self.assertTrue(out.result['native_save_required']);self.assertTrue(out.result['configuration']['valid'])
        self.assertEqual(load('ai').DEFAULT_PROMPT,out.result['configuration']['config']['ai_assist']['prompt'])
        self.assertEqual([],f.requests)

    def test_settings_preserve_clocks_budget_and_stop_has_no_resume(self):
        from test_runtime import CommonAdmissionTests
        f=CommonAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        task=f.runtime.submit('manual',f.target,{'name':'Fiction'},'admin')
        with f.repo.connection() as db:original=dict(db.execute('SELECT * FROM opportunities').fetchone())
        saved=f.repo.setting('runtime-input:'+original['id'])
        request=dict(task_id=task['id'],generation=task['generation'],opportunity_id=original['id'],target_keys=saved['scope']['units'],destination_template='movie-destination',locks={})
        with f.repo.connection(write=True) as db:result=f.runtime.settings(request,db=db,actor='admin')
        self.assertEqual(task['generation']+1,result['generation'])
        with f.repo.connection() as db:after=dict(db.execute('SELECT * FROM opportunities').fetchone())
        for key in ('created_at','config','failures','supersessions'):self.assertEqual(original[key],after[key])
        self.assertEqual(saved['effective']['schedule'],f.repo.setting('runtime-input:'+original['id'])['effective']['schedule'])
        request['generation']=result['generation'];request['target_keys']=['outside']
        with self.assertRaisesRegex(ValueError,'TARGET_EXPANSION_FORBIDDEN'):
            with f.repo.connection(write=True) as db:f.runtime.settings(request,db=db,actor='admin')
        f.repo.set_state(task['id'],'STOPPED','admin')
        request['target_keys']=saved['scope']['units']
        with self.assertRaisesRegex(ValueError,'TASK_GENERATION_CHANGED'):
            with f.repo.connection(write=True) as db:f.runtime.settings(request,db=db,actor='admin')

    def test_discovery_add_readback_handoff_and_each_tv_generation(self):
        from test_planner import AuthorityTests,NOW
        f=self.fixture(AuthorityTests);vector=f.claim();now=self.r.utcnow()
        target=self.r.Target.from_task(f.repo.get_task(f.task_id)).key
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','OK','',0,0,now))
            db.execute('INSERT INTO discovery_records VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',('s','item','r','{}','SUBMITTED','',0,0,'filter','{"identity":{"id":"00042"}}',1,now,now))
            db.execute('INSERT INTO discovery_targets VALUES(?,?,?,?,?,?,?,?,?,?)',(1,target,'intent','digest',f.task_id,0,'specials','SUBMITTED','','receipt'))
        stats=lambda:load('discovery').DiscoveryService.statistics(SimpleNamespace(repository=f.repo))
        self.assertEqual(0,stats()['stages']['download_acceptance']['numerator'])
        f.auth.begin_attempt('add','A',vector,'ADD',[0,1],{},now=NOW);f.auth.record_result('add','SUCCEEDED',{'readback':True},now=NOW)
        self.assertEqual(0,stats()['stages']['download_acceptance']['numerator'])
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO managed_downloads VALUES(?,?,?,?,?,?,?,?,?,?)',('isolated','a'*40,'/test',json.dumps(f.spec()['torrent_files']),'managed','A','client-id','RUNNING','{}',now))
        self.assertEqual(1,stats()['stages']['download_acceptance']['numerator'])
        with f.repo.connection(write=True) as db:db.execute("UPDATE plan_actions SET kind='DOWNLOAD' WHERE id='add'")
        self.assertEqual(0,stats()['stages']['download_acceptance']['numerator'])
        with f.repo.connection(write=True) as db:db.execute("UPDATE plan_actions SET kind='ADD' WHERE id='add'")
        f.auth.set_transfer_phase('A',vector,'READY_TO_PUBLISH')
        f.publish('A',vector,[0,1]);f.auth.record_result('publish','HANDED_OFF',{'destination':'owned'},now=NOW)
        stage=stats()['stages'];self.assertEqual(1,stage['delivery_completion']['numerator']);self.assertEqual(0,stage['ingest']['numerator'])
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO ingest_receipts VALUES(?,?,?,?,?,?,?)',('i1','A',f.keys[0],vector[f.keys[0]]['generation'],'v1','{}',now))
        self.assertEqual(0,stats()['stages']['ingest']['numerator'])

        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO ingest_receipts VALUES(?,?,?,?,?,?,?)',('i2','A',f.keys[1],vector[f.keys[1]]['generation'],'v2','{}',now))
        before=stats();self.assertEqual(1,before['stages']['ingest']['numerator'])
        with f.repo.connection(write=True) as db:db.execute('UPDATE discovery_records SET visible=0')
        self.assertEqual(before,stats())
        with f.repo.connection(write=True) as db:db.execute('UPDATE target_units SET generation=generation+1 WHERE target_key=?',(f.keys[1],))
        self.assertEqual(0,stats()['stages']['ingest']['numerator'])

    def test_immediate_common_runtime_waits_for_inventory_and_cannot_revive_stop(self):
        import asyncio
        from test_runtime import CommonAdmissionTests
        f=CommonAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        task=f.runtime.submit('manual',f.target,{'name':'Fiction'},'admin')
        f.plugin.configuration=self.config;f.plugin._authorize=lambda user:None;f.plugin.runtime=f.runtime
        with f.repo.connection() as db:op=dict(db.execute('SELECT * FROM opportunities').fetchone())
        checks=[]
        def inventory(*args,**kwargs):checks.append('inventory');return {'state':'UNKNOWN','diagnostics':['MAPPING_UNVERIFIED']}
        f.runtime.inventory=inventory
        m=load('ui');view=m.Views(f.plugin)
        request=m.Immediate(config_revision=self.config.view()['revision'],runtime_generation=1,generation=task['generation'],opportunity_id=op['id'],target_keys=json.loads(op['scope']),operation_id='immediate')
        result=asyncio.run(view.immediate(task['id'],request,user=SimpleNamespace(username='admin')))
        self.assertEqual('WAIT_INVENTORY',result.state);self.assertEqual(['inventory'],checks)
        self.assertEqual(result,asyncio.run(view.immediate(task['id'],request,user=SimpleNamespace(username='admin'))))
        self.assertEqual(['inventory'],checks)
        f.repo.set_state(task['id'],'STOPPED','admin')
        with self.assertRaises(Exception) as error:asyncio.run(view.immediate(task['id'],request.model_copy(update={'operation_id':'new'}),user=SimpleNamespace(username='admin')))
        self.assertEqual(409,error.exception.status_code);self.assertEqual('STOPPED',f.repo.get_task(task['id'])['state'])
    def test_unknown_retry_and_shared_reference_never_dispatch(self):
        import asyncio
        from test_delivery import DeliveryTests
        f=self.fixture(DeliveryTests);bid=f.prepared();m=load('ui');user=SimpleNamespace(username='admin')
        self.plugin.config=self.plugin.config.model_copy(update={'delivery':{'rules':list(f.worker.rules.values())}})
        runtime=SimpleNamespace(lock=threading.RLock(),busy=False,stages=load('runtime').OwnedStages(lambda:True),scope_worker=lambda identity:f.worker,
            authority=f.auth,check=lambda:None,config=self.plugin.config,candidates=SimpleNamespace(),reason=load('runtime').Runtime.reason)
        self.plugin.runtime=runtime;view=m.Views(self.plugin)
        bundle=f.worker.bundle(bid);bundle['files'][0]['state']='UNKNOWN';f.worker._save(bundle)
        request=m.Retry(**self.fence(),revision=f.worker.bundle(bid)['revision'],phase='transfer',expected_state=f.worker.bundle(bid)['state'],operation_id='unknown')
        with self.assertRaises(Exception) as error:asyncio.run(view.retry(bid,request,user=user))
        self.assertEqual(409,error.exception.status_code);self.assertEqual([],f.cloud.calls)
        snapshot=f.auth.plan('A')['snapshot'];f.auth.prepare('B','round',snapshot)
        p=view.cancel_preview(bid,m.BundlePreview(**self.fence(),revision=f.worker.bundle(bid)['revision']),user=user)
        self.assertIn('SHARED_REFERENCE',p.blockers)
        with self.assertRaises(Exception) as error:view.apply_cancel(bid,self.apply_body(p),user=user)
        self.assertEqual(409,error.exception.status_code);self.assertEqual([],f.cloud.calls)


class ManagementAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_configuration import ConfigurationAPITests
        ConfigurationAPITests.setUpClass();cls.mod=ConfigurationAPITests.mod

    def setUp(self):
        from test_configuration import ConfigurationAPITests
        ConfigurationAPITests.setUp(self)

    def test_actual_mount_all_read_views_auth_bounds_no_writes(self):
        urls=['/tasks','/candidates','/candidate-decisions','/archive/targets','/delivery/bundles','/policies','/discovery/sources','/discovery/records','/parse/samples','/ai','/diagnostics','/discovery/catalog','/discovery/statistics','/migration/owners','/exclusions','/health/runtime-records',*['/health/records/'+s for s in ('local_scans','archive_scans','downloads','operations','migration')]]
        with self.plugin.repository.connection() as db:before='\n'.join(db.iterdump())
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(401,self.client.get(url).status_code)
                r=self.client.get(url,headers=self.headers)
                self.assertEqual(200,r.status_code,r.text)
                if 'items' in r.json():self.assertEqual({'items','total','next_offset','truncated','snapshot'},set(r.json()))
        with self.plugin.repository.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))
        for url in ('/tasks?limit=101','/tasks?offset=-1','/tasks?sort=sql','/candidates?limit=101'):
            self.assertEqual(422,self.client.get(url,headers=self.headers).status_code)
        response=self.client.post('/ai/cache/clear/preview',headers=self.headers,json={'config_revision':0,'runtime_generation':1,'secret':'SENTINEL'})
        self.assertEqual(422,response.status_code);self.assertNotIn('SENTINEL',response.text)
        self.assertEqual(409,self.client.post('/discovery/history/cleanup',headers=self.headers,json={'record_ids':[1]}).status_code)
        self.client.app.dependency_overrides[self.mod.verify_token]=lambda:self.mod.TokenPayload(username='reader',super_user=False)
        self.assertEqual(403,self.client.get('/tasks',headers=self.headers).status_code)
        self.client.app.dependency_overrides.clear()

    def test_large_scan_checkpoint_summary_and_entry_pages_are_bounded(self):
        repo=self.plugin.repository
        with repo.connection(write=True) as db:db.execute('INSERT INTO reconcile_checkpoints VALUES(?,?)',('local:fixture',json.dumps({'state':'INCOMPLETE','directories':[{'path':'/private/root/'+str(i),'offset':i} for i in range(10001)],'stack':[],'failed_paths':['/private/root/bad'],'count':10001})))
        response=self.client.get('/health/records/local_scans',headers=self.headers)
        self.assertEqual(200,response.status_code,response.text);self.assertLess(len(response.content),2000)
        response=self.client.get('/health/local-scans/fixture/directories?limit=25&offset=25',headers=self.headers)
        self.assertEqual(200,response.status_code,response.text);page=response.json()
        self.assertEqual((10001,25,50,True),(page['total'],len(page['items']),page['next_offset'],page['truncated']))
        self.assertNotIn('/private/root',response.text)

    def test_actual_preview_exact_body_stale_and_duplicate_operation(self):
        repo=self.plugin.repository;now=load('repository').utcnow()
        with repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','OK','',0,0,now))
            for n in range(1,4):db.execute('INSERT INTO discovery_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(n,'s',str(n),'r','{}','READY','',0,0,'f','{}',1,now,now))
        fence=dict(config_revision=self.plugin.configuration.view()['revision'],runtime_generation=self.plugin.generation)
        def preview(ids):
            r=self.client.post('/discovery/history/cleanup/preview',headers=self.headers,json=dict(fence,record_ids=ids))
            self.assertEqual(200,r.status_code,r.text);return r.json()
        p=preview([1]);body=dict(preview_id=p['preview_id'],preview_digest=p['preview_digest'],operation_id='op',confirm=True)
        self.assertEqual(422,self.client.post('/discovery/history/cleanup/apply',headers=self.headers,json=dict(body,record_ids=[1,2])).status_code)
        with repo.connection(write=True) as db:db.execute("UPDATE discovery_records SET raw_revision='new' WHERE id=1")
        self.assertEqual(409,self.client.post('/discovery/history/cleanup/apply',headers=self.headers,json=body).status_code)
        p=preview([1]);body.update(preview_id=p['preview_id'],preview_digest=p['preview_digest'])
        r=self.client.post('/discovery/history/cleanup/apply',headers=self.headers,json=body)
        self.assertEqual(200,r.status_code,r.text)
        self.assertEqual(r.json(),self.client.post('/discovery/history/cleanup/apply',headers=self.headers,json=body).json())
        self.assertEqual(r.json(),self.client.get('/management/operations/op',headers=self.headers).json())
        self.assertEqual(p,self.client.get('/management/previews/'+p['preview_id'],headers=self.headers).json())
        with repo.connection() as db:self.assertEqual([0,1,1],[r[0] for r in db.execute('SELECT visible FROM discovery_records ORDER BY id')])


if __name__=='__main__':unittest.main()
