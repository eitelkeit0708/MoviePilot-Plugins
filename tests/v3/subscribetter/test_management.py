"""Bounded management contracts against real disposable SQLite, no host IO."""
import json
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from test_planner import load, NOW
from test_configuration import PrivateFixture


def runtime_fixture(plugin,worker):
    """Use real runtime authority checks without constructing external services."""
    module=load('runtime');runtime=object.__new__(module.Runtime)
    runtime.plugin=plugin;runtime.generation=plugin.generation
    runtime.lock=threading.RLock();runtime.busy=False;runtime._io=threading.local()
    runtime.stages=module.OwnedStages(lambda:None);runtime.deadline=None
    runtime.scope_worker=lambda identity:worker;runtime.authority=worker.authority
    return runtime


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

    def test_connection_probe_reuses_provider_and_distinguishes_cache_without_returning_model_content(self):
        import asyncio
        ui=load('ui');views=ui.Views(self.plugin);calls=[]
        request=ui.Fence(config_revision=self.config.view()['revision'],runtime_generation=4)
        with self.assertRaises(Exception) as error:asyncio.run(views.ai_probe(request,user=None))
        self.assertEqual(409,error.exception.status_code)
        result=SimpleNamespace(identity={'name':'PRIVATE_MODEL_OUTPUT'},source='api',attempts=1,reason='accepted',elapsed_ms=25)
        self.plugin.ai=SimpleNamespace(live=lambda:True,extract=lambda *a,**k:(calls.append((a,k)) or result))
        answer=asyncio.run(views.ai_probe(request,user=None))
        self.assertEqual('MODEL_RESPONDED',answer.state)
        self.assertNotIn('PRIVATE_MODEL_OUTPUT',answer.model_dump_json())
        self.assertEqual('ui-connection-probe-v1',calls[0][1]['parser_revision'])
        result.source='cache';result.attempts=0
        self.assertEqual('CACHED',asyncio.run(views.ai_probe(request,user=None)).state)

    def test_mapping_check_reads_real_strm_while_ordinary_work_is_off_and_keeps_database_unchanged(self):
        import asyncio
        ui=load('ui');archive=load('archive');root=Path(self.tmp.name)/'strm';root.mkdir()
        (root/'test.strm').write_text('/playback/media/test.mkv',encoding='utf-8')
        rule=dict(id='map',revision='r',emby_service='emby',library_id='library',cloud_scope_id='test',local_strm_prefix=str(root),emby_prefix='/emby',playback_prefix='/playback',cd2_prefix='/115')
        raw=dict(Name='真实样本',Path='/emby/test.strm',MediaSources=[dict(Path='/emby/test.strm')],Password='PRIVATE_SENTINEL')
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO archive_scans VALUES('scan','emby','library','COMPLETE','{}')")
            db.execute('INSERT INTO archive_scan_items VALUES(?,?,?,NULL)',('scan','item',json.dumps(raw)))
        class Stages:
            async def run(self,fn):return fn()
        self.plugin.runtime=SimpleNamespace(lock=threading.RLock(),busy=False,config=self.plugin.config,candidates=SimpleNamespace(deadline=None),stages=Stages(),delivery=SimpleNamespace(archive=SimpleNamespace(mappings=archive.Mappings([rule]))),check=lambda:self.fail('ordinary execution must remain off'))
        views=ui.Views(self.plugin)
        rows=views.archive_scan_items('scan',user=None)
        self.assertEqual('真实样本',rows.items[0].data['name']);self.assertNotIn('PRIVATE_SENTINEL',rows.model_dump_json())
        with self.repo.connection() as db:before=list(db.iterdump())
        answer=asyncio.run(views.mapping_test(ui.MappingTest(config_revision=self.config.view()['revision'],runtime_generation=4,scan_id='scan',item_id='item',mapping_id='map'),user=None))
        self.assertEqual('MAPPING_VERIFIED',answer.state)
        self.assertEqual('/115/media/test.mkv',answer.result['locations'][0]['cd2_path'])
        with self.repo.connection() as db:self.assertEqual(before,list(db.iterdump()))

    def test_tasks_expose_media_names_and_filter_without_exporting_snapshot(self):
        ui=load('ui').Views(self.plugin)
        row=self.repo.submit('human',self.r.Target('电视剧','themoviedb','24',1),
                             {'name':'GATE24 内格力','year':'2026','username':'PRIVATE_USER','save_path':'/private/test'},'test')
        self.repo.submit('other',self.r.Target('电影','themoviedb','25'),{'name':'另一个作品'},'test')
        page=ui.tasks(query='内格力',user=None)
        self.assertEqual(1,page.total)
        self.assertEqual(('GATE24 内格力','2026'),(page.items[0].title,page.items[0].year))
        self.assertNotIn('PRIVATE_USER',page.model_dump_json())
        self.assertNotIn('/private/test',page.model_dump_json())
        self.assertEqual('GATE24 内格力',ui.task(row['id'],user=None).task.title)
        self.assertEqual(0,ui.tasks(query="%' OR 1=1 --",user=None).total)

    def test_episode_pagination_is_numeric_and_keeps_current_and_inflight_facts_separate(self):
        ui=load('ui').Views(self.plugin)
        target=self.r.Target('电视剧','themoviedb','42',1)
        task=self.repo.submit('episodes',target,{'name':'作品'},'test')
        keys=[json.dumps(['电视剧','themoviedb','42',1,'',i],ensure_ascii=False,separators=(',',':')) for i in range(1,31)]
        with self.repo.connection(write=True) as db:
            for key in reversed(keys):
                db.execute('INSERT INTO target_units(target_key,task_id,identity,current_facts) VALUES(?,?,?,?)',
                    (key,task['id'],key,json.dumps({'state':'PRESENT','versions':[{'reliable':True,'raw':{'technical':{'resolution':1080}}}]})))
            db.execute("INSERT INTO opportunities(id,task_id,scope,mode,state,config,created_at,updated_at) VALUES(?,?,'[]','CONTINUOUS','ACTIVE','{}',?,?)",('round',task['id'],NOW.isoformat(),NOW.isoformat()))
            snap={'targets':{keys[0]:{'action':'QUALITY_UPGRADE','reason':'QUALITY_UPGRADE'}},'candidate_key':'candidate',
                  'torrent_files':[{'index':0,'path':'Series.S01E01.2160p.mkv','role':'video','targets':[keys[0]]}],
                  'selected_indices':[0],'password':'PRIVATE_SENTINEL'}
            db.execute("INSERT INTO plans(id,opportunity_id,task_id,snapshot,authorization,transfer_phase,created_at) VALUES(?,?,?,?,?,?,?)",('upgrade','round',task['id'],json.dumps(snap),'ACTIVE','RAPID_WAIT',NOW.isoformat()))
            db.execute("INSERT INTO plan_targets(plan_id,target_key,generation,state,action,transfer_phase) VALUES(?,?,0,'ACTIVE','QUALITY_UPGRADE','RAPID_WAIT')",('upgrade',keys[0]))
            db.execute("UPDATE target_units SET owner_plan_id='upgrade' WHERE target_key=?",(keys[0],))
            db.execute("UPDATE plans SET task_generation=? WHERE id='upgrade'",(task['generation'],))
        with self.repo.connection() as db:before='\n'.join(db.iterdump())
        one=ui.task(task['id'],limit=25,offset=0,user=None)
        two=ui.task(task['id'],limit=25,offset=25,user=None)
        self.assertEqual(list(range(1,31)),[json.loads(u.target_key)[5] for u in one.units.items+two.units.items])
        unit=one.units.items[0]
        self.assertEqual(1080,unit.current_facts['versions'][0]['raw']['technical']['resolution'])
        self.assertEqual('RAPID_WAIT',unit.processing['phase'])
        self.assertEqual(['Series.S01E01.2160p.mkv'],unit.processing['files'])
        self.assertNotIn('PRIVATE_SENTINEL',one.model_dump_json())
        with self.repo.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))

    def test_task_summary_counts_all_targets_and_read_does_not_write(self):
        ui=load('ui').Views(self.plugin)
        task=self.repo.submit('summary',self.r.Target('电视剧','themoviedb','42',1),{'name':'作品'},'test')
        with self.repo.connection(write=True) as db:
            for i in range(31):
                db.execute('INSERT INTO target_units(target_key,task_id,identity,current_facts,last_ingest_confirmed_at,owner_plan_id,cooldown_until) VALUES(?,?,?,?,?,?,?)',
                    (str(i),task['id'],'{}',json.dumps({'state':'PRESENT','versions':[{'raw':{'technical':{'resolution':1080}},'reliable':True}]}) if i<29 else None,NOW.isoformat() if i<29 else None,'plan' if i==30 else None,'2099-01-01T00:00:00+00:00' if i==29 else None))
        with self.repo.connection() as db:before='\n'.join(db.iterdump())
        progress=ui.tasks(limit=1,user=None).items[0].progress
        self.assertEqual((31,29,0),(progress['targets'],progress['confirmed'],progress['processing']))
        self.assertEqual(1,progress['unsettled'])
        self.assertEqual([1080],progress['resolutions'])
        self.assertEqual(29,progress['present'])
        self.assertEqual(progress,ui.task(task['id'],user=None).task.progress)
        self.assertEqual('2099-01-01T00:00:00+00:00',progress['cooldown_until'])
        with self.repo.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))

        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO task_lifecycle(task_id,config,scope) VALUES(?,?,?)',(task['id'],'{}','["0","30"]'))
            db.execute("INSERT INTO opportunities(id,task_id,scope,mode,state,config,created_at,updated_at) VALUES(?,?,'[]','CONTINUOUS','ACTIVE',?,?,?)",('summary-op',task['id'],'{"observation_enabled":false}',NOW.isoformat(),NOW.isoformat()))
            db.execute('INSERT INTO opportunity_targets VALUES(?,?,0)',('summary-op','30'))
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',('summary-op','30',NOW.isoformat(),NOW.isoformat(),'c','[1080]','2099-01-01T00:00:00+00:00'))
        progress=ui.tasks(limit=1,user=None).items[0].progress
        self.assertEqual((2,1),(progress['targets'],progress['present']))
        self.assertEqual(2,ui.task(task['id'],user=None).units.total)
        self.assertIsNone(progress['observation_until'])
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE opportunities SET config=? WHERE id=?',('{"observation_enabled":true}','summary-op'))
            db.execute('UPDATE observations SET deadline=?',('2000-01-01T00:00:00+00:00',))
        self.assertIsNone(ui.tasks(limit=1,user=None).items[0].progress['observation_until'])

    def test_policy_description_uses_saved_order_and_rank(self):
        policy=load('policy').Policy({'tv':'欧美剧'},1,templates={'欧美剧':dict(resolutions=[1080],group='any',source='web',dimensions=['audio','resolution'])})
        description=policy.describe('欧美剧')
        self.assertEqual([1080],description['resolutions'])
        self.assertEqual(['audio','resolution'],[r['dimension'] for r in description['comparison']])
        self.assertEqual('无损音轨',description['comparison'][0]['order'][0])
        self.assertEqual('仅 WEB 片源',description['source'])

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
        self.plugin.runtime=runtime_fixture(self.plugin,f.worker)
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
        self.assertEqual([x['snapshot']['path'] for x in f.worker.bundle(bid)['files']], p.objects['locations'])
        self.assertEqual('monitor', p.objects['scope'])
        p=view.cleanup_preview(bid,m.CleanupPreview(**self.fence(),revision=f.worker.bundle(bid)['revision'],scope='downloader_data'),user=user)
        self.assertFalse(p.permissions['delete_downloader_data_enabled'])

    def test_T116_superseded_old_delivery_remains_visible_for_review(self):
        from test_delivery import DeliveryTests
        f=self.fixture(DeliveryTests);bid=f.prepared()
        snap=copy.deepcopy(f.auth.plan('A')['snapshot'])
        snap['candidate_key']='B';snap['targets'][f.key]['quality']=[2]
        f.auth.prepare('B','round',snap,now=NOW)
        f.auth.supersede('B',f.auth.vector([f.key]),reason='QUALITY_UPGRADE',safe_isolation=True,now=NOW)
        self.assertEqual('ABANDONED',f.worker.reconcile(bid,now=NOW)['state'])
        view=load('ui').Views(self.plugin)
        page=view.bundles(plan_id='A',state='ABANDONED',user=None)
        self.assertEqual((1,bid,'SUPERSEDED'),(page.total,page.items[0].id,page.items[0].reason))
        self.assertEqual('SUPERSEDED',view.bundle(bid,user=None).bundle.reason)

    def test_delivery_projection_names_the_work_without_exposing_private_snapshot(self):
        from test_delivery import DeliveryTests
        f=self.fixture(DeliveryTests);bid=f.prepared();task_id=f.auth.plan('A')['task_id']
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE tasks SET snapshot=? WHERE id=?',(json.dumps({'name':'侠女内莉','username':'PRIVATE_SENTINEL'}),task_id))
        view=load('ui').Views(self.plugin)
        page=view.bundles(user=None)
        self.assertEqual(('侠女内莉',task_id),(page.items[0].title,page.items[0].task_id))
        self.assertEqual('侠女内莉',view.bundle(bid,user=None).bundle.title)
        self.assertNotIn('PRIVATE_SENTINEL',page.model_dump_json())

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
        f.archive.reconcile('test','10')
        with f.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT active FROM archive_versions WHERE id=?',(vid,)).fetchone()[0])
            self.assertEqual('INVALID',db.execute('SELECT state FROM archive_targets WHERE target_key=?',(f.key,)).fetchone()[0])
        runtime=SimpleNamespace(delivery=SimpleNamespace(archive=f.archive),repository=f.repo)
        self.assertEqual('INVALID',load('runtime').Runtime.inventory_view(runtime,f.r.Target('电影','themoviedb','42'),scope={'units':[f.key]})['state'])
        with f.repo.connection(write=True) as db:
            f.archive._store_version(db,f.archive.resolve_item('test','10',f.item)[0])
            self.assertEqual(0,db.execute('SELECT active FROM archive_versions WHERE id=?',(vid,)).fetchone()[0])
        self.assertEqual(before,{p.name:p.read_bytes() for p in f.strms.iterdir()})

    def test_ai_clear_fences_and_prompt_restore_use_no_model_or_chat(self):
        from test_ai import AITests
        f=AITests();f.setUp();self.addCleanup(f.doCleanups);ai=f.runtime();self.plugin.ai=ai
        m=load('ui');view=m.Views(self.plugin);user=SimpleNamespace(username='admin');request=m.Fence(**self.fence())
        ai.cache['old']=(0,{'name':'expired'})
        ai.stats();self.assertIn('old',ai.cache)
        p=view.cache_preview(request,user=user);ai.cache['new']=(0,{})
        with self.assertRaises(Exception) as error:view.apply_cache(self.apply_body(p),user=user)
        self.assertEqual(409,error.exception.status_code)
        p=view.cache_preview(request,user=user);self.assertEqual('APPLIED',view.apply_cache(self.apply_body(p),user=user).state)
        self.assertFalse(ai.cache)
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

    def test_completed_tv_mode_switch_keeps_episode_facts_and_survives_restart(self):
        from test_runtime import CommonAdmissionTests
        f=CommonAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        target=f.r.Target('电视剧','themoviedb','1396',1)
        keys=[f.p.TargetUnit(target,n).key for n in (1,2)]
        f.plugin.config.policy.bindings['tv']='欧美剧'
        template=f.plugin.config.destination_templates[0]
        template.id='tv-destination';template.category_id='tv'
        f.classification['effective']['category_id']='tv'
        scope=dict(target_key=target.key,provider_identity=['themoviedb','1396'],episode_group='',season=1,
            episodes=[1,2],provider_rows=[],scope_closed=True,units=keys,
            classification=f.classification,keywords=['Fixture'])
        f.provider.resolve=lambda _:copy.deepcopy(scope)
        f.runtime=f.m.Runtime(f.plugin,provider=f.provider,clients=lambda name:object())
        task=f.runtime.submit('manual',target,{'name':'Fixture'},'admin')
        with f.repo.connection(write=True) as db:
            opportunity=dict(db.execute('SELECT * FROM opportunities').fetchone())
            for n,key in enumerate(keys,1):
                db.execute('UPDATE target_units SET current_revision=?,current_facts=? WHERE target_key=?',
                           (n,json.dumps({'state':'PRESENT','episode':n}),key))
                db.execute('INSERT INTO archive_targets VALUES(?,?,?,?,?)',
                           (key,'PRESENT','revision-'+str(n),'{}',f.r.utcnow()))
                db.execute('INSERT INTO archive_versions VALUES(?,?,?,?,?,?)',
                           ('version-'+str(n),key,'emby','test',1,json.dumps({'episode':n})))
            before=[tuple(row) for row in db.execute('SELECT target_key,current_revision,current_facts FROM target_units ORDER BY target_key')]
            archive=[tuple(row) for row in db.execute('SELECT * FROM archive_versions ORDER BY id')]
        original=f.repo.setting('runtime-input:'+opportunity['id'])
        self.assertEqual('episode',original['planner_mode'])
        config=self.c.Configuration(f.repo,'SubscriBetter',PrivateFixture(),lambda x:None)
        config.initialize({})
        self.plugin.repository=f.repo;self.plugin.configuration=config;self.plugin.config=f.plugin.config
        self.plugin.generation=f.plugin.generation;self.plugin.runtime=f.runtime
        ui=load('ui');view=ui.Views(self.plugin);user=SimpleNamespace(username='admin')
        partial=ui.SettingsPreview(config_revision=config.view()['revision'],runtime_generation=f.plugin.generation,
            generation=task['generation'],opportunity_id=opportunity['id'],target_keys=keys[:1],
            destination_template='tv-destination',locks={},completed_mode='PACK')
        partial_preview=view.settings_preview(task['id'],partial,user=user)
        self.assertIn('PACK_REQUIRES_FULL_PROVIDER_SCOPE',partial_preview.blockers)
        with self.assertRaisesRegex(Exception,'PACK_REQUIRES_FULL_PROVIDER_SCOPE'):
            view.apply_settings(task['id'],self.apply_body(partial_preview,'partial'),user=user)
        request=ui.SettingsPreview(config_revision=config.view()['revision'],runtime_generation=f.plugin.generation,
            generation=task['generation'],opportunity_id=opportunity['id'],target_keys=keys,
            destination_template='tv-destination',locks={},completed_mode='PACK')
        preview=view.settings_preview(task['id'],request,user=user)
        self.assertEqual({'current':'EPISODE','requested':'PACK','planner_mode':'season'},preview.objects['mode_change'])
        self.assertEqual([],preview.blockers)
        result=view.apply_settings(task['id'],self.apply_body(preview),user=user).result
        switched=f.repo.setting('runtime-input:'+opportunity['id'])
        self.assertEqual('season',switched['planner_mode'])
        self.assertEqual('PACK',switched['effective']['lifecycle']['completed_mode'])
        self.assertEqual(result['generation'],switched['task_generation'])
        f.runtime.verify_input(switched)
        restarted=f.m.Runtime(f.plugin,provider=f.provider,clients=lambda name:object())
        import time
        restarted.bootstrap(time.monotonic()+2)
        restarted.verify_input(f.repo.setting('runtime-input:'+opportunity['id']))
        self.assertEqual(task['id'],restarted.submit('manual',target,{'name':'Fixture'},'admin')['id'])
        self.assertEqual(task['id'],restarted.submit('second',target,{'name':'Fixture'},'admin')['id'])
        back=ui.SettingsPreview(config_revision=config.view()['revision'],runtime_generation=f.plugin.generation,
            generation=result['generation'],opportunity_id=opportunity['id'],target_keys=keys,
            destination_template='tv-destination',locks={},completed_mode='EPISODE')
        back_preview=view.settings_preview(task['id'],back,user=user)
        self.assertEqual({'current':'PACK','requested':'EPISODE','planner_mode':'episode'},back_preview.objects['mode_change'])
        self.assertEqual('APPLIED',view.apply_settings(task['id'],self.apply_body(back_preview,'back'),user=user).state)
        self.assertEqual('episode',f.repo.setting('runtime-input:'+opportunity['id'])['planner_mode'])
        with f.repo.connection() as db:
            self.assertEqual(before,[tuple(row) for row in db.execute('SELECT target_key,current_revision,current_facts FROM target_units ORDER BY target_key')])
            self.assertEqual(archive,[tuple(row) for row in db.execute('SELECT * FROM archive_versions ORDER BY id')])
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])
            self.assertEqual(opportunity['created_at'],db.execute('SELECT created_at FROM opportunities').fetchone()[0])

    def test_settings_explicitly_rebind_reviewed_policy_and_parse_revisions(self):
        from test_runtime import CommonAdmissionTests
        f=CommonAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        task=f.runtime.submit('manual',f.target,{'name':'Fiction'},'admin')
        with f.repo.connection() as db:opportunity=dict(db.execute('SELECT * FROM opportunities').fetchone())
        original=f.repo.setting('runtime-input:'+opportunity['id'])
        f.runtime.policy.semantic_hash='reviewed-policy-revision'
        f.runtime.meta.corrector.revision='reviewed-parse-revision'
        request=dict(task_id=task['id'],generation=task['generation'],opportunity_id=opportunity['id'],
            target_keys=original['scope']['units'],destination_template='movie-destination',locks={},
            policy_revision=f.runtime.policy.semantic_hash,parse_revision=f.runtime.meta.corrector.revision)
        with self.assertRaisesRegex(ValueError,'REVIEWED_REVISIONS_CHANGED'):
            with f.repo.connection(write=True) as db:
                f.runtime.settings({k:v for k,v in request.items() if k not in ('policy_revision','parse_revision')},db=db,actor='admin')
        with f.repo.connection(write=True) as db:f.runtime.settings(request,db=db,actor='admin')
        saved=f.repo.setting('runtime-input:'+opportunity['id'])
        self.assertEqual((request['policy_revision'],request['parse_revision']),
            (saved['effective']['policy_revision'],saved['effective']['parse_revision']))
        self.assertEqual(saved['effective'],f.repo.setting('runtime-task:'+str(task['id']))['effective'])
        self.assertEqual(original['effective']['schedule'],saved['effective']['schedule'])
        self.assertEqual(original['effective']['lifecycle'],saved['effective']['lifecycle'])

    def test_settings_preview_shows_revisions_and_rejects_changed_revision(self):
        from test_runtime import CommonAdmissionTests
        f=CommonAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        task=f.runtime.submit('manual',f.target,{'name':'Fiction'},'admin')
        with f.repo.connection() as db:opportunity=dict(db.execute('SELECT * FROM opportunities').fetchone())
        config=self.c.Configuration(f.repo,'SubscriBetter',PrivateFixture(),lambda x:None)
        config.initialize({})
        self.plugin.repository=f.repo;self.plugin.configuration=config;self.plugin.config=f.plugin.config
        self.plugin.generation=f.plugin.generation;self.plugin.runtime=f.runtime
        ui=load('ui');view=ui.Views(self.plugin);user=SimpleNamespace(username='admin')
        saved=f.repo.setting('runtime-input:'+opportunity['id'])
        request=ui.SettingsPreview(**dict(config_revision=config.view()['revision'],runtime_generation=f.plugin.generation,
            generation=task['generation'],opportunity_id=opportunity['id'],target_keys=saved['scope']['units'],
            destination_template='movie-destination',locks={}))
        preview=view.settings_preview(task['id'],request,user=user)
        self.assertEqual(f.runtime.policy.semantic_hash,preview.objects['policy_revision'])
        self.assertEqual(f.runtime.meta.corrector.revision,preview.objects['parse_revision'])
        f.runtime.policy.semantic_hash='changed-after-preview'
        with self.assertRaisesRegex(Exception,'STALE_PREVIEW'):
            view.apply_settings(task['id'],self.apply_body(preview),user=user)
        f.runtime.meta.corrector.revision='parse-after-preview'
        fresh=view.settings_preview(task['id'],request,user=user)
        self.assertEqual('changed-after-preview',fresh.objects['policy_revision'])
        self.assertEqual('parse-after-preview',fresh.objects['parse_revision'])
        self.assertEqual('APPLIED',view.apply_settings(task['id'],self.apply_body(fresh),user=user).state)
        effective=f.repo.setting('runtime-input:'+opportunity['id'])['effective']
        self.assertEqual(('changed-after-preview','parse-after-preview'),
            (effective['policy_revision'],effective['parse_revision']))

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
