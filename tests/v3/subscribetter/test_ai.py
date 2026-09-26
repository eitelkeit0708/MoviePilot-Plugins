"""W08 offline checks: real HTTPX serialization, fake transport and private disposable DB."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
import json
import os
import sys
import tempfile
import threading
import time
import unittest

import httpx
from test_planner import load, PLUGIN


class AITests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PLUGIN / 'ai.py').exists(), 'W08 integrated AI not implemented')
        self.m = load('ai')
        self.r = load('repository')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.r.Repository(Path(self.tmp.name) / 'state.db')
        self.requests, self.replies, self.clients = [], [], []
        self.clock = [1000.0]

    def runtime(self, **changes):
        settings = dict(enabled=True, endpoint_ref='secret:'+'a'*32, credential_refs=['secret:'+'b'*32],
                        model='chosen-model')
        settings.update(changes)
        config = self.m.AIConfig.model_validate(settings)
        def factory(**kwargs):
            def handler(request):
                self.requests.append(request)
                reply = self.replies.pop(0) if self.replies else '{"name":"Example","year":""}'
                if callable(reply): reply = reply(request)
                if isinstance(reply, Exception): raise reply
                response=reply if isinstance(reply, httpx.Response) else httpx.Response(200, json={
                    'choices':[{'message':{'content':reply},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':7,'completion_tokens':2}})
                # Real streamed HTTP responses have not been consumed by a JSON
                # fixture constructor; preserve that raw transport boundary here.
                return (httpx.Response(response.status_code,headers=response.headers,stream=httpx.ByteStream(response.content))
                        if response.is_stream_consumed else response)
            client = httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
            self.clients.append(client)
            return client
        secrets = {'secret:'+'a'*32:'https://api.deepseek.com', 'secret:'+'b'*32:'fiction-key',
                   'secret:'+'c'*32:'fiction-key-2'}
        runtime = self.m.AIService(self.repo, config, factory, secrets.__getitem__,
                                   generation=1, current=lambda: True, clock=lambda:self.clock[0])
        self.addCleanup(runtime.close)
        return runtime

    def test_removed_chat_config_has_no_runtime_and_name_transport_still_works(self):
        runtime=self.runtime(chat_enabled=True,chat_routes=[{'ignored':'old'}],chat_history=['old'])
        self.assertFalse(any(k.startswith('chat_') for k in runtime.config.model_dump()))
        for name in ('chat','enqueue_chat','clear_sessions','sessions','chat_queue','session_epochs'):
            self.assertFalse(hasattr(runtime,name),name)
        self.assertEqual('accepted',runtime.extract('Example').reason)
        self.assertTrue(str(self.requests[0].url).endswith('/chat/completions'))
        self.assertEqual('accepted',runtime.extract('Example').reason)
        self.assertEqual(1,len(self.requests))
        self.assertNotIn('chat_sessions',runtime.stats())

    def test_internal_assistance_requires_fresh_owner_before_network_and_cache(self):
        runtime=self.runtime();runtime.assistance_gate=lambda:False
        self.assertEqual('owner_not_unique',runtime.extract('Example').reason)
        self.assertEqual([],self.requests)
        runtime.assistance_gate=lambda:True
        self.assertIsNotNone(runtime.extract('Example').identity)
        self.assertEqual(1,len(self.requests))
        runtime.assistance_gate=lambda:False
        self.assertEqual('owner_not_unique',runtime.extract('Example').reason)
        self.assertEqual(1,len(self.requests))
        def unavailable():raise RuntimeError('public snapshot unavailable')
        runtime.assistance_gate=unavailable
        self.assertEqual('owner_not_unique',runtime.extract('Example').reason)

    def test_explicit_probe_while_disabled_preserves_production_gate_and_shared_budget(self):
        runtime=self.runtime(enabled=False,name_assistance_enabled=False)
        runtime.current=lambda:False
        runtime.probe_current=lambda:True
        snapshot=dict(fingerprint='a'*64,overlaps=[],unclassified=['own_feature_inactive'])
        runtime.owner_snapshot=lambda *args:snapshot
        runtime.assistance_gate=lambda:False
        self.assertIsNone(runtime.extract('Example').identity)
        self.assertEqual([],self.requests)
        self.replies.append('{"name":"The Matrix","year":"1999"}')
        first=runtime.connection_probe()
        self.assertEqual('accepted',first.reason)
        self.assertFalse(runtime.config.enabled)
        self.assertFalse(runtime.live())
        self.assertEqual(1,len(self.requests))
        self.assertEqual('cache',runtime.connection_probe().source)
        snapshot['overlaps']=['other-handler']
        self.assertIsNone(runtime.connection_probe().identity)
        self.assertEqual(1,len(self.requests))
        snapshot['overlaps']=[];snapshot['unclassified']=['own_config_changed']
        self.assertIsNone(runtime.connection_probe().identity)
        self.assertEqual(1,len(self.requests))
        snapshot['unclassified']=[];runtime.probe_current=lambda:False
        self.assertIsNone(runtime.connection_probe().identity)
        self.assertEqual(1,len(self.requests))

    def test_strict_schema_and_grounded_source_boundaries(self):
        invalid = ['[]','null','42','"hello"','',None,'{','{"name":null,"year":null}',
                   '{"name":"Example","year":2024}', '{"name":"Example","year":"","season":0}',
                   '{"name":"Example","name":"Other","year":""}', '```json\n{}\n```']
        for raw in invalid:
            with self.subTest(raw=raw): self.assertIsNone(self.m.inspect_identity(raw,'Example 2024')[0])
        for name,year,title in [('Example','','Example 2 1080p'),('AB','','[A / B]'),
                                ('1917','1917','1917.1080p'),('Example','2024','Example 2024-09-12'),
                                ('1080p','','[1080p][作品甲]'),('命运石之门','','[命运石之门剧场版：负荷领域的既视感]')]:
            with self.subTest(title=title):
                self.assertIsNone(self.m.inspect_identity(json.dumps(dict(name=name,year=year)),title)[0])
        for name,year,title in [('污点','2026','[污点/Buppha the Movie] 2026'),
                               ('1917','2019','1917.2019.1080p'),('Example 2','','Example.2.1080p')]:
            self.assertEqual(dict(name=name,year=year),self.m.inspect_identity(json.dumps(dict(name=name,year=year)),title)[0])
        self.assertIsNone(self.m.inspect_identity('{"name":"HHWEB","year":""}','[HHWEB][作品甲]',team='HHWEB')[0])

    def test_profiles_are_serialized_and_chosen_model_is_not_replaced(self):
        for profile in ('auto','deepseek','generic'):
            c=self.runtime(profile=profile)
            result=c.extract('Example')
            self.assertEqual('accepted',result.reason)
            request=self.requests[-1]; body=json.loads(request.content)
            self.assertEqual('https://api.deepseek.com/v1/chat/completions',str(request.url))
            self.assertEqual('chosen-model',body['model']); self.assertNotIn('tools',body)
            self.assertEqual(profile!='generic','thinking' in body)
            self.assertEqual(profile!='generic','response_format' in body)
            self.assertEqual(512,body['max_tokens'])
            self.assertEqual({'input_title':'Example','input_subtitle':'','context':{}},json.loads(body['messages'][1]['content']))
            c.close()
            self.clock[0]+=4000

    def test_cache_exact_context_negative_copy_and_usage(self):
        c=self.runtime()
        first=c.extract('Example',subtitle='2024')
        first.identity['name']='changed'
        again=c.extract('Example',subtitle='2024')
        self.assertEqual('Example',again.identity['name']); self.assertEqual('cache',again.source)
        self.assertEqual(0,again.attempts)
        c.extract('Example',subtitle='2025')
        c.extract('Example',subtitle='2025',context={'locks':['year']})
        self.assertEqual(3,len(self.requests)); self.assertEqual(21,c.stats()['usage']['prompt_tokens'])
        self.replies.append('{"name":"","year":""}')
        self.assertEqual('no_name',c.extract('Unknown work').reason)
        self.assertEqual('cache',c.extract('Unknown work').source)
        self.assertEqual(4,len(self.requests))

    def test_auth_rotation_rate_limit_and_restart_deadline(self):
        c=self.runtime(credential_refs=['secret:'+'b'*32,'secret:'+'c'*32])
        self.replies.extend([httpx.Response(401),'{"name":"Example","year":""}'])
        self.assertEqual('accepted',c.extract('Example').reason)
        self.assertEqual(['Bearer fiction-key','Bearer fiction-key-2'],[r.headers['authorization'] for r in self.requests])
        self.replies.append(httpx.Response(429,headers={'Retry-After':'600'}))
        with self.assertLogs(self.m.__name__,level='WARNING') as logs:
            self.assertEqual('rate_limit',c.extract('Example Two').reason)
        self.assertEqual(['subscriBetter AI unavailable: rate_limit'],[record.getMessage() for record in logs.records])
        c.close()
        d=self.runtime(credential_refs=['secret:'+'b'*32,'secret:'+'c'*32])
        with self.assertLogs(self.m.__name__,level='WARNING') as logs:
            self.assertEqual('rate_limit',d.extract('Example Three').reason)
        self.assertEqual(['subscriBetter AI unavailable: rate_limit'],[record.getMessage() for record in logs.records])
        self.assertEqual(3,len(self.requests)); self.assertGreaterEqual(d.stats()['cooldown_remaining'],600)

    def test_coalesced_calls_and_clear_fence_keep_real_resources(self):
        c=self.runtime(); entered=threading.Event(); release=threading.Event()
        def reply(request):
            entered.set(); self.assertTrue(release.wait(3)); return '{"name":"Example","year":""}'
        self.replies.append(reply)
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader=pool.submit(c.extract,'Example'); self.assertTrue(entered.wait(2))
            follower=pool.submit(c.extract,'Example')
            c.clear_cache('tester'); release.set()
            self.assertIsNone(leader.result().identity)
            follower.result()
        self.assertEqual(1,len(self.requests)); self.assertEqual(0,c.stats()['cache_size'])

    def test_unknown_dispatched_budget_survives_new_runtime(self):
        c=self.runtime(); key=c.request_digest('Example')
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO ai_requests VALUES(?,?,?,?,?,?,?,?)',
                       (c.scope,key,'INFLIGHT',1,0,'uncertain','old',self.clock[0]))
        self.assertEqual('outcome_unknown',c.extract('Example').reason)
        self.assertEqual([],self.requests)

    def test_config_import_and_private_store(self):
        store=self.m.SecretStore(Path(self.tmp.name))
        values={}
        def put(value):
            ref='secret:'+format(len(values)+1,'032x');values[ref]=value;return ref
        mapped=self.m.migrate_legacy(dict(enabled=True,recognize=True,openai_url='https://gateway.invalid/base',
            openai_key='key-a,key-b',model='user-model',customize_prompt='  custom\n text  ',
            previous_customize_prompt='previous',restore_prompt=True,clear_cache=True),put)
        self.assertEqual('user-model',mapped.model)
        self.assertEqual('  custom\n text  ',mapped.prompt)
        self.assertEqual('previous',mapped.prompt_backup)
        self.assertFalse(mapped.enabled)
        self.assertEqual(['key-a','key-b'],[values[r] for r in mapped.credential_refs])
        self.assertNotIn('key-a',mapped.model_dump_json())
        if os.name=='posix':
            ref=store.put('fictional');self.assertEqual('fictional',store.resolve(ref))
            self.assertEqual(0o600,store.path.stat().st_mode & 0o777)
        else:
            with self.assertRaisesRegex(ValueError,'POSIX'):store.put('fictional')
        for ref in ('../secret','secret:/x',''):
            with self.assertRaises(ValueError): store.resolve(ref)

    def test_name_assistance_preserves_scope_and_rejects_hard_conflict(self):
        from test_meta import native
        meta=load('meta');corrector=meta.MetaCorrector()
        c=self.runtime()
        title='[片名乙] Wrong.S00E02-E04.2024.1080p'
        correction=corrector.correct(native('Wrong',begin_episode=24),title)
        self.assertEqual('DEFER',correction.status)
        self.replies.append('{"name":"片名乙","year":"2024"}')
        merged,ai=c.assist(title,'',correction,corrector=corrector)
        self.assertEqual('OK',merged.status);self.assertEqual('片名乙',merged.meta.cn_name)
        self.assertEqual((0,2,4),(merged.meta.begin_season,merged.meta.begin_episode,merged.meta.end_episode))
        self.assertEqual('2160p',merged.meta.resource_pix)
        conflict=corrector.correct(native('Wrong'),title,'S03E08')
        unchanged,result=c.assist(title,'S03E08',conflict,corrector=corrector)
        self.assertEqual('DEFER',unchanged.status);self.assertEqual(1,len(self.requests))
        clear=corrector.correct(native('Example',begin_episode=None),'Example 2024')
        c.assist('Example 2024','',clear,corrector=corrector)
        self.assertEqual(1,len(self.requests))

    def test_bridge_is_cache_only_bounded_and_owner_checked(self):
        from test_meta import native
        meta=load('meta');c=self.runtime(name_recognize_bridge=True,queue_size=1)
        service=meta.MetaService(self.repo,meta.MetaCorrector(),parser=lambda *a,**kw:native('Wrong',begin_episode=None))
        c.owner_check=lambda *args:self.m.owner_receipt('receipt',*args,fingerprint='a'*64)
        c.owner_snapshot=lambda *args:dict(fingerprint='a'*64,overlaps=[],unclassified=[])
        event=NS(event_data={'title':'[片名乙] 2024'})
        c.name_event(event,service)
        c.name_event(NS(event_data={'title':'[另一片名] 2024'}),service)
        self.assertEqual(1,c.stats()['queued']);self.assertEqual([],self.requests)
        self.replies.append('{"name":"片名乙","year":"2024"}')
        c.drain(service)
        c.name_event(event,service)
        self.assertEqual('片名乙',event.event_data['name'])
        c.owner_snapshot=lambda *args:dict(fingerprint='a'*64,overlaps=['other'],unclassified=[])
        blocked=NS(event_data={'title':'[片名乙] 2024'})
        c.name_event(blocked,service);self.assertNotIn('name',blocked.event_data)

    def test_candidate_pipeline_uses_ai_then_independent_provider_identity(self):
        from test_meta import native
        meta=load('meta');candidates=load('candidates');c=self.runtime()
        calls=[]
        class Adapter:
            def recognize(self,parsed,declared):calls.append((parsed,declared));return NS()
            def identity(self,media):return ('tmdb','different')
        service=candidates.CandidateService(self.repo,Adapter(),ai=c)
        parse=meta.MetaService(self.repo,meta.MetaCorrector(),parser=lambda *a,**kw:native('Wrong',begin_episode=None))
        record=service.observe(dict(site=1,torrent_id='x',title='[片名乙] 2024',description='',labels=[]))
        self.replies.append('{"name":"片名乙","year":"2024"}')
        result=service.recognize(record['candidate_key'],self.r.Target('电影','tmdb','target'),parse)
        self.assertEqual('REJECT',result['status']);self.assertEqual('片名乙',calls[0][0].cn_name)
        self.assertEqual(('tmdb',None),calls[0][1]);self.assertEqual(1,c.stats()['counts']['candidate_submitted'])
        self.assertNotIn('identity_matched',c.stats()['counts'])
        other=service.observe(dict(site=1,torrent_id='y',title='[另一个片名] 2024',description='',labels=[]))
        self.replies.append('{"name":"","year":""}')
        failed=service.recognize(other['candidate_key'],self.r.Target('电影','tmdb','target'),parse)
        self.assertEqual('DEFER',failed['status']);self.assertEqual('no_name',failed['ai']['reason'])
        with self.repo.connection() as db:
            stored=json.loads(db.execute('SELECT data FROM candidates WHERE candidate_key=?',(other['candidate_key'],)).fetchone()[0])
        self.assertEqual('no_name',stored['recognition']['ai']['reason'])

    def test_clear_title_skips_model_and_nonempty_native_conflict_uses_bounded_assistance(self):
        from test_meta import native
        meta=load('meta');candidates=load('candidates');ai=self.runtime()
        class Adapter:
            def recognize(self,parsed,declared):return NS(type='电影')
            def identity(self,media):return ('tmdb','target')
        service=candidates.CandidateService(self.repo,Adapter(),ai=ai)
        parser=lambda title,*args,**kwargs:native('Example' if title.startswith('Example') else 'Wrong',begin_episode=None)
        parse=meta.MetaService(self.repo,meta.MetaCorrector(),parser=parser)
        target=self.r.Target('电影','tmdb','target')
        clear=service.observe(dict(site=1,torrent_id='clear',title='Example 2024',description='',labels=[]))
        result=service.recognize(clear['candidate_key'],target,parse)
        self.assertEqual('OK',result['status']);self.assertEqual([],self.requests)
        self.assertEqual('deterministic',result['ai']['reason'])
        conflict=service.observe(dict(site=1,torrent_id='conflict',title='[片名乙] 2024',description='',labels=[]))
        self.replies.append('{"name":"片名乙","year":"2024"}')
        result=service.recognize(conflict['candidate_key'],target,parse)
        self.assertEqual('OK',result['status']);self.assertEqual('片名乙',result['meta'].cn_name)
        self.assertEqual('accepted',result['ai']['reason']);self.assertEqual(1,len(self.requests))
        hard=service.observe(dict(site=1,torrent_id='hard',title='[片名乙] S03E08 2024',description='S02E07',labels=[]))
        result=service.recognize(hard['candidate_key'],target,parse)
        self.assertEqual('DEFER',result['status']);self.assertEqual(1,len(self.requests))

    def test_theatrical_s00_numeric_title_and_explicit_id_conflict_share_recognition_gate(self):
        from test_meta import native
        meta=load('meta');candidates=load('candidates');ai=self.runtime()
        original=[None];parsed=[];recognized=[]
        def parser(*args,**kwargs):parsed.append(args);return original[0]
        class Adapter:
            def recognize(self,corrected,declared):
                recognized.append((corrected,declared));return NS(type=corrected.type)
            def identity(self,media):return ('tmdb','42')
        parse=meta.MetaService(self.repo,meta.MetaCorrector(),parser=parser)
        service=candidates.CandidateService(self.repo,Adapter(),ai=ai)
        cases=[
            ('theatrical','Steins;Gate The Movie.2024.1080p',native('Steins;Gate The Movie',begin_season=1,end_season=2,total_season=2,begin_episode=4,end_episode=8,total_episode=5),
             self.r.Target('电影','tmdb','42'),dict(type='电影',en_name='Steins;Gate The Movie',begin_season=None,begin_episode=None)),
            ('s00','Fictional.S00E02-E04.1080p',native('Fictional',begin_season=1,begin_episode=2),
             self.r.Target('电视剧','tmdb','42',0),dict(type='电视剧',begin_season=0,begin_episode=2,end_episode=4)),
            ('numeric','1917.2019.1080p',native('',year='1917',type='电影',begin_episode=None),
             self.r.Target('电影','tmdb','42'),dict(type='电影',en_name='1917',year='2019',begin_episode=None)),
        ]
        for tid,title,original_meta,target,expected in cases:
            original[0]=original_meta
            row=service.observe(dict(site=1,torrent_id=tid,title=title,description='',labels=[]))
            result=service.recognize(row['candidate_key'],target,parse)
            self.assertEqual('OK',result['status'],tid)
            for field,value in expected.items():self.assertEqual(value,getattr(result['meta'],field),tid+':'+field)
        self.assertEqual(3,len(parsed));self.assertEqual(3,len(recognized));self.assertEqual([],self.requests)
        original[0]=native('',year='1917',type='电视剧',begin_episode=None)
        wrong_type=service.observe(dict(site=1,torrent_id='numeric-wrong-type',title=cases[2][1],description='',labels=[]))
        self.assertEqual('REJECT',service.recognize(wrong_type['candidate_key'],cases[2][3],parse)['status'])
        self.assertEqual('电视剧',recognized[-1][0].type);self.assertEqual([],self.requests)
        conflict=service.observe(dict(site=1,torrent_id='conflicting-id',title=cases[0][1],
                                      description='',labels=[],media_source='tmdb',media_id='43'))
        self.assertEqual('REJECT',service.recognize(conflict['candidate_key'],cases[0][3],parse)['status'])
        self.assertEqual(4,len(parsed));self.assertEqual(4,len(recognized));self.assertEqual([],self.requests)

    def test_plugin_ai_lifecycle_errors_do_not_remove_ownership_safety(self):
        from test_ownership import PluginTests
        PluginTests.setUpClass()
        plugin=PluginTests.mod.SubscriBetter();plugin.data_path=Path(self.tmp.name)/'plugin';plugin.data_path.mkdir()
        plugin.init_plugin({'enabled':True,'dry_run':False,'ai_assist':{'timeout':0}})
        self.assertFalse(plugin._ordinary_work_active())
        self.assertIn('INVALID_OR_STALE_CONFIG',plugin.errors)
        self.assertIsNotNone(plugin.ownership);self.assertIsNotNone(plugin.guard)
        self.assertIsNone(plugin.ai)
        plugin.init_plugin({'enabled':True,'dry_run':False})
        self.assertEqual([],plugin.ai_errors)
        plugin.stop_service()
        self.assertFalse(plugin.get_state())
        from ai_host_contract import MessageType
        from unittest.mock import patch
        sent=[];plugin.post_message=lambda **kw:sent.append(kw)
        with patch.object(sys.modules['app.schemas.types'],'MessageType',MessageType,create=True):
            plugin._ai_notify('AI service: authentication')
        self.assertEqual(MessageType.Plugin,sent[0]['mtype'])

    def test_host_projection_unknown_responder_and_inactive_legacy_feature(self):
        rows=[dict(event_type='name',handler_identifier='own.name',status='enabled'),
              dict(event_type='name',handler_identifier='old.recognize',status='enabled')]
        plugins=[dict(id='old',source='ChatGPTPlusUltra',prefix='old',active=True,config={'enabled':True,'recognize':False})]
        fresh=self.m.owner_projection(rows,plugins,'name_bridge','name','own.name',{'generation':1})
        self.assertEqual([],fresh['overlaps']);self.assertEqual([],fresh['unclassified'])
        plugins[0]['config']['recognize']=True
        self.assertEqual(['old.recognize'],self.m.owner_projection(rows,plugins,'name_bridge','name','own.name',{})['overlaps'])
        rows.append(dict(event_type='name',handler_identifier='unknown.call',status='enabled'))
        self.assertIn('unknown.call',self.m.owner_projection(rows,plugins,'name_bridge','name','own.name',{})['unclassified'])

    def test_http_errors_distinct_and_non_auth_never_rotate(self):
        for status,code in [(400,'configuration'),(403,'permission'),(408,'timeout'),(500,'service')]:
            with self.subTest(status=status):
                c=self.runtime(model='test-'+str(status),credential_refs=['secret:'+'b'*32,'secret:'+'c'*32])
                self.replies.append(httpx.Response(status));before=len(self.requests)
                with self.assertLogs(self.m.__name__,level='WARNING') as logs:
                    self.assertEqual(code,c.extract('Example').reason)
                self.assertEqual(['subscriBetter AI unavailable: '+code],[record.getMessage() for record in logs.records])
                self.assertEqual(before+1,len(self.requests))
        self.assertEqual(600,self.m.AIService.retry_after('600',1000))
        self.assertEqual(600,self.m.AIService.retry_after('Thu, 01 Jan 1970 00:26:40 GMT',1000))

    def test_reloaded_runtime_cannot_publish_old_request(self):
        c=self.runtime();entered=threading.Event();release=threading.Event()
        def reply(_):entered.set();self.assertTrue(release.wait(3));return '{"name":"Example","year":""}'
        self.replies.append(reply)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(c.extract,'Example');self.assertTrue(entered.wait(2))
            replacement=self.runtime();release.set()
            self.assertIsNone(pending.result().identity)
        self.assertEqual(0,replacement.stats()['cache_size'])

    def test_inflight_http_cache_clear_reload_and_disable_are_fenced_and_closed(self):
        for action in ("clear", "reload", "disable"):
            with self.subTest(action=action):
                c = self.runtime(model="t180-" + action)
                entered, release = threading.Event(), threading.Event()
                notices = []
                c.notify = notices.append
                def reply(_):
                    entered.set()
                    self.assertTrue(release.wait(5))
                    return '{"name":"Example","year":""}'
                self.replies.append(reply)
                before = len(self.requests)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(c.extract, "Example")
                    self.assertTrue(entered.wait(2))
                    client = self.clients[-1]
                    if action == "clear":
                        c.clear_cache("tester")
                    elif action == "reload":
                        c.close()
                        replacement = self.runtime(model="t180-replacement")
                    else:
                        c.config.enabled = False
                        c.close()
                    release.set()
                    result = pending.result()
                self.assertIsNone(result.identity)
                self.assertEqual("stale_runtime", result.reason)
                self.assertEqual(0, c.stats()["cache_size"])
                self.assertEqual(1, c.stats()["counts"]["api_calls"])
                self.assertEqual(1, c.stats()["counts"]["api_responses"])
                self.assertEqual(1, c.stats()["counts"]["usage_responses"])
                self.assertEqual(7, c.stats()["usage"]["prompt_tokens"])
                self.assertNotIn("name_accepted", c.stats()["counts"])
                self.assertFalse(any(key.startswith("validation:") for key in c.stats()["counts"]))
                self.assertEqual(before + 1, len(self.requests))
                self.assertEqual([], notices)
                if action == "reload":
                    self.assertEqual(0, replacement.stats()["cache_size"])
                    self.assertNotIn("api_calls", replacement.stats()["counts"])
                c.close()
                self.assertTrue(client.is_closed)
                self.assertEqual({}, c.clients)

    def test_negative_ttl_and_unknown_usage_survive_restart_without_new_charge(self):
        c=self.runtime();self.replies.append(httpx.Response(200,json={
            'choices':[{'message':{'content':'{"name":"","year":""}'}}],
            'usage':{'prompt_tokens':True,'completion_tokens':-1,'prompt_cache_hit_tokens':'private'}}))
        self.assertEqual('no_name',c.extract('Example').reason)
        c.close();self.clock[0]+=40
        replacement=self.runtime()
        self.assertEqual('no_name',replacement.extract('Example').reason)
        self.assertEqual(1,len(self.requests));self.assertEqual({},replacement.stats()['usage'])

    def test_new_runtime_cannot_exceed_old_inflight_instance_bound(self):
        c=self.runtime(max_concurrency=1);entered=threading.Event();release=threading.Event()
        def reply(_):entered.set();self.assertTrue(release.wait(3));return '{"name":"Example","year":""}'
        self.replies.append(reply)
        with ThreadPoolExecutor(max_workers=1) as pool:
            old=pool.submit(c.extract,'Example');self.assertTrue(entered.wait(2));c.close()
            replacement=self.runtime(max_concurrency=1,model='changed-model')
            self.assertEqual('previous_runtime_draining',replacement.extract('Example Two').reason)
            self.assertEqual(1,len(self.requests));release.set();old.result()

    def test_known_prompt_migration_retains_previous_backup(self):
        values={}
        def put(value):ref='secret:'+format(len(values)+1,'032x');values[ref]=value;return ref
        data=dict(enabled=True,recognize=True,chat_enabled=True,customize_prompt=self.m.LEGACY_EXTRACTION_PROMPT,
                  previous_customize_prompt='older user backup',openai_url='https://api.deepseek.com',openai_key='fiction')
        mapped=self.m.migrate_legacy(data,put)
        self.assertEqual(self.m.DEFAULT_PROMPT,mapped.prompt)
        self.assertEqual(data['customize_prompt'],mapped.prompt_backup)
        self.assertEqual('older user backup',mapped.prompt_previous_backup)
        preview=self.m.legacy_preview(data)
        self.assertTrue(preview['requested_enabled'])
        self.assertFalse(mapped.enabled)

    def test_profiles_endpoint_and_serialized_proxy_options(self):
        self.assertEqual('https://gateway.invalid/api',self.m.normalize_endpoint('https://gateway.invalid/api/',True))
        for endpoint in ('https://x/u?token=private','https://user:pass@x','https://x/v1/chat/completions'):
            with self.assertRaises(ValueError):self.m.normalize_endpoint(endpoint)
        c=self.runtime();c.resolve=lambda ref:'https://api.deepseek.com.attacker.invalid/base' if ref==c.config.endpoint_ref else 'fiction'
        self.assertEqual('accepted',c.extract('Example').reason)
        self.assertNotIn('thinking',json.loads(self.requests[-1].content))
        d=self.runtime(profile='deepseek',compatible=True,model='forwarded')
        d.resolve=c.resolve
        d.extract('Example')
        self.assertEqual('https://api.deepseek.com.attacker.invalid/base/chat/completions',str(self.requests[-1].url))
        self.assertEqual({'type':'disabled'},json.loads(self.requests[-1].content)['thinking'])

    def test_notification_failures_and_secrets_do_not_change_response_state(self):
        c=self.runtime(notifications=True);notices=[]
        def notify(message):notices.append(message);raise RuntimeError('fiction-secret')
        c.notify=notify;self.replies.append(httpx.Response(401,text='fiction-secret'))
        with self.assertLogs(self.m.__name__,level='WARNING') as logs:
            self.assertEqual('authentication',c.extract('Example').reason)
            c.extract('Example Two')
        self.assertEqual(['subscriBetter AI unavailable: authentication'],[record.getMessage() for record in logs.records])
        self.assertEqual(['AI service: authentication'],notices)
        text=self.m.safe_text('https://x.invalid/private/path?passkey=secret Authorization: Bearer hidden\n'+chr(27)+' sk-abc123456',('hidden',))
        self.assertNotIn('private/path',text);self.assertNotIn('hidden',text);self.assertNotIn('abc123456',text)

    def test_internal_pipeline_ignores_unused_auxiliary_listener(self):
        c=self.runtime();c.owner_check=lambda *args:None
        c.owner_snapshot=lambda *args:dict(fingerprint='a'*64,overlaps=['unrelated'],unclassified=['other'])
        self.assertEqual('accepted',c.extract('Example').reason)

    def test_legacy_more_than_attempt_limit_keys_remain_ordered(self):
        values={}
        def put(value):ref='secret:'+format(len(values)+1,'032x');values[ref]=value;return ref
        mapped=self.m.migrate_legacy({'openai_key':','.join('fiction-'+str(i) for i in range(8))},put)
        self.assertEqual(['fiction-'+str(i) for i in range(8)],[values[r] for r in mapped.credential_refs])
        self.assertEqual(2,mapped.max_attempts)

    def test_source_title_and_subtitle_never_join_name_evidence(self):
        self.assertIsNone(self.m.inspect_identity('{"name":"ExampleShow","year":""}','Example','Show')[0])
        self.assertIsNone(self.m.inspect_identity('{"name":"Example 2024","year":""}','Example','2024')[0])
        self.assertEqual({'name':'作品甲','year':'2024'},self.m.inspect_identity('{"name":"作品甲","year":"2024"}',
                         'unknown release','[作品甲] 2024')[0])
        self.assertIsNone(self.m.inspect_identity('{"name":"Example","year":"2024"}','Example 2024','2025')[0])
        self.assertIsNone(self.m.inspect_identity('{"name":"Example","year":"2024"}','Example 1976 2024 Remastered')[0])
        self.assertEqual({'name':'Example','year':'1976'},self.m.inspect_identity('{"name":"Example","year":"1976"}',
                         'Example 1976 2024 Remastered')[0])

    def test_coalesced_followers_use_bounded_admission(self):
        c=self.runtime(max_concurrency=1,queue_size=1,timeout=1)
        entered=threading.Event();release=threading.Event()
        def reply(_):entered.set();release.wait(3);return '{"name":"Example","year":""}'
        self.replies.append(reply)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(c.extract,'Example');self.assertTrue(entered.wait(2))
            second=pool.submit(c.extract,'Other')
            try:
                deadline=time.monotonic()+1
                while c.stats()['inflight']<2 and time.monotonic()<deadline:threading.Event().wait(.001)
                self.assertEqual(2,c.stats()['inflight'])
                start=time.monotonic();self.assertEqual('busy',c.extract('Example').reason)
                self.assertLess(time.monotonic()-start,.3)
            finally:release.set();first.result();second.result()

    def test_http_response_envelope_is_bounded_before_json_decode(self):
        class Content(httpx.SyncByteStream):
            consumed=0
            def __iter__(self):
                for _ in range(100):
                    self.consumed+=1
                    yield b' '*8192
        content=Content();c=self.runtime();self.replies.append(httpx.Response(200,stream=content))
        self.assertEqual('invalid_response',c.extract('Example').reason)
        self.assertLess(content.consumed,100)

    def test_review_R1_compressed_response_rejected_before_read_or_decode(self):
        import gzip
        compressed=gzip.compress(b' '*(1024*1024))
        self.assertLess(len(compressed),2048)
        class Content(httpx.SyncByteStream):
            consumed=0
            def __iter__(self):
                self.consumed+=1
                yield compressed
        content=Content();c=self.runtime()
        self.replies.append(httpx.Response(200,headers={'Content-Encoding':'gzip'},stream=content))
        self.assertEqual('invalid_response',c.extract('Example').reason)
        self.assertEqual(0,content.consumed,'reject encoding before HTTPX can receive/decompress the gzip block')
        self.assertEqual('identity',self.requests[-1].headers['accept-encoding'])
        self.assertEqual({},c.stats()['usage'])

    def test_review_R2_roman_sequel_case_never_changes_identity_guard(self):
        for numeral in ('II','ii'):
            with self.subTest(numeral=numeral):
                title=f'Rocky.{numeral}.1979.1080p'
                self.assertEqual((None,'sequel_or_subtitle_lost'),
                    self.m.inspect_identity('{"name":"Rocky","year":"1979"}',title))
                retained={'name':'Rocky '+numeral,'year':'1979'}
                self.assertEqual(retained,self.m.inspect_identity(json.dumps(retained),title)[0])

    def test_dribbling_response_checks_elapsed_deadline_per_received_chunk(self):
        from unittest.mock import patch
        elapsed=[0.0]
        class Content(httpx.SyncByteStream):
            consumed=0
            def __iter__(self):
                for _ in range(100):
                    self.consumed+=1;elapsed[0]+=2
                    yield b' '
        content=Content();c=self.runtime(timeout=1);self.replies.append(httpx.Response(200,stream=content))
        with patch.object(self.m.time,'monotonic',lambda:elapsed[0]), self.assertLogs(self.m.__name__,level='WARNING') as logs:
            self.assertEqual('timeout',c.extract('Example').reason)
        self.assertEqual(['subscriBetter AI unavailable: timeout'],[record.getMessage() for record in logs.records])
        self.assertEqual(1,content.consumed)

    @unittest.skipUnless(os.name=='posix','POSIX credential filesystem enforcement requires Linux')
    def test_private_store_mode_symlinks_and_concurrent_atomic_updates(self):
        root=Path(self.tmp.name);store=self.m.SecretStore(root)
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs=list(pool.map(lambda i:self.m.SecretStore(root).put('fiction-'+str(i)),range(12)))
        self.assertEqual(0o600,store.path.stat().st_mode&0o777)
        self.assertEqual(['fiction-'+str(i) for i in range(12)],[store.resolve(ref) for ref in refs])
        self.assertEqual([],list(root.glob('*.tmp')))
        store.path.chmod(0o644)
        with self.assertRaises(ValueError):store.resolve(refs[0])
        store.path.chmod(0o600)
        link=root/'link';link.symlink_to(root,target_is_directory=True)
        with self.assertRaises(OSError):self.m.SecretStore(link).put('never-written')


class LegacyProtocolTests(unittest.TestCase):
    """Fresh V3 runs of fixed ChatGPTPlusUltra 1.4.2 source-boundary fixtures.

    Adapted from eitelkeit0708 test_refinements.py/test_plugin.py, GPL-3.0.
    V2 event mutation, private client patches and manifest assertions are excluded.
    """
    @classmethod
    def setUpClass(cls):cls.ai=load('ai')


_ALIASES=[
    ('The Stain Directors Cut 2026 [污点 / บุปผาราตรี / To Be Named Movie / Lady of the Night / Buppha the Movie / The Stain]','污点'),
    ('[污点/Buppha the Movie/The Stain] 2026','污点'),
    ('【污点／Buppha the Movie／The Stain】 2026','污点'),
    ('Buppha the Movie 2026 [污点]','污点'),
    ('[The Stain / Buppha the Movie] 2026','The Stain'),
    ('The.Stain.2026 [Buppha the Movie]','The Stain'),
    ('[作品甲 / 作品乙剧场版] 2026','作品甲'),
    ('[作品甲] [作品乙电影版] 2026','作品甲'),
    ('[作品甲【1080p】 / 作品乙剧场版] 2026','作品甲'),
]
_MOVIE_LOSS=[
    ('[命运石之门剧场版：负荷领域的既视感] 2013','命运石之门'),
    ('[劇場版 命運石之門：負荷領域的既視感] 2013','命運石之門'),
    ('Steins;Gate.the.Movie.2013','Steins Gate'),
    ('Example 2013 [Example The Movie]','Example'),
    ('[Example The Movie / Example] 2013','Example'),
    ('[剧场版][命运石之门] 2013','命运石之门'),
    ('[命运石之门]【剧场版】 2013','命运石之门'),
    ('【電影版】【作品甲】 2013','作品甲'),
    ('Example [The Movie] 2013','Example'),
    ('[The Movie] Example 2013','Example'),
]
def _legacy_case(title,name,accepted):
    def check(self):
        identity,reason=self.ai.inspect_identity(json.dumps({'name':name,'year':'2026' if accepted else ''}),title)
        self.assertEqual({'name':name,'year':'2026'} if accepted else None,identity,reason)
    return check

for _kind,_cases,_accept in [('alias',_ALIASES,True),('movie_marker',_MOVIE_LOSS,False)]:
    for _index,(_title,_name) in enumerate(_cases):
        setattr(LegacyProtocolTests,f'test_legacy_{_kind}_{_index:02d}',_legacy_case(_title,_name,_accept))


if __name__=='__main__': unittest.main()
