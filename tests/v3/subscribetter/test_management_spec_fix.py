"""Review regressions through the shared runtime and mounted management API."""
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_planner import load


class ScopeTests(unittest.TestCase):
    def setUp(self):
        from test_runtime import CommonAdmissionTests
        self.f=CommonAdmissionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;self.target=f.r.Target('电视剧','themoviedb','42',1)
        self.keys=[load('planner').TargetUnit(self.target,e).key for e in (1,2)]
        scope=dict(f.provider.resolve(f.target),target_key=self.target.key,season=1,episodes=[1,2],
            provider_rows=[{'id':1,'episode_number':1},{'id':2,'episode_number':2}],units=self.keys)
        f.provider.resolve=lambda target:copy.deepcopy(scope)
        self.task=f.runtime.submit('tv-review',self.target,{'name':'Fiction'},'admin')
        with f.repo.connection() as db:self.op=dict(db.execute('SELECT * FROM opportunities').fetchone())
        self.request=dict(task_id=self.task['id'],generation=self.task['generation'],opportunity_id=self.op['id'],
            target_keys=self.keys[:1],destination_template='movie-destination',locks={})

    def shrink(self):
        with self.f.repo.connection(write=True) as db:return self.f.runtime.settings(self.request,db=db,actor='admin')

    def test_shrunk_continuous_scope_runs_and_preserves_lifecycle_history(self):
        f=self.f
        for key in self.keys:f.runtime.scheduler.observe(self.op['id'],key,'eligible',[1],eligible=True)
        f.runtime.scheduler.configure_lifecycle(self.task['id'],self.keys,movie_days=30,tv_days=30,anchor='COMPLETE_COLLECTED')
        f.runtime.scheduler.update_completion(self.task['id'],self.keys,scope_closed=True,collected=True)
        life=f.runtime.scheduler.lifecycle(self.task['id'])
        self.assertIsNotNone(life['complete_collected_at']);self.assertIsNotNone(life['expires_at'])
        with f.repo.connection() as db:
            units=[tuple(r) for r in db.execute('SELECT * FROM target_units ORDER BY target_key')]
            observations=[tuple(r) for r in db.execute('SELECT * FROM observations')]
        self.shrink()
        after=f.runtime.scheduler.lifecycle(self.task['id'])
        self.assertEqual(self.keys[:1],json.loads(after.pop('scope')))
        life.pop('scope');self.assertEqual(life,after)
        with f.repo.connection() as db:
            self.assertEqual(units,[tuple(r) for r in db.execute('SELECT * FROM target_units ORDER BY target_key')])
            self.assertEqual(observations,[tuple(r) for r in db.execute('SELECT * FROM observations')])
        op=f.runtime.scheduler.opportunity(self.op['id'])
        for key in ('created_at','config','failures','supersessions'):self.assertEqual(self.op[key],op[key])
        f.runtime.inventory=lambda *a,**kw:dict(state='MISSING')
        f.runtime.candidates.search_errors=[]
        f.runtime.candidates.runtime={}
        f.runtime.delivery.archive.current=lambda keys:{key:dict(state='MISSING') for key in keys}
        with patch.object(f.runtime,'search',return_value=[]) as search,patch.object(f.runtime.candidates,'refresh',create=True,side_effect=ValueError('RESOURCE_UNAVAILABLE')):
            self.assertEqual('WAITING',f.runtime.work(op,deadline=time.monotonic()+5)['state'])
            search.assert_called_once()
        with self.assertRaisesRegex(ValueError,'exact declared scope'):
            f.runtime.scheduler.update_completion(self.task['id'],self.keys,scope_closed=True,collected=True)

    def test_settings_scope_rebind_rolls_back_with_outer_transaction(self):
        f=self.f
        with f.repo.connection() as db:before='\n'.join(db.iterdump())
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with f.repo.connection(write=True) as db:
                f.runtime.settings(self.request,db=db,actor='admin')
                self.assertEqual(self.keys[:1],json.loads(db.execute('SELECT scope FROM task_lifecycle').fetchone()[0]))
                raise RuntimeError('rollback')
        with f.repo.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))

    def test_ingest_counts_current_required_scope_and_generation_only(self):
        f=self.f;now=f.r.utcnow()
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','OK','',0,0,now))
            db.execute('INSERT INTO discovery_records VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',('s','item','r','{}','SUBMITTED','',0,0,'filter','{"identity":{"id":"42"}}',1,now,now))
            db.execute('INSERT INTO discovery_targets VALUES(?,?,?,?,?,?,?,?,?,?)',(1,self.target.key,'intent','digest',self.task['id'],1,'','SUBMITTED','','receipt'))
            generation=db.execute('SELECT generation FROM target_units WHERE target_key=?',(self.keys[0],)).fetchone()[0]
            db.execute('INSERT INTO ingest_receipts VALUES(?,?,?,?,?,?,?)',('i1',None,self.keys[0],generation,'v1','{}',now))
        stats=lambda:load('discovery').DiscoveryService.statistics(SimpleNamespace(repository=f.repo))
        count=lambda:stats()['stages']['ingest']['numerator']
        self.assertEqual(0,count())
        self.shrink();self.assertEqual(1,count())
        before=stats()
        with f.repo.connection(write=True) as db:db.execute('UPDATE discovery_records SET visible=0')
        self.assertEqual(before,stats())
        with f.repo.connection(write=True) as db:db.execute('UPDATE target_units SET generation=generation+1 WHERE target_key=?',(self.keys[0],))
        self.assertEqual(0,count())
        with f.repo.connection(write=True) as db:
            db.execute('INSERT INTO ingest_receipts VALUES(?,?,?,?,?,?,?)',('i2',None,self.keys[0],generation+1,'v2','{}',now))
        self.assertEqual(1,count())
        # A newer ONESHOT runtime scope can coexist with a previous lifecycle row.
        with f.repo.connection(write=True) as db:db.execute('UPDATE task_lifecycle SET scope=?',(json.dumps(self.keys),))
        self.assertEqual(1,count())
        runtime_task=f.repo.setting('runtime-task:'+str(self.task['id']))
        original=copy.deepcopy(runtime_task)
        # An explicit missing required unit must not disappear through an inner join.
        runtime_task['scope']['units'].append('missing-required-unit')
        f.repo.setting('runtime-task:'+str(self.task['id']),runtime_task)
        self.assertEqual(0,count())
        f.repo.setting('runtime-task:'+str(self.task['id']),original)
        self.assertEqual(1,count())
        # Old repository-only admissions still have an explicit opportunity scope.
        with f.repo.connection(write=True) as db:
            db.execute("DELETE FROM settings WHERE key=?",('runtime-task:'+str(self.task['id']),))
            db.execute('UPDATE task_lifecycle SET scope=?',(json.dumps(self.keys[:1]),))
        self.assertEqual(1,count())
        with f.repo.connection(write=True) as db:db.execute('DELETE FROM task_lifecycle')
        self.assertEqual(1,count())


class SafeProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_management import ManagementAPITests
        ManagementAPITests.setUpClass();cls.api_class=ManagementAPITests

    def setUp(self):
        self.api=self.api_class();self.api.setUp();self.addCleanup(self.api.doCleanups)
        self.raw='<b>admin</b> https://example.invalid/?passkey=fiction'

    def get(self,url):
        response=self.api.client.get(url,headers=self.api.headers)
        self.assertEqual(200,response.status_code,response.text)
        self.assertNotIn('<b>',response.text);self.assertNotIn('https://',response.text);self.assertNotIn('passkey=fiction',response.text)
        return response.json()

    def test_task_detail_and_list_share_safe_actor_and_get_has_no_writes(self):
        repo=self.api.plugin.repository;r=load('repository')
        task=repo.submit('unsafe-actor',r.Target('电影','themoviedb','42'),{'name':'Fiction'},self.raw)
        with repo.connection() as db:before='\n'.join(db.iterdump())
        listed=self.get('/tasks')['items'][0]
        self.assertEqual(listed,self.get('/tasks/'+str(task['id']))['task'])
        with repo.connection() as db:self.assertEqual(before,'\n'.join(db.iterdump()))

    def test_source_record_and_bundle_scalar_reasons_are_safe(self):
        repo=self.api.plugin.repository;now=load('repository').utcnow()
        with repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','ERROR',self.raw,0,0,now))
            db.execute('INSERT INTO discovery_records VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',('s','item','r','{}','ERROR',self.raw,0,0,'f','{}',1,now,now))
        self.assertEqual('admin [URL]',self.get('/discovery/sources')['items'][0]['last_reason'])
        self.assertEqual('admin [URL]',self.get('/discovery/records')['items'][0]['reason'])
        from test_delivery import DeliveryTests
        DeliveryTests.setUpClass();f=DeliveryTests();f.setUp();self.addCleanup(f.doCleanups)
        bid=f.prepared();bundle=f.worker.bundle(bid);bundle['reason']=self.raw;f.worker._save(bundle)
        # Keep the mounted API and its repository; import the fictional durable fixture.
        with f.repo.connection() as source,repo.connection() as destination:source.backup(destination)
        self.api.plugin.configuration.initialize({})
        self.assertEqual('admin [URL]',self.get('/delivery/bundles')['items'][0]['reason'])
        self.assertEqual('admin [URL]',self.get('/delivery/bundles/'+bid)['bundle']['reason'])


if __name__=='__main__':unittest.main()
