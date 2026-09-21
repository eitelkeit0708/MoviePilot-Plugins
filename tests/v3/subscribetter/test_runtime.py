"""A2 composition boundaries, fictional provider/SDK and disposable local state only."""
from datetime import date
from types import SimpleNamespace
import copy
import json
import asyncio
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from test_planner import load


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.m=load('runtime');self.r=load('repository');self.calls=[]
        self.media=SimpleNamespace(type='电视剧',identity=('themoviedb','1396'),tmdb_info={'seasons':[{'season_number':1,'episode_count':1}],'episode_groups':{'results':[{'id':'group'}]}},
            episode_group='group',season_info=[{'order':6,'episodes':[{'id':42,'show_id':1396,'season_number':5,'episode_number':1,'air_date':'2020-01-01'}]}])
        self.detail={'season_number':1,'episodes':[{'id':41,'show_id':1396,'season_number':1,'episode_number':7,'air_date':'2020-01-01'}]}
        def recognize(target,group):self.calls.append(('recognize',group));return copy.deepcopy(self.media)
        def season(target):self.calls.append(('season',target.season));return copy.deepcopy(self.detail)
        self.provider=self.m.ScopeProvider(recognize=recognize,season_detail=season,identity=lambda m:m.identity,classify=lambda m:{'state':'not_evaluated'})

    def test_explicit_provider_rows_and_group_membership_preserve_broadcast_identity(self):
        target=self.r.Target('电视剧','themoviedb','1396',1)
        scope=self.provider.resolve(target,today=date(2026,9,20))
        self.assertEqual([7],scope['episodes']);self.assertTrue(scope['scope_closed'])
        self.assertEqual(41,scope['provider_rows'][0]['id'])
        group=self.r.Target('电视剧','themoviedb','1396',6,'group')
        scope=self.provider.resolve(group,today=date(2026,9,20))
        self.assertEqual([1],scope['episodes']);self.assertEqual(5,scope['provider_rows'][0]['season_number'])
        self.assertEqual('group',scope['episode_group']);self.assertEqual(6,scope['season'])
        self.media.tmdb_info={'episode_groups':{'results':[{'id':'other'}]}}
        with self.assertRaisesRegex(ValueError,'GROUP_MEMBERSHIP_UNVERIFIED'):self.provider.resolve(group)

    def test_counts_duplicates_wrong_identity_future_and_unknown_are_not_complete_scope(self):
        target=self.r.Target('电视剧','themoviedb','1396',1)
        for detail in ({'episode_count':7},{'episodes':[]},{'episodes':[{'season_number':1,'episode_number':0}]},
                       {'episodes':[{'season_number':2,'episode_number':1}]},
                       {'episodes':[{'season_number':1,'episode_number':1}]*2}):
            self.detail=detail
            with self.subTest(detail=detail),self.assertRaises(ValueError):self.provider.resolve(target)
        self.detail={'episodes':[{'id':41,'season_number':1,'episode_number':7,'air_date':'2099-01-01'}]}
        self.assertFalse(self.provider.resolve(target)['scope_closed'])
        self.detail['episodes'][0]['air_date']=None
        self.assertFalse(self.provider.resolve(target)['scope_closed'])
        self.media.identity=('themoviedb','999')
        with self.assertRaisesRegex(ValueError,'PROVIDER_IDENTITY_UNVERIFIED'):self.provider.resolve(target)
        with self.assertRaises(ValueError):self.provider.resolve(self.r.Target('电视剧','douban','1396',1))


