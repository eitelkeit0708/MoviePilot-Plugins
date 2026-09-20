"""W10A1 local config/migration boundaries; no host writes, network or real secrets."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from test_planner import load, PLUGIN


class PrivateFixture:
    """Fictional in-memory substitute for the POSIX-only private store."""
    def __init__(self): self.values = {}; self.blobs = {}
    def put(self, value):
        import hashlib
        ref = 'secret:' + hashlib.sha256(value.encode()).hexdigest()[:32]
        self.values[ref] = value
        return ref
    def resolve(self, ref): return self.values[ref]
    def put_snapshot(self, value):
        import hashlib
        ref = 'snapshot:' + hashlib.sha256(value).hexdigest()
        self.blobs[ref] = value
        return ref
    def read_snapshot(self, ref): return self.blobs[ref]


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PLUGIN/'configuration.py').exists(), 'W10A1 strict configuration boundary missing')
        self.c = load('configuration'); self.m = load('migration'); self.r = load('repository')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = self.r.Repository(Path(self.tmp.name)/'state.db')
        self.store = PrivateFixture(); self.saved = []
        self.config = self.c.Configuration(self.repo, 'SubscriBetter', self.store, self.saved.append)
        self.config.initialize({})
        self.inventory = {'generation': 1, 'plugins': [], 'handlers': [], 'services': [], 'jobs': []}
        self.migration = self.m.Migration(self.repo, self.config, self.store, lambda: copy.deepcopy(self.inventory))

    def apply(self, changes):
        view = self.config.view()
        preview = self.config.preview(changes, view['revision'], view['digest'], 'admin')
        self.assertTrue(preview['valid'], preview)
        self.config.initialize(preview['config'])
        return self.config.view()

    def test_native_save_requires_preflight_and_preserves_last_valid_nested_values(self):
        self.apply({'ai_assist': {'timeout': 41, 'positive_ttl': 0}, 'candidates': {'site_ids': [7]}})
        before = self.config.view()
        self.config.initialize({'enabled': True, 'dry_run': False, 'ai_assist': {'secret_key': 'SENTINEL'}})
        self.assertEqual(before['digest'], self.config.view()['digest'])
        self.assertFalse(self.config.ready)
        self.assertNotIn('SENTINEL', json.dumps(self.saved + [self.config.view()]))
        self.config.initialize(before['config'])
        self.assertTrue(self.config.ready)
        later = self.apply({'ai_assist': {'negative_ttl': 0}})
        self.assertEqual(41, later['config']['ai_assist']['timeout'])
        self.assertEqual([7], later['config']['candidates']['site_ids'])
        with self.assertRaises(ValueError): self.config.preview({}, before['revision'], before['digest'], 'admin')
        def unavailable(_):raise RuntimeError('PRIVATE_RUNTIME_FAILURE')
        self.config.validate_references=unavailable
        self.config.initialize(later['config'])
        self.assertFalse(self.config.ready);self.assertEqual(later['config'],self.saved[-1])
        self.assertNotIn('PRIVATE_RUNTIME_FAILURE',json.dumps(self.config.errors))

    def test_strict_configuration_rejects_unknown_nested_and_unsafe_dependencies(self):
        for change in ({'ai_assist': {'timeout': float('inf')}}, {'discovery': {'unknown': 1}},
                       {'permissions': {'delete_downloader_data_enabled': 'true'}},
                       {'schedule': {'observation_enabled': True}},
                       {'discovery': {'cron': 'invalid'}},
                       {'destination_templates': [{'id':'x','category_id':'absent','downloader':'q','save_path':'../bad'}]}):
            with self.subTest(change=change):
                view=self.config.view(); p=self.config.preview(change, view['revision'], view['digest'], 'admin')
                self.assertFalse(p['valid'])
        default=self.c.Config().model_dump()
        self.assertFalse(default['enabled']);self.assertTrue(default['dry_run'])
        self.assertEqual([False]*5,list(default['permissions'].values()))

    def test_bootstrap_restart_cannot_bypass_preview_or_consume_another_preview(self):
        repo=self.r.Repository(Path(self.tmp.name)/'bootstrap.db')
        cfg=self.c.Configuration(repo,'New',self.store,self.saved.append)
        cfg.initialize({'enabled':True,'dry_run':False})
        self.assertFalse(cfg.ready)
        restarted=self.c.Configuration(repo,'New',self.store,self.saved.append)
        restarted.initialize(cfg.view()['config'])
        self.assertFalse(restarted.ready)
        current=restarted.view()
        p=restarted.preview({},current['revision'],current['digest'],'admin')
        restarted.initialize(p['config']);self.assertTrue(restarted.ready)
        current=restarted.view()
        other=restarted.preview({'enabled':False},current['revision'],current['digest'],'admin')
        bypass=dict(current['config'],configuration_receipt=other['receipt_id'])
        restarted.initialize(bypass)
        self.assertFalse(restarted.ready)
        with repo.connection() as db:
            self.assertEqual('PREVIEW',db.execute('SELECT state FROM migration_receipts WHERE id=?',(other['receipt_id'],)).fetchone()[0])
        p=self.config.preview({'PRIVATE_UNKNOWN_SENTINEL':'value'},self.config.view()['revision'],self.config.view()['digest'],'admin')
        self.assertNotIn('PRIVATE_UNKNOWN_SENTINEL',json.dumps(p))

    def test_existing_w07_rule_permission_preserves_only_identical_explicit_permission(self):
        repo=self.r.Repository(Path(self.tmp.name)/'old-delivery.db')
        cfg=self.c.Configuration(repo,'New',self.store,self.saved.append)
        cfg.initialize({'delivery':{'cloud_scopes':{'main':{'cd2_plugin':'CD2','p115_plugin':'P115','root':'/cloud','allowed_prefixes':['/cloud']}},
            'rules':[{'id':'old','local_root':'/local','staging_root':'/cloud/staging','incoming_root':'/cloud/incoming',
            'consumer_roots':['/cloud/incoming'],'cloud_scope_id':'main','cleanup_success':True}]}})
        self.assertTrue(cfg.ready)
        self.assertEqual({'cleanup_success':True,'cleanup_abandoned':False,'cleanup_staging':False,
            'remove_downloader_task_enabled':False,'delete_downloader_data_enabled':False},cfg.view()['config']['permissions'])
        self.assertTrue(cfg.view()['config']['delivery']['rules'][0]['cleanup_success'])

    def test_discovery_owner_uses_existing_digest_and_actual_scheduler_identity(self):
        discovery=load('discovery')
        self.apply({'enabled':True,'dry_run':False,'discovery':{'enabled':True,'rsshub_base_url':'https://rss.invalid','media_type_allowlist':['电影'],
            'sources':[{'id':'weekly','kind':'rsshub','route_key':'movie_weekly_best'}]}})
        feature=self.migration.feature('discovery','weekly')
        self.assertEqual(discovery._digest(self.config.view()['config']['discovery']),feature['config_digest'])
        self.inventory.update(plugins=[dict(id='SubscriBetter',source='SubscriBetter',prefix='fixture.New',
            config=self.config.view()['config'],active=True,loaded=True,version='1')],
            services=[dict(instance_id='SubscriBetter',id='SubscriBetter_discovery',callable=True,handler='fixture.New.discovery_tick')],
            jobs=[dict(id='SubscriBetter_SubscriBetter_discovery',status='normal')])
        p=self.migration.preview_cutover([feature],[],'admin')
        active=self.migration.advance(p['receipt_id'],1,p['digest'],'activate','activate','admin')
        self.assertEqual('ACTIVE',active['state']);self.assertIsNotNone(self.migration.unique_owner(**feature))
        self.migration.preview_cutover([feature],[],'admin')
        self.assertIsNotNone(self.migration.unique_owner(**feature),'preview is read-only with respect to active owner')
        self.inventory['jobs']=[]
        self.assertIsNone(self.migration.unique_owner(**feature))

    def test_legacy_all_named_fields_raw_snapshot_private_and_reimport_after_edit(self):
        ai = dict(enabled=True,recognize=True,openai_url='https://private.invalid/custom',openai_key='KEY_SENTINEL,second',
            model='explicit',request_profile='generic',compatible=True,proxy=True,customize_prompt='  custom prompt\n',
            previous_customize_prompt=' backup\n',restore_prompt=True,clear_cache=True,timeout=44,max_attempts=3,
            max_concurrency=4,positive_ttl=0,negative_ttl=7,cache_size=27,notify=True,chat_enabled=True,
            statistics={'calls':9},chat_history=['evidence only'],unknown_field='retained')
        ranks=['movie-ustop','movie-weekly','movie-real-time','show-domestic','movie-hot-gaia','tv-hot','movie-top250','movie-top250-full']
        discovery=dict(enabled=True,ranks=ranks,rss_addrs='https://rss.invalid/a@@TV;/movie#/tv;@tv@',cron='',onlyonce=True,
            proxy=True,sleep_time='2,5',is_exit_ip_rate_limit=True,vote='8.1',release_year='2020',is_only_movies=False,
            is_seasons_all=True,history_type='最新12条历史',clear=True,clear_unrecognized=True,delete_history=True,
            migrate_from_url='https://old.invalid',migrate_api_token='TOKEN_SENTINEL',migrate_once=True)
        history=[dict(title='Original',type='电视剧',year='0',poster='https://poster.invalid/?token=TOKEN_SENTINEL',
            overview='old',tmdbid='00042',doubanid='0',unique='Original2020',time='09-19',time_full='2026-09-19 12:00:00',
            vote=0,status=s,unknown='preserved') for s in self.m.LEGACY_STATUSES]
        raw=json.dumps({'ai':ai,'discovery':discovery,'history':history},ensure_ascii=False,indent=2).encode()
        p=self.migration.preview_import(raw, 'fixture', '1.4.2/1.0.7', None, 'admin')
        self.assertEqual(raw,self.store.read_snapshot(p['snapshot_ref']))
        public=json.dumps(p,ensure_ascii=False)
        for secret in ('KEY_SENTINEL','TOKEN_SENTINEL','https://private.invalid'): self.assertNotIn(secret,public)
        self.assertTrue(set(ai) <= {x['field'] for x in p['fields'] if x['source']=='ai'})
        self.assertTrue(set(discovery) <= {x['field'] for x in p['fields'] if x['source']=='discovery'})
        imported=self.migration.import_page(p['receipt_id'],p['revision'],p['digest'],0,3,'import-1','admin')
        self.assertEqual(3,imported['cursor'])
        imported=self.migration.import_page(p['receipt_id'],imported['revision'],p['digest'],3,100,'import-2','admin')
        self.assertEqual(7,imported['cursor']);self.assertEqual('IMPORTED',imported['state'])
        config=self.config.view()['config'];self.assertFalse(config['enabled']);self.assertFalse(config['ai_assist']['enabled'])
        self.assertFalse(config['discovery']['enabled']);self.assertEqual(0,config['ai_assist']['positive_ttl'])
        self.assertEqual('  custom prompt\n',config['ai_assist']['prompt']);self.assertEqual(' backup\n',config['ai_assist']['prompt_backup'])
        for key,expected in {'name_assistance_enabled':True,'model':'explicit','profile':'generic','compatible':True,'proxy':True,
            'timeout':44,'max_attempts':3,'max_concurrency':4,'positive_ttl':0,'negative_ttl':7,'cache_size':27,
            'notifications':True,'chat_enabled':False,'name_recognize_bridge':False}.items():
            with self.subTest(legacy_ai=key):self.assertEqual(expected,config['ai_assist'][key])
        self.assertEqual(['KEY_SENTINEL','second'],[self.store.resolve(r) for r in config['ai_assist']['credential_refs']])
        self.assertEqual(ai['openai_url'],self.store.resolve(config['ai_assist']['endpoint_ref']))
        for key,expected in {'cron':'0 8 * * *','minimum_rating':8.1,'minimum_release_year':2020,
            'season_scope':'all_known','media_type_allowlist':[],'rating_source':'recognized_provider'}.items():
            with self.subTest(legacy_discovery=key):self.assertEqual(expected,config['discovery'][key])
        budget=config['discovery']['request_budget']
        self.assertEqual((2,5),(budget['interval_min_seconds'],budget['interval_max_seconds']))
        self.assertTrue(all(s['proxy'] and not s['enabled'] for s in config['discovery']['sources']))
        self.assertEqual(discovery['rss_addrs'],next(s['legacy_original_text'] for s in config['discovery']['sources'] if s['kind']=='custom'))
        self.assertEqual('latest12',config['history_view'])
        for field in ('onlyonce','clear','clear_unrecognized','delete_history','migrate_once','restore_prompt','clear_cache'):
            with self.subTest(one_shot=field):self.assertEqual('ACTION_NOT_REPLAYED',next(f['status'] for f in p['fields'] if f['field']==field))
        for field in ('migrate_from_url','migrate_api_token'):
            self.assertEqual(discovery[field],self.store.resolve(p['private_refs'][field]))
        projected=self.migration.history(p['receipt_id'],100,0)
        self.assertEqual(list(self.m.LEGACY_STATUSES),[r['raw']['status'] for r in projected])
        for row in projected:
            self.assertEqual(set(self.m.HISTORY_FIELDS),set(row['raw']))
            self.assertEqual({'themoviedb':'00042'},row['identities'])
            self.assertIsNone(row['season']);self.assertIsNone(row['source_timezone'])
            self.assertEqual('LEGACY_UNVERIFIED',row['state'])
            self.assertIn('TIMEZONE_UNKNOWN',row['diagnostics']);self.assertIn('RATING_UNKNOWN',row['diagnostics'])
        self.apply({'ai_assist':{'model':'user-edited'}})
        again=self.migration.import_page(p['receipt_id'],imported['revision'],p['digest'],7,100,'import-3','admin')
        self.assertEqual('user-edited',self.config.view()['config']['ai_assist']['model'])
        self.assertEqual(7,len(self.migration.history(p['receipt_id'],100,0)))
        self.assertEqual(again['cursor'],7)
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM plan_actions').fetchone()[0])

    def test_owner_no_overlap_fresh_drift_partial_cutover_and_rollback(self):
        self.apply({'enabled':True,'dry_run':False,'ai_assist':{'enabled':True,'endpoint_ref':self.store.put('https://api.invalid'),
            'credential_refs':[self.store.put('fiction')],'model':'fixture','name_recognize_bridge':True}})
        self.inventory['plugins']=[dict(id='SubscriBetter',source='SubscriBetter',prefix='fixture.SubscriBetter',
            config=self.config.view()['config'],active=True,loaded=True,version='1.0.0')]
        self.inventory['event_types']={'name_bridge':'name','chat':'message'}
        self.inventory['handlers']=[{'event_type':'name','handler_identifier':'fixture.SubscriBetter.ai_name','status':'enabled'}]
        feature=self.migration.feature('name_bridge',{'event':'NameRecognize'})
        p=self.migration.preview_cutover([feature],[], 'admin')
        active=self.migration.advance(p['receipt_id'],p['revision'],p['digest'],'activate','op1','admin')
        self.assertEqual('ACTIVE',active['state'])
        self.assertEqual('ACTIVE',self.migration.unique_owner(**feature)['status'])
        self.inventory['plugins'][0]['config']['ai_assist']['model']='drift'
        self.assertIsNone(self.migration.unique_owner(**feature))
        self.inventory['plugins'][0]['config']=self.config.view()['config']
        rollback=self.migration.advance(active['receipt_id'],active['revision'],active['digest'],'rollback','op2','admin')
        self.assertEqual('ROLLBACK_FENCED',rollback['state'])
        self.assertIsNone(self.migration.unique_owner(**feature))
        self.assertFalse(rollback['next_changes'][0]['changes']['ai_assist']['name_recognize_bridge'])

    def test_import_restart_after_config_write_resumes_without_overwriting_user_edit(self):
        from unittest.mock import patch
        raw=json.dumps({'ai':{'model':'old'},'history':[{'status':'已添加订阅'}]}).encode()
        p=self.migration.preview_import(raw,'fixture','1',None,'admin')
        original=self.config.import_disabled
        def crash(*args):
            original(*args)
            raise RuntimeError('controlled process exit after safe config write')
        with patch.object(self.config,'import_disabled',side_effect=crash):
            with self.assertRaises(RuntimeError):self.migration.import_page(p['receipt_id'],p['revision'],p['digest'],0,100,'crash','admin')
        self.apply({'ai_assist':{'model':'user after crash'}})
        restarted=self.m.Migration(self.repo,self.config,self.store,lambda:self.inventory)
        resumed=restarted.import_page(p['receipt_id'],p['revision'],p['digest'],0,100,'resume','admin')
        self.assertEqual('IMPORTED',resumed['state'])
        self.assertEqual('user after crash',self.config.view()['config']['ai_assist']['model'])

    def test_cutover_selected_legacy_readbacks_unknown_clone_and_restart(self):
        self.apply({'enabled':True,'dry_run':False,'ai_assist':{'enabled':True,'endpoint_ref':self.store.put('https://api.invalid'),
            'credential_refs':[self.store.put('fiction')],'model':'fixture','name_recognize_bridge':True}})
        config=self.config.view()['config']
        own=dict(id='SubscriBetter',source='SubscriBetter',prefix='fixture.New',config=config,active=True,loaded=True,version='1')
        old=dict(id='Old',source='ChatGPTPlusUltra',prefix='fixture.Old',active=True,loaded=True,version='1.4.2',
            config={'enabled':True,'recognize':True,'chat_enabled':True,'model':'unchanged','openai_key':'PRIVATE'})
        self.inventory.update(plugins=[own,old],event_types={'name_bridge':'name'},handlers=[
            dict(event_type='name',handler_identifier='fixture.New.ai_name',status='enabled'),
            dict(event_type='name',handler_identifier='fixture.Old.recognize',status='enabled')])
        feature=self.migration.feature('name_bridge',{'event':'NameRecognize'})
        p=self.migration.preview_cutover([feature],[dict(instance_id='Old',module='name_bridge',config_digest=self.c.digest(old['config']))],'admin')
        self.assertNotIn('PRIVATE',json.dumps(p));self.assertEqual({'recognize':False},p['steps'][0]['changes'])
        waiting=self.migration.advance(p['receipt_id'],1,p['digest'],'activate','wait','admin')
        self.assertEqual('WAIT_HOST_SAVE',waiting['state']);self.assertIsNone(self.migration.unique_owner(**feature))
        old['config']['recognize']=False
        restarted=self.m.Migration(self.repo,self.config,self.store,lambda:copy.deepcopy(self.inventory))
        pending=restarted.advance(p['receipt_id'],waiting['revision'],p['digest'],'activate','persisted-only','admin')
        self.assertEqual('WAIT_OWNER',pending['state'])
        self.assertIn('LEGACY_RUNTIME_FLAGS_UNVERIFIED:Old',pending['diagnostics'])
        self.assertIsNone(restarted.unique_owner(**feature))
        self.inventory['handlers'][1]['status']='disabled'
        active=restarted.advance(p['receipt_id'],pending['revision'],p['digest'],'activate','readback','admin')
        self.assertEqual('ACTIVE',active['state']);self.assertTrue(old['config']['chat_enabled'])
        self.inventory['handlers'].append(dict(event_type='name',handler_identifier='unknown.Clone.respond',status='enabled'))
        self.assertIsNone(restarted.unique_owner(**feature))
        self.inventory['handlers'].pop()
        rollback=restarted.advance(p['receipt_id'],active['revision'],p['digest'],'rollback','rollback','admin')
        with self.assertRaises(ValueError):restarted.advance(p['receipt_id'],rollback['revision'],p['digest'],'rollback_readback','early','admin')
        own['config']['ai_assist']['name_recognize_bridge']=False
        old['config']['model']='external edit'
        with self.assertRaises(ValueError):restarted.advance(p['receipt_id'],rollback['revision'],p['digest'],'rollback_readback','conflict','admin')
        self.assertIsNone(restarted.unique_owner(**feature))

    def test_narrow_legacy_read_requires_preview_and_never_follows_user_method_or_route(self):
        import asyncio
        endpoint=self.store.put('https://old.invalid');token=self.store.put('PRIVATE_READ_TOKEN')
        p=self.migration.preview_source(endpoint,token,'DoubanRankPlusOptimized',[], 'admin')
        self.assertNotIn('PRIVATE_READ_TOKEN',json.dumps(p))
        calls=[]
        async def read(base,credential,instance,ranges):
            calls.append((base,credential,instance,ranges))
            return [json.dumps({'enabled':True,'ranks':['movie-weekly']}).encode(),b'[]']
        result=asyncio.run(self.migration.read_source(p['receipt_id'],p['revision'],p['digest'],'read1','admin',read=read))
        self.assertEqual('READ_DONE',result['state']);self.assertEqual(1,len(calls))
        with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
        again=asyncio.run(self.migration.read_source(p['receipt_id'],p['revision'],p['digest'],'read1','admin',read=read))
        self.assertEqual(result,again);self.assertEqual(1,len(calls))
        with self.assertRaises(ValueError):self.migration.preview_source(endpoint,token,'../../delete_history',[],'admin')

    def test_schema9_migration_preserves_all_existing_rows_and_columns(self):
        task=self.repo.submit('old-stopped',self.r.Target('电影','themoviedb','42'),{'name':'Old'},'admin')
        self.repo.set_state(task['id'],'STOPPED','admin')
        with self.repo.connection(write=True) as db:
            db.execute('DROP TABLE migration_history');db.execute('DROP TABLE migration_receipts')
            db.execute('PRAGMA user_version=9')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            before={t:([tuple(r) for r in db.execute('PRAGMA table_info('+t+')')],
                       [tuple(r) for r in db.execute('SELECT * FROM '+t)]) for t in tables}
        migrated=self.r.Repository(self.repo.path)
        with migrated.connection() as db:
            self.assertEqual(10,db.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(before,{t:([tuple(r) for r in db.execute('PRAGMA table_info('+t+')')],
                       [tuple(r) for r in db.execute('SELECT * FROM '+t)]) for t in tables})
            self.assertEqual([],list(db.execute('PRAGMA foreign_key_check')))
            self.assertEqual('ok',db.execute('PRAGMA integrity_check').fetchone()[0])
            for name in ('migration_receipts','migration_history'):
                self.assertEqual(0,db.execute('SELECT count(*) FROM '+name).fetchone()[0])

    def test_legacy_whole_instance_requires_known_capabilities_and_stopped_jobs(self):
        self.apply({'enabled':True,'dry_run':False,'discovery':{'enabled':True,'rsshub_base_url':'https://rss.invalid',
            'sources':[{'id':'weekly','kind':'rsshub','route_key':'movie_weekly_best'}]}})
        feature=self.migration.feature('discovery','weekly')
        own=dict(id='SubscriBetter',source='SubscriBetter',prefix='fixture.New',config=self.config.view()['config'],active=True,loaded=True,version='1')
        old=dict(id='Old',source='DoubanRankPlusOptimized',prefix='fixture.Old',config={'enabled':True,'ranks':['movie-weekly'],'cron':''},
            active=True,loaded=True,version='1.0.7',api_paths=['/delete_history','/migrate-config','/migrate-history'],commands=[])
        own_service=dict(instance_id='SubscriBetter',id='SubscriBetter_discovery',callable=True,handler='fixture.New.discovery_tick')
        old_service=dict(instance_id='Old',id='legacy',callable=True,handler='fixture.Old.__start_task')
        self.inventory.update(plugins=[own,old],services=[own_service,old_service],
            jobs=[dict(id='SubscriBetter_SubscriBetter_discovery',status='normal'),dict(id='Old_legacy',status='normal')])
        choice=dict(instance_id='Old',module='discovery',config_digest=self.c.digest(old['config']))
        with self.assertRaises(ValueError):self.migration.preview_cutover([feature],[choice],'admin')
        choice.update(whole_instance=True,all_capabilities=['discovery'])
        old['commands']=['unselected']
        with self.assertRaises(ValueError):self.migration.preview_cutover([feature],[choice],'admin')
        old['commands']=[]
        p=self.migration.preview_cutover([feature],[choice],'admin')
        self.assertEqual({'enabled':False},p['steps'][0]['changes'])
        old['config']['enabled']=False
        waiting=self.migration.advance(p['receipt_id'],p['revision'],p['digest'],'activate','partial','admin')
        self.assertEqual('WAIT_OWNER',waiting['state']);self.assertIsNone(self.migration.unique_owner(**feature))
        self.inventory['services']=[own_service];self.inventory['jobs'].pop()
        active=self.migration.advance(p['receipt_id'],waiting['revision'],p['digest'],'activate','fresh','admin')
        self.assertEqual('ACTIVE',active['state'])
        self.inventory['plugins'].append(dict(own,id='Clone'))
        self.assertIsNone(self.migration.unique_owner(**feature))
        self.inventory['plugins'].pop()
        rollback=self.migration.advance(p['receipt_id'],active['revision'],p['digest'],'rollback','stop-new','admin')
        own['config']['discovery']['enabled']=False
        restore=self.migration.advance(p['receipt_id'],rollback['revision'],p['digest'],'rollback_readback','new-off','admin')
        self.assertEqual('ROLLBACK_RESTORE',restore['state'])
        self.assertEqual({'enabled':True},restore['next_changes'][0]['changes'])
        old['config']['enabled']=True
        pending=self.migration.advance(p['receipt_id'],restore['revision'],p['digest'],'rollback_readback','old-config-only','admin')
        self.assertEqual('ROLLBACK_RESTORE',pending['state'])
        self.inventory['services'].append(old_service);self.inventory['jobs'].append(dict(id='Old_legacy',status='normal'))
        done=self.migration.advance(p['receipt_id'],pending['revision'],p['digest'],'rollback_readback','old-registration','admin')
        self.assertEqual('ROLLED_BACK',done['state']);self.assertIsNone(self.migration.unique_owner(**feature))

    def test_private_snapshot_platform_boundary_and_duplicate_json_rejection(self):
        import os
        store=load('ai').SecretStore(Path(self.tmp.name))
        if os.name=='posix':
            raw=b' {"private":"fiction"}\n';ref=store.put_snapshot(raw)
            self.assertEqual(raw,store.read_snapshot(ref));self.assertEqual(ref,store.put_snapshot(raw))
            file=Path(self.tmp.name)/('migration-'+ref[9:]+'.raw')
            self.assertEqual(0o600,file.stat().st_mode&0o777)
            file.write_bytes(b'changed')
            with self.assertRaises(ValueError):store.read_snapshot(ref)
        else:
            with self.assertRaises(ValueError):store.put_snapshot(b'fiction')
        with self.assertRaises(ValueError):self.migration.preview_import(b'{"ai":{},"ai":{}}','fixture','1',None,'admin')
        with self.assertRaises(ValueError):self.migration.preview_import(b'{"ai":{"timeout":NaN}}','fixture','1',None,'admin')

    def test_current_category_binding_is_revision_enabled_and_type_fenced(self):
        config=self.c.Config(policy={'bindings':{'movie-animation':'动画电影'},'classification_revision':2}).model_dump()
        catalog=dict(revision=2,categories=[dict(id='movie-animation',enabled=True,media_type='电影')])
        self.c.validate_categories(config,catalog)
        for change in (dict(revision=3),dict(categories=[]),
                       dict(categories=[dict(id='movie-animation',enabled=False,media_type='电影')]),
                       dict(categories=[dict(id='movie-animation',enabled=True,media_type='音乐')])):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):self.c.validate_categories(config,dict(catalog,**change))

    def test_policy_migration_exposes_supported_predicates_and_requires_explicit_equivalent(self):
        raw=json.dumps({'policy':[{'id':'user-film','name':'Keep name','include':['2160p'],'exclude':['CAM']},
            {'id':'user-ambiguous','match':['title','description'],'include':['WEB']}]}).encode()
        p=self.migration.preview_import(raw,'policy-fixture','1',None,'admin')
        self.assertEqual({'user-film'},set(p['policy_candidates']))
        self.assertIn('EXPLICIT_EQUIVALENT_REQUIRED',{f['status'] for f in p['fields'] if f['source']=='policy'})
        self.migration.import_page(p['receipt_id'],p['revision'],p['digest'],0,100,'import-policy','admin')
        self.assertEqual({},self.config.view()['config']['policy']['overrides'])
        # Admin explicitly binds the current category and supplies a reviewed AST
        # for the old fallback match semantics; neither is guessed by import.
        overrides=dict(p['policy_candidates'],**{'user-ambiguous':{'any':[{'regex':['title','WEB']},{'regex':['description','WEB']}]}})
        configured=self.apply({'policy':{'bindings':{'movie-animation':'动画电影'},'classification_revision':2,
            'overrides':overrides,'admission':{'all':[{'registered':'user-film'},{'registered':'user-ambiguous'}]}}})
        self.assertFalse(configured['config']['enabled']);self.assertTrue(configured['config']['dry_run'])
        self.assertEqual(overrides,configured['config']['policy']['overrides'])

    def test_narrow_reader_only_gets_two_fixed_paths_and_sanitizes_safety_failure(self):
        import asyncio
        from unittest.mock import patch,AsyncMock
        class Response:
            status=200;headers={}
            def __init__(self):self.sent=False
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):
                if self.sent:return b''
                self.sent=True;return b'{}'
        class Opener:
            def __init__(self):self.requests=[]
            def open(self,request,timeout):self.requests.append((request,timeout));return Response()
        opener=Opener();fetcher=load('discovery').HostRSSFetcher
        with patch.object(fetcher,'_safe',new=AsyncMock()) as safety,patch('urllib.request.build_opener',return_value=opener):
            result=asyncio.run(self.m.read_legacy_source('https://old.invalid','FICT TOKEN','DoubanRankPlusOptimized',[]))
        self.assertEqual([b'{}',b'{}'],result);self.assertEqual(2,len(opener.requests))
        for (request,timeout),route in zip(opener.requests,('migrate-config','migrate-history')):
            self.assertEqual('GET',request.get_method())
            self.assertEqual('https://old.invalid/api/v1/plugin/DoubanRankPlusOptimized/'+route+'?migrate_api_token=FICT+TOKEN',request.full_url)
            self.assertLessEqual(timeout,15)
        with patch.object(fetcher,'_safe',new=AsyncMock(side_effect=RuntimeError('PRIVATE URL TOKEN'))):
            with self.assertRaisesRegex(ValueError,'^LEGACY_READ_FAILED$'):
                asyncio.run(self.m.read_legacy_source('https://old.invalid','FICT TOKEN','DoubanRankPlusOptimized',[]))


class ConfigurationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_ownership import PluginTests
        PluginTests.setUpClass();cls.mod=PluginTests.mod

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.plugin=self.mod.SubscriBetter();self.plugin.data_path=Path(self.tmp.name);self.plugin.init_plugin({})
        self.store=PrivateFixture();self.plugin.secret_store=self.store
        self.plugin.configuration.secrets=self.store;self.plugin.migration.secrets=self.store
        app=FastAPI()
        for route in self.plugin.get_api():
            route=dict(route);route.pop('auth',None)
            app.router.add_api_route(**route)
        self.client=TestClient(app);self.addCleanup(self.client.close);self.headers={'Authorization':'Bearer unit-admin'}

    def test_typed_authenticated_safe_api_and_get_readonly(self):
        self.assertEqual(401,self.client.get('/configuration').status_code)
        before=self.plugin.repository.setting('configuration:SubscriBetter')
        for url in ('/configuration','/migration/owners'):
            self.assertEqual(200,self.client.get(url,headers=self.headers).status_code)
        self.assertEqual(before,self.plugin.repository.setting('configuration:SubscriBetter'))
        state=self.client.get('/configuration',headers=self.headers).json()
        invalid=self.client.post('/credentials',headers=self.headers,json=dict(revision=state['revision'],digest=state['digest'],
            operation_id='k1',kind='key',value='SECRET_SENTINEL',unexpected='SECRET_SENTINEL'))
        self.assertEqual(422,invalid.status_code);self.assertNotIn('SECRET_SENTINEL',invalid.text)
        body=dict(revision=state['revision'],digest=state['digest'],operation_id='k1',kind='key',value='SECRET_SENTINEL')
        response=self.client.post('/credentials',headers=self.headers,json=body)
        self.assertEqual(200,response.status_code);self.assertNotIn('SECRET_SENTINEL',response.text)
        self.assertEqual(response.json(),self.client.post('/credentials',headers=self.headers,json=body).json())
        bypass=dict(state['config'],enabled=True,dry_run=False,raw_key='SECRET_SENTINEL')
        self.plugin.init_plugin(bypass)
        self.assertFalse(self.plugin._ordinary_work_active());self.assertNotIn('SECRET_SENTINEL',json.dumps(self.plugin.saved_config))
        self.assertTrue(all(r.get('response_model') is not dict for r in self.plugin.management.routes()))

    def test_typed_import_receipt_history_and_request_budget(self):
        raw=json.dumps({'ai':{'model':'typed'},'history':[{'title':'Old','tmdbid':'0007','status':'已添加订阅'}]}).encode()
        p=self.client.post('/migration/preview',headers=self.headers,json=dict(source_instance='fixture',source_version='1',
            content_base64=base64.b64encode(raw).decode()))
        self.assertEqual(200,p.status_code,p.text);receipt=p.json()
        body={k:receipt[k] for k in ('receipt_id','revision','digest')}
        body.update(operation_id='typed-import',confirm=True,cursor=0,limit=100)
        response=self.client.post('/migration/import',headers=self.headers,json=body)
        self.assertEqual(200,response.status_code,response.text)
        history=self.client.get('/migration/receipts/'+receipt['receipt_id']+'/history',headers=self.headers)
        self.assertEqual(200,history.status_code,history.text)
        self.assertEqual('LEGACY_UNVERIFIED',history.json()['rows'][0]['state'])
        self.assertEqual({'themoviedb':'0007'},history.json()['rows'][0]['identities'])
        oversized=self.client.post('/migration/preview',headers=self.headers,content=b'X'*3000001)
        self.assertEqual(413,oversized.status_code)


if __name__ == '__main__': unittest.main()
