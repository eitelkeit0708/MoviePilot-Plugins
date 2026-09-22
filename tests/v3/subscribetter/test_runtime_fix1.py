"""Directed regressions for independently reproduced A2 runtime gaps."""
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_planner import load
import test_runtime as fixtures


class RuntimeFixTests(unittest.TestCase):
    def test_immature_shared_pack_does_not_block_mature_safe_episode(self):
        import json
        from dataclasses import asdict
        from datetime import datetime,timedelta,timezone
        import test_planner as p
        p.AuthorityTests.setUpClass();f=p.AuthorityTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        config=f.s.ScheduleConfig(observation_enabled=True,base_seconds=30,quiet_seconds=20,max_seconds=90)
        with f.repo.connection(write=True) as db:
            db.execute('UPDATE opportunities SET config=? WHERE id=?',(json.dumps(asdict(config)),'round'))
        e07,e08=f.keys;base=datetime.now(timezone.utc)-timedelta(seconds=61)
        whole=f.spec(candidate='B',shared=True);whole['candidate_key']='whole'
        episode=f.spec(keys=[e07],candidate='A');episode['candidate_key']='episode'
        f.schedule.observe('round',e07,'whole',[2],eligible=True,now=base)
        f.schedule.observe('round',e08,'whole',[2],eligible=True,now=base+timedelta(seconds=40))
        m=load('runtime');r=object.__new__(m.Runtime)
        r.repository=f.repo;r.authority=f.auth;r.scheduler=f.schedule;r.verify_input=lambda _:None
        r.pipeline=SimpleNamespace(revalidate=lambda _:None)
        ranked=r.ranked([episode,whole]);op=f.schedule.opportunity('round')
        now=base+timedelta(seconds=61)
        with patch.object(f.s,'instant',lambda value=None:now if value is None else value),patch.object(f.m,'instant',lambda value=None:now if value is None else value):
            self.assertIsNone(r.candidate_plan(op,{},whole,round_plans=ranked))
            chosen=r.candidate_plan(op,{},episode,round_plans=ranked)
        self.assertIsNotNone(chosen)
        self.assertEqual('episode',chosen['snapshot']['candidate_key'])
        self.assertEqual(chosen['id'],f.auth.vector([e07])[e07]['owner_plan_id'])
        self.assertIsNone(f.auth.vector([e08])[e08]['owner_plan_id'])
        with f.repo.connection() as db:self.assertEqual(1,db.execute('SELECT count(*) FROM plans').fetchone()[0])

    def test_cold_fixture_does_not_bootstrap_available_host_sdk(self):
        import sys
        from unittest.mock import Mock
        host=Mock(side_effect=RuntimeError('host runtime is not bootstrapped'))
        public=SimpleNamespace(ModuleManager=host,PluginManager=host)
        f=fixtures.ColdExecutionTests('test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers')
        f.setUp();self.addCleanup(f.doCleanups)
        with patch.dict(sys.modules,{'app.sdk.plugin':public}):f.test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers()
        host.assert_not_called()

    def test_expired_warm_round_refreshes_exact_plan_without_duplicate_add(self):
        f=fixtures.ColdExecutionTests('test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers')
        f.setUp();self.addCleanup(f.doCleanups);m=f.m;advance=m.Runtime.advance;seen=[]
        def wrapped(r,plan,saved):
            result=advance(r,plan,saved)
            if result.get('state')=='DOWNLOADING' and not seen:
                r.pipeline.rounds[plan['snapshot']['candidate_key']]['acquired']=time.monotonic()-301
                with patch.object(r.candidates,'refresh',wraps=r.candidates.refresh) as refresh:
                    seen.append(advance(r,plan,saved)['state']);self.assertEqual(1,refresh.call_count)
            return result
        with patch.object(m.Runtime,'advance',wrapped):f.test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers()
        self.assertEqual(['DOWNLOADING'],seen)

    def fixture(self):
        f=fixtures.CommonAdmissionTests('test_manual_native_discovery_converge_and_freeze_real_config_separate_from_restore')
        f.setUp();self.addCleanup(f.doCleanups);return f

    def test_explicit_inventory_template_is_used_and_partitioned(self):
        f=self.fixture();r=f.runtime
        r.config.destination_templates.append(r.config.destination_templates[0].model_copy(update={'id':'second'}))
        r.delivery.rules['rule']['cloud_scope_id']='scope';calls=[]
        r.delivery.archive.mappings=SimpleNamespace(rules=[dict(emby_service='e',library_id='1',cloud_scope_id='scope')])
        r.delivery.archive.reconcile=lambda *a,**kw:calls.append((a,kw)) or dict(status='INCOMPLETE',scan_id='scan')
        self.assertEqual('UNKNOWN',r.inventory(f.target,template_id='second')['state'])
        self.assertEqual(('e','1'),calls[0][0])

    def test_slow_search_stops_before_evaluation_and_retains_round(self):
        f=self.fixture();r=f.runtime;r.config.recovery.seconds=.1
        r.submit('deadline',f.target,{},'test');key=f.p.TargetUnit(f.target).key
        r.owner.ensure_paused=lambda **kw:None;r.owner.reconcile=lambda:None;r.bootstrap=lambda deadline:None
        r.inventory=lambda *a,**kw:dict(state='MISSING');r.delivery.archive.current=lambda keys:{key:dict(state='MISSING')}
        calls=[]
        def search(*args):time.sleep(.15);return [dict(candidate_key='one')]
        r.search=search;r.evaluate=lambda *a:calls.append('evaluate') or dict(plans=[])
        r.repository.setting('runtime-lane',1);result=r._tick()
        self.assertEqual([],calls);self.assertEqual('TICK_DEADLINE',result['results'][0]['reason'])
        pending=f.repo.setting('runtime-round:'+result['results'][0]['opportunity_id'])
        self.assertEqual(['one'],pending['keys']);self.assertEqual(0,pending['cursor'])

    def test_unverified_subtitle_assets_do_not_reach_organization(self):
        m=load('runtime');r=object.__new__(m.Runtime);calls=[]
        plan=dict(id='p',snapshot=dict(candidate_key='c',targets={'u':{}}));r.verify_input=lambda saved:None
        executor=SimpleNamespace(_owned=lambda s:dict(state='RUNNING'),sample=lambda p:dict(status='COMPLETED'))
        r.pipeline=SimpleNamespace(rounds={'c':{'acquired':time.monotonic()}},revalidate=lambda p:{},executor=lambda:executor,
            execute=lambda *a,**k:dict(state='RUNNING',subtitles=dict(assets_state='UNVERIFIED',fresh_asset_plan_required=True)),
            organize=lambda *a:calls.append('organize') or dict(state='WAITING'))
        r.delivery=SimpleNamespace(rules={'rule':dict(local_root='/fiction')})
        result=r.advance(plan,dict(effective=dict(template=dict(organized_rule='rule'))))
        self.assertEqual([],calls);self.assertEqual('WAITING_ASSETS',result['state'])

    def test_complete_round_prefers_better_and_does_not_trust_unavailable_old_best(self):
        import test_planner as p
        p.AuthorityTests.setUpClass();f=p.AuthorityTests('test_atomic_claim_before_callbacks_two_connections');f.setUp();self.addCleanup(f.doCleanups)
        m=load('runtime');r=object.__new__(m.Runtime);r.repository=f.repo;r.authority=f.auth;r.scheduler=f.schedule;r.verify_input=lambda saved:None
        a=f.spec(candidate='A');b=f.spec(candidate='B')
        for v in b['targets'].values():v['quality']=[2]
        op=f.schedule.opportunity('round')
        with patch.object(m,'instant',return_value=p.NOW):
            self.assertIsNone(r.candidate_plan(op,{},a,round_plans=[a,b]))
            for k in f.keys:f.schedule.observe('round',k,'unavailable',[3],eligible=True,now=p.NOW)
            claimed=r.candidate_plan(op,{},b,round_plans=[a,b])
        self.assertEqual('B',claimed['snapshot']['candidate_key'])

    def test_local_scan_cleanup_lane_is_bounded_and_checks_each_permission(self):
        f=self.fixture();r=f.runtime;calls=[]
        r.delivery.local_maintenance=lambda **kw:calls.append(kw) or dict(state='LOCAL_CHECKED')
        r.owner.ensure_paused=lambda **kw:None;r.repository.setting('runtime-lane',5)
        result=r._tick();self.assertEqual('LOCAL_CHECKED',result['results'][0]['state']);self.assertEqual(1,len(calls))

    def test_archive_source_proof_reaches_real_evidence_confirmation(self):
        import test_archive
        test_archive.ArchiveTests.setUpClass();f=test_archive.ArchiveTests('test_enrichment_consumes_once_without_publish_plan_or_cooldown')
        f.setUp();self.addCleanup(f.doCleanups);f.enrichment()
        with f.repo.connection() as db:candidate=__import__('json').loads(db.execute("SELECT data FROM candidates WHERE candidate_key='release'").fetchone()[0])
        candidate['candidate_key']='release'
        proof=f.archive.candidate_evidence(candidate,[f.key])
        self.assertTrue(proof['same_video_verified'][f.key]);self.assertTrue(proof['same_assets_verified'])
        result=f.archive.enrich_evidence('o','release',f.enrichments,proof['manifest'])
        self.assertTrue(result['accepted']);self.assertIsNone(f.s.Scheduler(f.repo).target(f.key)['last_ingest_confirmed_at'])

    def test_local_sidecar_window_and_new_plan_preserve_source_table(self):
        import test_planner as p
        from datetime import timedelta
        from pathlib import Path
        import tempfile
        p.AuthorityTests.setUpClass();f=p.AuthorityTests('test_atomic_claim_before_callbacks_two_connections');f.setUp();self.addCleanup(f.doCleanups)
        tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        snap=f.spec(candidate='A');snap['save_path']=root.as_posix()[2:] if root.drive else root.as_posix()
        for item in snap['torrent_files']:
            path=root/item['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*item['size'])
        video=next(i for i in snap['torrent_files'] if i['role']=='video');path=root/video['path'];path.with_suffix('.zh.vtt').write_text('WEBVTT\n')
        f.auth.prepare('original','round',snap,now=p.NOW);f.auth.claim('original',f.auth.vector(f.keys),now=p.NOW)
        m=load('runtime_assets');pipeline=SimpleNamespace(service=SimpleNamespace(repository=f.repo),active=lambda:None)
        plan=f.auth.plan('original')
        with patch.object(m,'instant',return_value=p.NOW):self.assertEqual('WAITING_ASSETS',m.observe(pipeline,plan,seconds=1)['state'])
        with patch.object(m,'instant',return_value=p.NOW+timedelta(seconds=2)):observed=m.observe(pipeline,plan,seconds=1)
        self.assertEqual('VERIFIED',observed['state']);self.assertEqual(1,len(observed['assets']))
        new=dict(snap,source_plan='original',local_assets=observed['assets'],selected_indices=snap['selected_indices']+[len(snap['torrent_files'])])
        f.auth.extend_assets('original','expanded',new,f.auth.vector(f.keys))
        self.assertEqual(snap,f.auth.plan('original')['snapshot']);self.assertEqual(snap['torrent_files'],f.auth.plan('expanded')['snapshot']['torrent_files'])
        self.assertEqual(0,f.schedule.opportunity('round')['supersessions'])
        self.assertEqual(snap['selected_indices'],f.auth.active_files(snap['downloader'],snap['infohash'],snap['save_path']))
        path.with_suffix('.zip').write_bytes(b'not an extracted subtitle')
        with self.assertRaisesRegex(ValueError,'SUBTITLE_ARCHIVE_PENDING'):m.observe(pipeline,plan,seconds=1)

    def test_legacy_fallback_validates_copy_but_preserves_transfer_hash(self):
        f=self.fixture();r=f.runtime;r.submit('legacy',f.target,{},'test')
        with f.repo.connection() as db:op=db.execute('SELECT id FROM opportunities').fetchone()[0]
        key=f.p.TargetUnit(f.target).key
        snapshot=dict(candidate_key='c',infohash='a'*40,downloader='qb',save_path='/downloads',policy_revision=r.policy.semantic_hash,parse_revision='parse-v1',
            current={key:dict(state='MISSING',revision=0)},targets={key:dict(action='ACQUIRE',reason='MISSING',quality=[1],evidence_keys=[],evidence_source='none')},
            torrent_files=[dict(index=0,path='movie.mkv',size=1,role='video',targets=[key],requires=[])],selected_indices=[0],verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
        r.authority.prepare('p',op,snapshot);r.plugin._ordinary_work_active=lambda:False
        c=f.c;dm=load('delivery');json=__import__('json')
        from pathlib import PurePosixPath
        class HostPath(PurePosixPath):
            def is_symlink(self):return False
            def absolute(self):return self
            def exists(self):return False
        paths=patch.object(dm,'Path',HostPath);paths.start();self.addCleanup(paths.stop)
        mapping=dict(id='m',revision='1',emby_service='e',library_id='1',cloud_scope_id='cloud',local_strm_prefix='/strm',emby_prefix='/emby',playback_prefix='/play',cd2_prefix='/115')
        rule=c.DeliveryRule(id='legacy',local_root='/local',staging_root='/115/staging',incoming_root='/115/incoming',cloud_scope_id='cloud',consumer_roots=['/115/incoming'],fallback=True,fallback_gb=.3).model_dump()
        rule['fallback_gb']='0.3'
        config=dict(cloud_scopes={'cloud':dict(cd2_plugin='Cloud',p115_plugin='Disk',root='/115',allowed_prefixes=['/115'])},libraries={'e':['1']},policy_bindings={},classification_revision=1,mappings=[mapping],rules=[rule])
        with self.assertRaises(Exception):c.DeliveryConfig.model_validate(config)
        original=dm.validate_rules([rule])['legacy']['transfer_revision']
        bundle=dict(plan_id='p',rule_id='legacy',rule_transfer_revision=original,staging='/115/staging/b',incoming='/115/incoming/b',files=[],manifest={'old':'unchanged'})
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO delivery_bundles VALUES(?,?,?,?,?,?,?)',('b','p','legacy','UNKNOWN','',0,json.dumps(bundle)))
        self.assertEqual('BOUND',r.bind_scope('b',config,0,'test')['state'])
        saved=f.repo.setting('delivery-scope:b');self.assertEqual('0.3',saved['config']['rules'][0]['fallback_gb'])
        self.assertEqual(original,dm.validate_rules(saved['config']['rules'])['legacy']['transfer_revision'])
        config['rules'][0]['fallback_gb']='NaN'
        with self.assertRaises(Exception):r.bind_scope('b',config,0,'test')

    def test_proven_current_video_selects_only_local_sidecar_and_never_downloads_video(self):
        import test_archive,json,tempfile,shutil
        from pathlib import Path
        from datetime import timedelta
        test_archive.ArchiveTests.setUpClass();f=test_archive.ArchiveTests('test_enrichment_consumes_once_without_publish_plan_or_cooldown')
        f.setUp();self.addCleanup(f.doCleanups);task=f.enrichment()
        f.archive.enrich_evidence('o','release',f.enrichments,f.enrich_manifest)
        f.s.Scheduler(f.repo).open_opportunity('sidecar-o',task['id'],[f.a.TargetUnit(f.r.Target.from_task(task))],mode='ONESHOT',config=f.s.ScheduleConfig(observation_enabled=False))
        tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(tmp.cleanup);root=Path(tmp.name);out=root/'out';out.mkdir()
        (root/'movie.mkv').write_bytes(b'x'*100);(root/'movie.zh.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nTest\n')
        em=load('execution');strong,sha=em.asset_hashes(root/'movie.zh.srt');now=f.s.instant()
        asset=dict(file=dict(index=1,path='movie.zh.srt',size=(root/'movie.zh.srt').stat().st_size,role='subtitle',targets=[f.key],requires=[]),
            sha256=strong,sha1=sha,mtime_ns=(root/'movie.zh.srt').stat().st_mtime_ns,stable_since=(now-timedelta(seconds=2)).isoformat(),observed_at=now.isoformat())
        with f.repo.connection() as db:raw=json.loads(db.execute("SELECT data FROM candidates WHERE candidate_key='release'").fetchone()[0])
        facts=f.policy.normalize(dict(f.archive.current([f.key])[f.key]['versions'][0].facts.raw))
        path=root.as_posix()[2:] if root.drive else root.as_posix()
        candidate=dict(candidate_key='release',infohash=raw['infohash'],torrent_files=raw['torrent_files'],facts={f.key:facts},classification=raw['classification'],
            downloader='test',save_path=path,parse_revision='parse',available=True,identity_ok=True,scope_ok=True,parse_status='OK',files_verified=True,configuration_verified=True,
            local_assets=[asset],source_plan='source-plan')
        cm=load('candidates');calls=[]
        class Client:
            def task(self,infohash):return dict(id='owned',infohash=infohash,save_path=path,state='COMPLETED')
            def files(self,infohash):return [dict(id=7,path='movie.mkv',size=100,wanted=True,completed=100)]
            def add(self,*args):calls.append('add');raise AssertionError('video must not download')
        service=SimpleNamespace(repository=f.repo,adapter=SimpleNamespace(classify=lambda media:raw['classification']))
        pipeline=cm.CandidatePipeline(service,SimpleNamespace(corrector=SimpleNamespace(revision='parse')),f.policy,f.archive.current,lambda name:Client())
        result=pipeline._evaluate(candidate,[f.key],'episode');self.assertEqual('SIDECAR_SUPPLEMENT',result['decisions'][f.key]['action'],result)
        snap=result['plans'][0];self.assertEqual([1],snap['selected_indices']);self.assertEqual(raw['torrent_files'],snap['torrent_files'])
        pipeline.rounds['release']=dict(candidate=candidate,media=object(),mode='episode',acquired=time.monotonic())
        # Original source plan is an immutable /download fixture. Use that exact
        # approved root through a local filesystem projection for this test.
        source=f.archive.authority.plan('source-plan')['snapshot']
        source_new=dict(source,save_path=path)
        auth=f.archive.authority;auth.prepare('local-source','sidecar-o',source_new)
        candidate['source_plan']='local-source';snap['source_plan']='local-source'
        auth.prepare('sidecar','sidecar-o',snap);auth.claim('sidecar',auth.vector([f.key]))
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,client_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                ('test',raw['infohash'],path,json.dumps([['movie.mkv',100]]),'owned','source-plan','RUNNING','owned',f.r.utcnow()))
        executor=pipeline.executor();executor.verify_torrent=lambda body:(raw['infohash'],[('movie.mkv',100)])
        self.assertEqual('RUNNING',executor.execute('sidecar',b'fixture-torrent')['state'])
        host=SimpleNamespace(history=lambda *args:True,transfer=lambda source,destination,item,snapshot:shutil.copyfile(source,destination/source.name))
        result=em.Organizer(executor,host).organize('sidecar',out)
        self.assertEqual('COMPLETE',result['state'],result);self.assertEqual(['movie.zh.srt'],[p.name for p in out.iterdir()]);self.assertEqual([],calls)

    def test_search_budget_stops_new_http_after_slow_page_size(self):
        f=self.fixture();m=load('candidates');calls=[]
        def page(*args):calls.append('page-size');time.sleep(.03);return 20
        adapter=SimpleNamespace(sites=lambda:[dict(id=1)],page_size=page,search=lambda *args:calls.append('search') or [])
        service=m.CandidateService(f.repo,adapter)
        service.search([1],['one','two'],m.SearchBudget(2,2,1,100,8,0),deadline=time.monotonic()+.01)
        self.assertEqual(['page-size'],calls)

    def test_consumer_budget_stops_between_provider_and_emby(self):
        m=load('runtime_delivery');f=self.fixture();key=f.p.TargetUnit(f.target).key;calls=[]
        f.runtime.delivery.archive.mappings=SimpleNamespace(rules=[dict(emby_service='e',library_id='1',cloud_scope_id='c')],revision='1')
        f.runtime.delivery.archive.repository=f.repo
        f.runtime.delivery.archive.sources=SimpleNamespace(emby_target_page=lambda *a:calls.append('emby'))
        def provider(target):time.sleep(.03);return dict(units=[key])
        with self.assertRaisesRegex(ValueError,'TICK_DEADLINE'):
            m.observe_consumer(f.runtime.delivery,dict(id='b',manifest=dict(publication={key:{}})),provider,seconds=.01)
        self.assertEqual([],calls)

    def test_public_nested_plugin_callbacks_are_not_mistaken_for_disabled(self):
        from types import MappingProxyType
        for projection,expected in (({('p','name'):MappingProxyType({'download_added':lambda:None})},'DISPATCHED'),
                                    ({('p','name'):{}},'NOOP'),({('p','name'):object()},'DISPATCHED')):
            with self.subTest(expected=expected):
                f=fixtures.ColdExecutionTests('test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers')
                f.setUp();self.addCleanup(f.doCleanups)
                f.public_plugin_callbacks=SimpleNamespace(ModuleManager=lambda:SimpleNamespace(get_running_modules=lambda method:iter(())),
                    PluginManager=lambda:SimpleNamespace(get_plugin_modules=lambda:projection))
                f.test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers()
                state=f.repo.setting('subtitle:cold')
                if expected=='NOOP':self.assertIn('download_added',state['noops'])
                else:self.assertEqual('DISPATCHED',next(w['state'] for w in state['workflows'] if w['workflow']=='download_added'))

if __name__=='__main__':unittest.main()