class DirectedInventoryTests(unittest.TestCase):
    def setUp(self):
        self.r=load('repository');self.p=load('planner');self.m=load('archive')
        self.sources=self.m.HostArchiveSources(None,cloud_scopes={},libraries={'emby':['library']})
        self.series={'Id':'series','Type':'Series','ProviderIds':{'Tmdb':'1396'}}
        self.episode={'Id':'episode','Type':'Episode','SeriesId':'series','ProviderIds':{'Tmdb':'62155'},
            'ParentIndexNumber':5,'IndexNumber':9}

    def test_provider_query_uses_verified_series_parent_and_no_extra_terminal_request(self):
        target=self.r.Target('电视剧','themoviedb','1396',6,'group')
        calls=[]
        def query(service,library,params):
            calls.append(params)
            return {'Items':[self.series] if params['IncludeItemTypes']=='Series' else [self.episode], 'TotalRecordCount':1}
        with patch.object(self.sources,'_emby',side_effect=query):
            page=self.sources.emby_target_page('emby','library',target,0,2)
        self.assertEqual('tmdb.1396',calls[0]['AnyProviderIdEquals'])
        self.assertEqual('series',calls[1]['ParentId']);self.assertNotIn('SeriesId',calls[1])
        self.assertEqual(self.series,page['Series']);self.assertEqual(2,len(calls))
        self.assertEqual(1,page['TotalRecordCount'])
        self.episode['SeriesId']='unselected'
        with patch.object(self.sources,'_emby',side_effect=query),self.assertRaisesRegex(ValueError,'SERIES_IDENTITY_CONFLICT'):
            self.sources.emby_target_page('emby','library',target,0,2)

    def test_group_join_uses_episode_provider_id_not_broadcast_number_or_offset(self):
        target=self.r.Target('电视剧','themoviedb','1396',6,'group')
        scope={'target_key':target.key,'provider_rows':[{'id':62155,'season_number':5,'episode_number':1}]}
        key=self.p.TargetUnit(target,1).key
        self.assertEqual([key],self.m.units(self.episode,[{'episode_group':'group'}],{'series':self.series},scope=scope))
        with self.assertRaisesRegex(ValueError,'GROUP_SCOPE_REQUIRED'):
            self.m.units(self.episode,[{'episode_group':'group'}],{'series':self.series})
        self.episode['ProviderIds']={'Tmdb':'unknown'}
        with self.assertRaisesRegex(ValueError,'GROUP_EPISODE_UNVERIFIED'):
            self.m.units(self.episode,[{'episode_group':'group'}],{'series':self.series},scope=scope)


class ScanFinalizationTests(unittest.TestCase):
    def setUp(self):
        import test_archive
        test_archive.ArchiveTests.setUpClass()
        self.fixture=test_archive.ArchiveTests('test_current_old_library_independent_item_id_churn_multi_versions')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_finalize_is_bounded_restartable_and_does_not_publish_before_watermark(self):
        f=self.fixture
        self.assertEqual('COMPLETE',f.archive.reconcile('test','10')['status'])
        before=f.archive.current([f.key])[f.key]
        for n in range(5):f.media('new'+str(n),'new'+str(n)+'.strm',str(n+1)*40)
        limits={'pages':1,'page_size':100,'items':1}
        result=f.archive.reconcile('test','10',limits=limits)
        finalize=0
        while result['status']=='INCOMPLETE':
            if result['phase']=='FINALIZE':
                finalize+=1
                current=f.archive.current([f.key])[f.key]
                self.assertEqual(before['archive_revision'],current['archive_revision'])
                self.assertEqual(1,len(current['versions']))
                f.archive=f.m.Archive(f.repo,f.policy,f.sources,mappings=[f.mapping])
            result=f.archive.reconcile('test','10',limits=limits,scan_id=result['scan_id'])
        self.assertGreaterEqual(finalize,5);self.assertEqual('COMPLETE',result['status'])
        self.assertEqual(6,len(f.archive.current([f.key])[f.key]['versions']))

    def test_late_finalize_failure_keeps_every_old_version(self):
        f=self.fixture;f.archive.reconcile('test','10')
        old={v.version_id for v in f.archive.current([f.key])[f.key]['versions']}
        f.media('a','a.strm','b'*40);f.media('z','z.strm','c'*40)
        limits={'pages':1,'page_size':100,'items':1}
        result=f.archive.reconcile('test','10',limits=limits)
        while result['status']=='INCOMPLETE' and result['phase']!='FINALIZE':
            result=f.archive.reconcile('test','10',limits=limits,scan_id=result['scan_id'])
        self.assertEqual('FINALIZE',result['phase'])
        result=f.archive.reconcile('test','10',limits=limits,scan_id=result['scan_id'])
        (f.strms/'z.strm').write_text('/Cloud/115/media/changed.mkv')
        while result['status']=='INCOMPLETE':result=f.archive.reconcile('test','10',limits=limits,scan_id=result['scan_id'])
        self.assertEqual('ERROR',result['status'])
        self.assertEqual(old,{v.version_id for v in f.archive.current([f.key])[f.key]['versions']})

    def test_final_sql_does_not_overwrite_newer_other_library_or_authority_facts(self):
        f=self.fixture
        target=f.r.Target('电影','themoviedb','42')
        task=f.repo.submit('managed',target,{},'fixture',42,True);f.repo.complete_handoff(task['id'],task['generation'])
        f.s.Scheduler(f.repo).open_opportunity('managed',task['id'],[f.a.TargetUnit(target)],mode='CONTINUOUS',config=f.s.ScheduleConfig())
        f.archive.reconcile('test','10')
        f.media('second','second.strm','b'*40)
        limits={'pages':1,'page_size':100,'items':1}
        for conflict in ('current','other_library'):
            result=f.archive.reconcile('test','10',limits=limits)
            while result['status']=='INCOMPLETE' and result['phase']!='FINALIZE':
                result=f.archive.reconcile('test','10',scan_id=result['scan_id'],limits=limits)
            self.assertEqual('FINALIZE',result['phase'])
            if conflict=='current':
                authority=f.a.Authority(f.repo);revision=authority.vector([f.key])[f.key]['current_revision']
                authority.update_current(f.key,{'state':'UNKNOWN','evidence_ref':'other-ingest'},expected_revision=revision)
            else:
                second=f.m.Archive(f.repo,f.policy,f.sources,mappings=[f.mapping,dict(f.mapping,id='other',library_id='11')])
                self.assertEqual('COMPLETE',second.reconcile('test','11')['status'])
            with f.repo.connection() as db:
                before=tuple(db.execute('SELECT current_revision,current_facts FROM target_units WHERE target_key=?',(f.key,)).fetchone())
                archive_before=tuple(db.execute('SELECT state,revision,data,updated_at FROM archive_targets WHERE target_key=?',(f.key,)).fetchone())
            while result['status']=='INCOMPLETE':result=f.archive.reconcile('test','10',scan_id=result['scan_id'],limits=limits)
            self.assertEqual('ERROR',result['status']);self.assertIn(result['diagnostics'][0],('SCAN_AUTHORITY_CHANGED','SCAN_SUPERSEDED'))
            with f.repo.connection() as db:
                self.assertEqual(before,tuple(db.execute('SELECT current_revision,current_facts FROM target_units WHERE target_key=?',(f.key,)).fetchone()))
                self.assertEqual(archive_before,tuple(db.execute('SELECT state,revision,data,updated_at FROM archive_targets WHERE target_key=?',(f.key,)).fetchone()))


class OwnedStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_reload_drains_actual_thread_before_close_and_rejects_new_stage(self):
        m=load('runtime');started=threading.Event();release=threading.Event();events=[]
        owner=m.OwnedStages(lambda:events.append('close'))
        def work():started.set();release.wait(2);events.append('finished')
        task=asyncio.create_task(owner.run(work))
        await asyncio.to_thread(started.wait,1)
        owner.retire();task.cancel();await asyncio.sleep(.01)
        self.assertEqual([],events);self.assertFalse(task.done())
        with self.assertRaisesRegex(ValueError,'STALE_GENERATION'):await owner.run(lambda:None)
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(['finished','close'],events)


class CommonAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.r=load('repository');self.p=load('planner');self.s=load('scheduler');self.c=load('configuration');self.m=load('runtime')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=self.r.Repository(Path(self.tmp.name)/'state.db')
        config=self.c.Config.model_validate({'enabled':True,'dry_run':False,'policy':{'bindings':{'movie':'外语电影'}},
            'candidates':{'site_ids':[1],'interval':0},'schedule':{'observation_enabled':True,'base_seconds':30,'quiet_seconds':20,'max_seconds':90,'failure_limit':2},
            'destination_templates':[{'id':'movie-destination','category_id':'movie','downloader':'qb','save_path':'/downloads','sites':[1]}]})
        # The accepted config and actual rule are injected together; this unit
        # does not claim host service/reference or filesystem availability.
        config.destination_templates[0].organized_rule='rule'
        self.classification={'state':'complete','policy_revision':1,'effective':{'category_id':'movie'}}
        self.target=self.r.Target('电影','themoviedb','42');self.calls=[]
        def submit(intent,target,snapshot,actor,native_id=None,adopt=False):
            self.calls.append(snapshot)
            row=self.repo.submit(intent,target,snapshot,actor,native_id=native_id)
            if row['state']=='PENDING':
                self.repo.bind_native(row['id'],row['id']+100);self.repo.complete_handoff(row['id'],row['generation'])
            return self.repo.get_task(row['id'])
        self.plugin=SimpleNamespace(repository=self.repo,config=config,generation=1,ownership=SimpleNamespace(submit=submit),
            scheduler=self.s.Scheduler(self.repo),authority=self.p.Authority(self.repo),candidates=SimpleNamespace(),
            meta_service=SimpleNamespace(corrector=SimpleNamespace(revision='parse-v1')),ai=None,
            delivery_worker=SimpleNamespace(rules={'rule':{'enabled':True}},archive=SimpleNamespace(current=lambda keys:{})),
            _ordinary_work_active=lambda:True)
        scope=dict(target_key=self.target.key,provider_identity=['themoviedb','42'],episode_group='',season=None,episodes=[],provider_rows=[],
            scope_closed=True,units=[self.p.TargetUnit(self.target).key],classification=self.classification,keywords=['Fiction'])
        self.provider=SimpleNamespace(resolve=lambda target:copy.deepcopy(scope))
        self.runtime=self.m.Runtime(self.plugin,provider=self.provider,clients=lambda name:object())

    def test_manual_native_discovery_converge_and_freeze_real_config_separate_from_restore(self):
        first=self.runtime.submit('manual',self.target,{'name':'Fiction'},'admin')
        self.runtime.submit('discovery',self.target,{'name':'Fiction','save_path':'movie-destination'},'discovery')
        self.runtime.submit('native',self.target,{'name':'Fiction'},'automatic')
        with self.repo.connection() as db:
            self.assertEqual(3,db.execute('SELECT COUNT(*) FROM intents').fetchone()[0])
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])
            opportunity=db.execute('SELECT id,config FROM opportunities').fetchone()
        frozen=self.repo.setting('runtime-input:'+opportunity['id'])
        self.assertEqual(30,json.loads(opportunity['config'])['base_seconds'])
        self.assertEqual(2,frozen['effective']['schedule']['failure_limit'])
        self.assertEqual('/downloads',self.calls[1]['save_path'])
        self.assertNotIn('scope',self.repo.get_task(first['id'])['snapshot'])
        self.plugin.config.schedule['base_seconds']=60
        self.assertEqual(30,frozen['effective']['schedule']['base_seconds'])
        self.repo.set_state(first['id'],'STOPPED','admin')
        self.assertEqual('STOPPED',self.runtime.submit('later',self.target,{'name':'Fiction'},'admin')['state'])

    def test_generation_change_and_unverified_classification_send_no_native_request(self):
        self.plugin.generation=2
        with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):
            self.runtime.submit('stale',self.target,{'name':'Fiction'},'admin')
        self.assertEqual([],self.calls)
        self.plugin.generation=1;self.classification['state']='not_evaluated'
        with self.assertRaisesRegex(ValueError,'CLASSIFICATION_UNVERIFIED'):
            self.runtime.submit('unknown',self.target,{'name':'Fiction'},'admin')
        self.assertEqual([],self.calls)

    def test_restart_recovers_admitted_opportunity_without_overwriting_native_snapshot(self):
        row=self.runtime.submit('manual',self.target,{'name':'Fiction','username':'admin'},'admin')
        self.runtime.submit('discovery',self.target,{'name':'Fiction','username':'rss','save_path':'movie-destination'},'discovery')
        with self.repo.connection(write=True) as db:
            opportunity=db.execute('SELECT id FROM opportunities').fetchone()[0]
            db.execute('DELETE FROM settings WHERE key=?',('runtime-input:'+opportunity,))
        restarted=self.m.Runtime(self.plugin,provider=self.provider,clients=lambda name:object())
        import time
        restarted.bootstrap(time.monotonic()+2)
        self.assertEqual(row['id'],self.repo.setting('runtime-input:'+opportunity)['task_id'])
        self.assertEqual('admin',self.repo.get_task(row['id'])['snapshot']['username'])
        self.assertEqual('admin',self.repo.setting('runtime-task:'+str(row['id']))['native']['username'])


class ConsumerTests(unittest.TestCase):
    setUp=ScanFinalizationTests.setUp

    def test_server_derives_all_assets_then_shared_atomic_ingest_and_duplicate(self):
        f=self.fixture;f.publication();m=load('runtime_delivery')
        scope=dict(target_key=f.r.Target('电影','themoviedb','42').key,units=[f.key],provider_rows=[])
        calls=[]
        def page(service,library,target,start,limit):
            calls.append((service,library,start,limit));return f.sources.emby_page(service,library,start,limit)
        f.sources.emby_target_page=page;f.archive.scope_provider=lambda target:scope
        def inventory(cloud,path,**kwargs):
            return [dict(path=p,directory=False) for s,p in f.sources.cloud if s==cloud and p.startswith(path+'/')]
        worker=SimpleNamespace(archive=f.archive,cloud=SimpleNamespace(inventory=inventory))
        bundle=dict(id='server-derived',manifest=f.manifest,publication_action='pub')
        secret=f.sources.cloud.pop(('cloud','/115/media/movie.srt'))
        with self.assertRaisesRegex(ValueError,'CONSUMER_ASSETS_INCOMPLETE'):
            for _ in range(10):m.observe_consumer(worker,bundle,lambda target:scope,entries=1,pages=1)
        self.assertIsNone(f.s.Scheduler(f.repo).target(f.key)['last_ingest_confirmed_at'])
        f.sources.cloud['cloud','/115/media/movie.srt']=secret
        with f.repo.connection(write=True) as db:db.execute('DELETE FROM settings WHERE key=?',('runtime-consumer:'+bundle['id'],))
        receipt=None
        for _ in range(10):
            receipt=m.observe_consumer(worker,bundle,lambda target:scope,entries=1,pages=1)
            if receipt:break
        self.assertIsNotNone(receipt);self.assertEqual([0,1],sorted(x['file_index'] for x in receipt['assets']))
        self.assertEqual([dict(service='test',library_id='10',item_id='old')],receipt['emby'])
        f.archive.authority.record_result('pub','HANDED_OFF',dict(asset_manifest=f.manifest,consumer_receipt=receipt))
        self.assertTrue(f.archive.confirm_ingest('pub',f.manifest,receipt)['accepted'])
        self.assertFalse(f.archive.confirm_ingest('pub',f.manifest,receipt)['accepted'])
        self.assertTrue(all(c[:2]==('test','10') for c in calls))
        with f.repo.connection() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM ingest_receipts').fetchone()[0])
            self.assertEqual('ARCHIVED',db.execute("SELECT state FROM opportunities WHERE id='o'").fetchone()[0])
            self.assertEqual('PASSIVE',db.execute('SELECT state FROM tasks').fetchone()[0])


class ColdExecutionTests(unittest.TestCase):
    setUp=CommonAdmissionTests.setUp

    def test_completed_organized_assets_reenter_delivery_without_rehashing_source(self):
        from test_acceptance_runtime_clocks import RuntimeClockTests
        from test_planner import NOW
        from datetime import timedelta
        from unittest.mock import Mock
        f=RuntimeClockTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.prepare('B',2)
        f.f.plugin.authority.claim('B',f.f.plugin.authority.vector([f.key]),now=NOW+timedelta(seconds=121),immediate=True)
        runtime=f.f.runtime;plan=f.f.plugin.authority.plan('B')
        saved=f.f.repo.setting('runtime-input:'+f.opportunity)
        with f.f.repo.connection(write=True) as db:
            db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?)',
                ('B',0,'/fixture/source','/fixture/output',100,'a'*64,'COMPLETE','{}'))
        executor=SimpleNamespace(_owned=lambda _:None,sample=lambda _:dict(status='COMPLETED'))
        organizer=Mock(return_value=dict(state='COMPLETE'))
        delivery=SimpleNamespace(rules={'rule':dict(local_root='/fixture',enabled=True)},
            validate_publication=Mock(),prepare=Mock(return_value=dict(state='PREPARED',bundle_id='bundle')))
        runtime.delivery=delivery
        with patch.object(runtime,'refresh_plan'),patch.object(runtime.pipeline,'revalidate',return_value=dict(
                facts={f.key:SimpleNamespace(raw={})},classification={})),\
             patch.object(runtime.pipeline,'executor',return_value=executor),\
             patch.object(runtime.pipeline,'execute',return_value=dict(state='RUNNING')),\
             patch.object(runtime.pipeline,'organize',organizer),patch.object(runtime,'persist_scope'):
            self.assertEqual('PREPARED',runtime.advance(plan,saved)['state'])
            organizer.assert_not_called()
            with f.f.repo.connection(write=True) as db:
                db.execute("UPDATE organized_assets SET state='AUTHORIZED' WHERE plan_id='B'")
            self.assertEqual('PREPARED',runtime.advance(plan,saved)['state'])
            organizer.assert_called_once()

    def test_completed_asset_with_changed_destination_cannot_prepare_delivery(self):
        from test_delivery import DeliveryTests
        DeliveryTests.setUpClass()
        f=DeliveryTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        path=f.local/'movie.mkv'
        path.write_bytes(b'y'*100)  # Same size, different digest.
        with self.assertRaisesRegex(ValueError,'ORGANIZED_ASSET_CHANGED'):
            f.worker.prepare('A','r',publication=f.publication)
        with f.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM delivery_bundles').fetchone()[0])

    def test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers(self):
        import sys
        from torrentool.api import Bencode
        cm=load('candidates');self.m.CandidatePipeline=cm.CandidatePipeline
        raw=dict(site=1,torrent_id='42',title='Fiction 2160p WEB-DL 中文字幕',description='',labels=[])
        content=Bencode.encode({'info':{'name':'Pack','piece length':16384,'pieces':b'x'*20,'files':[
            {'path':['Fiction.2160p.WEB-DL.mkv'],'length':100},{'path':['Fiction.zh.srt'],'length':3},{'path':['poster.jpg'],'length':2}]}})
        available=[content];searches=[];calls=[]
        class Adapter:
            def sites(s):return [dict(id=1)]
            def page_size(s,*args):return None
            def search(s,site,word,page):searches.append((site['id'],word,page));return [raw]
            def acquire(s,raw):return available[0]
            def recognize(s,*args):return SimpleNamespace(title='Fiction')
            def identity(s,media):return ('themoviedb','42')
            def classify(s,media):return self.classification
        class Meta:
            corrector=SimpleNamespace(revision='parse-v1')
            def parse(s,*args,**kwargs):return SimpleNamespace(status='OK',meta=SimpleNamespace(),revision='parse-v1',record=lambda:dict(status='OK'))
        table=cm.torrent_table(content)[1]
        class Client:
            exists=False;state='PAUSED';wanted={0,1,2}
            def task(s,infohash):return dict(id=infohash,infohash=infohash,save_path='/downloads',state=s.state) if s.exists else None
            def files(s,infohash):return [dict(id=i+10,path=p,size=n,wanted=i in s.wanted,completed=0) for i,(p,n) in reversed(list(enumerate(table)))]
            def add(s,body,infohash,save_path,marker):s.exists=True;calls.append('add');return infohash
            def pause(s,tid):s.state='PAUSED';calls.append('pause');return True
            def select_files(s,tid,indices,wanted):
                original={i-10 for i in indices};s.wanted=s.wanted|original if wanted else s.wanted-original;calls.append('select');return True
            def resume(s,tid):s.state='DOWNLOADING';calls.append('resume');return True
        client=Client();self.plugin.candidates=cm.CandidateService(self.repo,Adapter());self.plugin.meta_service=Meta()
        key=self.p.TargetUnit(self.target).key
        self.plugin.delivery_worker.archive.current=lambda keys:{key:dict(state='MISSING',revision=0,versions=[])}
        runtime=self.m.Runtime(self.plugin,provider=self.provider,clients=lambda name:client)
        row=runtime.submit('manual',self.target,{'name':'Fiction'},'admin')
        with self.repo.connection() as db:opportunity=dict(db.execute('SELECT * FROM opportunities').fetchone())
        saved=self.repo.setting('runtime-input:'+opportunity['id'])
        candidate=runtime.candidates.observe(raw);result=runtime.evaluate(saved,candidate['candidate_key'])
        self.assertEqual(1,len(result['plans']),result)
        snapshot=result['plans'][0]
        for target,value in snapshot['targets'].items():runtime.scheduler.observe(opportunity['id'],target,snapshot['candidate_key'],value['quality'],eligible=True)
        runtime.authority.prepare('cold',opportunity['id'],snapshot)
        runtime.candidates.runtime.clear();runtime.pipeline.rounds.clear()
        available[0]=content.replace(b'x'*20,b'y'*20)
        with self.assertRaisesRegex(ValueError,'COLD_PLAN_CHANGED'):runtime.recover(runtime.authority.plan('cold'),saved)
        self.assertEqual([],calls)
        available[0]=content;runtime.recover(runtime.authority.plan('cold'),saved)
        self.assertTrue(searches);self.assertEqual({1},{s[0] for s in searches})
        from datetime import timedelta
        later=self.s.instant()+timedelta(seconds=100)
        runtime.authority.claim('cold',runtime.authority.vector([key]),now=later)
        class Download:
            def download_added(s,**kwargs):calls.append('download_added')
            def download_site_subtitles(s,**kwargs):calls.append('download_site_subtitles')
        public=getattr(self,'public_plugin_callbacks',SimpleNamespace(
            ModuleManager=lambda:SimpleNamespace(get_running_modules=lambda method:iter(())),
            PluginManager=lambda:SimpleNamespace(get_plugin_modules=lambda:{})))
        with patch.dict(sys.modules,{'app.chain.download':SimpleNamespace(DownloadChain=Download),'app.sdk.media':SimpleNamespace(Context=lambda **kw:kw),'app.sdk.plugin':public}):
            result=runtime.advance(runtime.authority.plan('cold'),saved)
        self.assertEqual('DOWNLOADING',result['state'],result);self.assertEqual({0,1},client.wanted)
        self.assertEqual(1,calls.count('add'));self.assertEqual(1,calls.count('resume'))
        runtime.candidates.runtime.clear();runtime.pipeline.rounds.clear();self.plugin.generation+=1
        with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):runtime.advance(runtime.authority.plan('cold'),saved)
        self.assertEqual(1,calls.count('add'))


class PassiveTests(unittest.TestCase):
    setUp=CommonAdmissionTests.setUp

    def test_independent_rss_persists_watermark_failure_empty_and_cold_cursor(self):
        import time
        cm=load('candidates');m=load('runtime_passive');calls=[]
        self.runtime.config.passive_libraries={'test':['10']}
        self.runtime.config.candidates.supplement_limit=1
        rows=[dict(site=1,torrent_id=str(n),title='Fiction '+str(n),description='真实描述',pubdate='2026-09-20') for n in range(3)]
        adapter=SimpleNamespace(sites=lambda:[dict(id=1)],rss=lambda site,timeout:rows)
        self.runtime.candidates=cm.CandidateService(self.repo,adapter)
        p=m.Passive(self.runtime)
        p.arrival=lambda key:calls.append(key) or dict(state='NO_IMPROVEMENT')
        self.assertEqual(2,p.rss(time.monotonic()+2)['remaining'])
        state=self.repo.setting('runtime-rss-site:1');self.assertEqual(1,state['cursor']);self.assertTrue(state['watermark'])
        self.assertIsNone(self.runtime.candidates.records()[0]['labels'])
        self.assertEqual('真实描述',self.runtime.candidates.records()[0]['description'])
        # The in-memory torrent map may disappear; remaining keys are durable.
        self.runtime.candidates.runtime.clear();p=m.Passive(self.runtime)
        p.arrival=lambda key:calls.append(key) or dict(state='NO_IMPROVEMENT')
        p.rss(time.monotonic()+2);p.rss(time.monotonic()+2)
        self.assertEqual(3,len(set(calls)))
        state=self.repo.setting('runtime-rss-site:1');state['next_at']='2000-01-01T00:00:00+00:00';self.repo.setting('runtime-rss-site:1',state)
        p.rss(time.monotonic()+2);self.assertEqual(3,len(calls))
        state=self.repo.setting('runtime-rss-site:1');before=state['watermark'];state['next_at']='2000-01-01T00:00:00+00:00';self.repo.setting('runtime-rss-site:1',state)
        def fail(*args):raise ValueError('SITE_RSS_FAILED')
        adapter.rss=fail;self.assertEqual('RSS_FAILED',p.rss(time.monotonic()+2)['state'])
        state=self.repo.setting('runtime-rss-site:1');self.assertEqual(before,state['watermark']);self.assertEqual('SITE_RSS_FAILED',state['failure'])
        state['next_at']='2000-01-01T00:00:00+00:00';self.repo.setting('runtime-rss-site:1',state)
        adapter.rss=lambda *args:[];self.assertEqual('RSS_CHECKED',p.rss(time.monotonic()+2)['state'])
        self.assertIsNone(self.repo.setting('runtime-rss-site:1')['failure'])
        with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])

    def test_passive_scan_only_archives_and_valid_upgrade_opens_scoped_oneshot(self):
        import time
        m=load('runtime_passive');cm=load('candidates');key=self.p.TargetUnit(self.target).key
        self.runtime.config.passive_libraries={'test':['10']};scans=[]
        self.runtime.delivery.archive.reconcile=lambda *args,**kw:scans.append((args,kw)) or dict(status='COMPLETE',scan_id='scan')
        passive=m.Passive(self.runtime);passive.scan(time.monotonic()+2)
        with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
        self.assertEqual(('test','10'),scans[0][0])
        media=SimpleNamespace(type='电影')
        raw=dict(site=1,torrent_id='42',title='Fiction',description='',labels=[])
        adapter=SimpleNamespace(sites=lambda:[dict(id=1)],search=lambda *args:[raw],page_size=lambda *args:None,
            recognize=lambda *args:media,identity=lambda media:('themoviedb','42'))
        self.runtime.candidates=cm.CandidateService(self.repo,adapter);candidate=self.runtime.candidates.observe(raw)
        self.runtime.meta=SimpleNamespace(parse=lambda *args:SimpleNamespace(status='OK',meta=SimpleNamespace()),corrector=SimpleNamespace(revision='parse-v1'))
        self.runtime.delivery.archive.current=lambda keys:{key:dict(archive_revision='before',state='PRESENT')}
        outcomes=[dict(plans=[])]
        self.runtime.pipeline=SimpleNamespace(evaluate=lambda *args,**kw:outcomes[0])
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO archive_targets VALUES(?,?,?,?,?)',(key,'PRESENT','r','{}',self.r.utcnow()))
            db.execute('INSERT INTO archive_versions VALUES(?,?,?,?,?,?)',('version',key,'test','10',1,'{}'))
        self.assertEqual('NO_IMPROVEMENT',passive.arrival(candidate['candidate_key'])['state'])
        with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
        outcomes[0]=dict(plans=[dict(targets={key:dict(action='QUALITY_UPGRADE')})])
        admitted=passive.arrival(candidate['candidate_key']);self.assertEqual('ADMITTED',admitted['state'])
        opportunity=self.runtime.scheduler.opportunity(admitted['opportunity_id']);self.assertEqual('ONESHOT',opportunity['mode'])
        self.assertEqual([key],json.loads(opportunity['scope']))
        self.assertIsNone(self.runtime.scheduler.lifecycle(admitted['task_id']))
        self.repo.set_state(admitted['task_id'],'STOPPED','test')
        self.assertEqual('USER_STOPPED',passive.arrival(candidate['candidate_key'])['state'])
        with self.repo.connection() as db:self.assertEqual(1,db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])

    def test_completed_oneshot_is_not_reopened_by_bootstrap(self):
        import time
        row=self.runtime.submit('once',self.target,{'name':'Fiction'},'rss',mode='ONESHOT')
        with self.repo.connection(write=True) as db:db.execute("UPDATE opportunities SET state='ARCHIVED'")
        self.runtime.bootstrap(time.monotonic()+2)
        with self.repo.connection() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])
            self.assertEqual('ARCHIVED',db.execute('SELECT state FROM opportunities').fetchone()[0])

    def test_expired_arrival_after_empty_feed_and_restart_keeps_same_budget(self):
        import time
        row=self.runtime.submit('rss:same-baseline',self.target,{'name':'Fiction'},'rss',mode='ONESHOT')
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE opportunities SET state='ARCHIVED',failures=2")
        # A successful empty feed drops its rolling seen window; durable business
        # intent still prevents the same unchanged-baseline arrival reopening it.
        self.repo.setting('runtime-rss-site:1',dict(seen={},pending=[],cursor=0))
        restarted=self.m.Runtime(self.plugin,provider=self.provider,clients=lambda name:object())
        restarted.submit('rss:same-baseline',self.target,{'name':'Fiction'},'rss',mode='ONESHOT')
        restarted.bootstrap(time.monotonic()+2)
        with self.repo.connection() as db:
            self.assertEqual([('ARCHIVED',2)],[tuple(r) for r in db.execute('SELECT state,failures FROM opportunities')])

    def test_large_selected_site_list_rotates_with_one_search_budget(self):
        row=self.runtime.submit('manual',self.target,{'name':'Fiction'},'admin')
        with self.repo.connection() as db:opportunity=db.execute('SELECT id FROM opportunities').fetchone()[0]
        saved=self.repo.setting('runtime-input:'+opportunity)
        saved['effective']['template']['sites']=list(range(1,101));saved['effective']['candidates']['requests']=4
        calls=[];self.runtime.candidates=SimpleNamespace(search=lambda sites,words,budget:calls.append(sites) or [])
        self.runtime.search(saved,['Fiction'])
        state=self.repo.setting('runtime-search:'+opportunity);state['next_at']='2000-01-01T00:00:00+00:00';self.repo.setting('runtime-search:'+opportunity,state)
        self.runtime.search(saved,['Fiction'])
        self.assertEqual([[1,2],[3,4]],calls)

    def test_policy_reprofile_pages_stored_facts_without_provider_or_clock_writes(self):
        cm=load('candidates');service=cm.CandidateService(self.repo,None)
        for n in range(5):service.observe(dict(site=1,torrent_id=str(n),title='Fiction',description='',labels=[]))
        self.runtime.config.recovery.entries=2
        self.runtime.provider.resolve=lambda target:(_ for _ in ()).throw(AssertionError('no remote provider'))
        self.assertEqual('CANDIDATES',self.runtime.reprofile()['phase'])
        self.assertEqual(2,self.runtime.reprofile()['checked'])
        cursor=self.repo.setting('runtime-reprofile')['cursor'];self.assertTrue(cursor)
        self.assertEqual(2,self.runtime.reprofile()['checked'])
        self.assertEqual('COMPLETE',self.runtime.reprofile()['phase'])
        with self.repo.connection() as db:
            self.assertEqual(5,db.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'runtime-policy:candidates:%'").fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM ingest_receipts').fetchone()[0])


class ReplacementTests(unittest.TestCase):
    def test_runtime_replacement_requires_physical_isolation_and_keeps_budget(self):
        import test_planner
        test_planner.AuthorityTests.setUpClass()
        f=test_planner.AuthorityTests('test_atomic_claim_before_callbacks_two_connections');f.setUp();self.addCleanup(f.doCleanups)
        m=load('runtime');r=object.__new__(m.Runtime)
        r.repository=f.repo;r.authority=f.auth;r.scheduler=f.schedule;r.verify_input=lambda saved:None
        f.claim('A')
        opportunity=f.schedule.opportunity('round');snapshot=f.spec(candidate='B')
        with patch.object(m,'instant',return_value=test_planner.NOW),self.assertRaisesRegex(ValueError,'REPLACEMENT_LAYOUT_COLLISION'):
            r.candidate_plan(opportunity,{},snapshot,round_plans=[snapshot])
        self.assertEqual({'A'},{v['owner_plan_id'] for v in f.auth.vector(f.keys).values()})
        snapshot['save_path']='/isolated-b'
        with patch.object(m,'instant',return_value=test_planner.NOW):new=r.candidate_plan(opportunity,{},snapshot,round_plans=[snapshot])
        self.assertEqual('ACTIVE',new['authorization']);self.assertEqual('SUPERSEDED',f.auth.plan('A')['authorization'])
        self.assertEqual(1,f.schedule.opportunity('round')['supersessions'])
        self.assertEqual(opportunity['created_at'],f.schedule.opportunity('round')['created_at'])
