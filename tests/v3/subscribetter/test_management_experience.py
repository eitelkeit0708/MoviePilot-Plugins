"""Actual SQL paging and draft-policy semantics for the product UI."""
import json
from types import SimpleNamespace as NS
import unittest
import test_management as management
import test_management_display as display
from test_planner import load


class ExperienceTests(unittest.TestCase):
    setUpClass=management.ManagementTests.__dict__['setUpClass']
    setUp=management.ManagementTests.setUp
    populate=display.DisplayTests.populate
    populate_processing=display.DisplayTests.populate_processing

    def test_work_grouping_and_activity_filters_are_applied_before_paging(self):
        self.populate_processing()
        with self.repo.connection(write=True) as db:
            for i in range(6):
                db.execute('INSERT INTO delivery_bundles VALUES(?,?,?,?,?,?,?)',('batch'+str(i),'p6','r','CONFIRMED','2099-01-01',0,'{}'))
            db.execute("UPDATE delivery_bundles SET state='UNKNOWN' WHERE id='b'")
        page=self.views.delivery_works(limit=1,user=None)
        self.assertEqual(1,page.total)
        self.assertEqual((7,6,1),(page.items[0].data['batch_count'],page.items[0].data['confirmed_count'],page.items[0].data['attention_count']))
        self.assertEqual(1,self.views.delivery_works(state='UNKNOWN',user=None).items[0].data['batch_count'])
        self.assertEqual(7,self.views.bundles(task_id=self.task_id,user=None).total)
        self.assertEqual(0,self.views.bundles(task_id=self.task_id+1,user=None).total)
        self.assertEqual([self.task_id],[r.id for r in self.views.tasks(activity='processing',limit=1,user=None).items])
        self.assertEqual(1,self.views.tasks(activity='attention',user=None).total)
        with self.repo.connection(write=True) as db:db.execute("UPDATE delivery_bundles SET state='WAITING' WHERE id='b'")
        self.assertEqual(0,self.views.tasks(activity='attention',user=None).total)
        with self.repo.connection(write=True) as db:db.execute("UPDATE plans SET authorization='SUPERSEDED' WHERE id='p6'")
        self.assertEqual(0,self.views.tasks(activity='processing',user=None).total)
        self.assertEqual(1,self.views.tasks(activity='attention',user=None).total)

    def test_exclusions_are_scoped_before_paging(self):
        key=self.populate_processing()
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO exclusions VALUES(?,?,?,?,?)',('other',json.dumps({'candidate_key':'other','targets':['other-target']}),'reason',None,1))
            db.execute('INSERT INTO exclusions VALUES(?,?,?,?,?)',('mine',json.dumps({'candidate_key':'sample','targets':[key]}),'reason',None,1))
            db.execute('INSERT INTO candidates VALUES(?,?,?,?)',('sample',json.dumps({'title':'Matched resource','private':'not returned'}),'now','now'))
        page=self.views.exclusions(task_id=self.task_id,active=True,limit=1,user=None)
        self.assertEqual((1,'mine'),(page.total,page.items[0].id))
        self.assertEqual('Matched resource',page.items[0].data['candidate_title'])
        self.assertNotIn('private',page.items[0].data)
        self.assertEqual(0,self.views.exclusions(task_id=self.task_id+1,user=None).total)

    def test_discovery_membership_uses_linked_task_state_and_preserves_known_identity(self):
        self.populate();now=self.r.utcnow()
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO discovery_sources VALUES(?,?,?,?,?,?,?,NULL,NULL,?)',('s','v','{}','OK','',0,0,now))
            for i in range(1,11):
                db.execute('INSERT INTO discovery_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,'s',str(i),'r','{}','SUBMITTED','',0,0,'f','{"identity":{"id":"42"}}',1,now,now))
            db.execute("UPDATE tasks SET state='PAUSED' WHERE id=?",(self.task_id,))
            db.execute('INSERT INTO discovery_targets VALUES(?,?,?,?,?,?,?,?,?,?)',(1,self.key,'intent','digest',self.task_id,1,'','SUBMITTED','','receipt'))
            db.execute('INSERT INTO discovery_targets VALUES(?,?,?,?,?,?,?,?,?,?)',(2,self.key,'intent','digest',None,1,'','EXISTING','RECORD_ONLY','receipt'))
            db.execute("UPDATE discovery_records SET data='{}',state='DEFERRED' WHERE id=3")
        views=load('ui').Views(self.plugin)
        self.assertEqual([1],[r.id for r in views.records(view='managed',limit=1,user=None).items])
        self.assertEqual([2],[r.id for r in views.records(view='library',user=None).items])
        self.assertEqual([3],[r.id for r in views.records(view='unrecognized',user=None).items])
        self.assertEqual(7,views.records(view='not_added',limit=1,user=None).total)
        with self.repo.connection(write=True) as db:db.execute("UPDATE tasks SET state='RELEASED_NATIVE',native_id=123 WHERE id=?",(self.task_id,))
        self.assertEqual(0,views.records(view='managed',user=None).total)
        self.assertEqual(1,views.records(view='native',user=None).total)

    def test_draft_policy_trial_does_not_mutate_saved_policy_or_start_work(self):
        ui=load('ui');policy=load('policy');config=self.c.PolicyConfig(bindings={'tv':'欧美剧'})
        self.plugin.runtime=NS(policy=policy.Policy(config.bindings,1),config=NS(policy=config),
            meta=NS(corrector=NS(revision='parse')),public_evidence=lambda value:value)
        views=ui.Views(self.plugin)
        body=dict(category_id='tv',candidate={'title':'Example.S01E01.2160p.WEB-DL-HHWEB 中文字幕'},
                  config_revision=self.config.view()['revision'],runtime_generation=4)
        saved=views.simulate(ui.Simulation(**body),user=None)
        draft=views.simulate(ui.Simulation(**body,draft_policy={**config.model_dump(),'locks':{'resolution':1080}}),user=None)
        self.assertEqual('ALLOW',saved.evidence['evaluation']['status'])
        self.assertEqual('LOCK_MISMATCH:resolution',draft.evidence['evaluation']['reason'])
        self.assertTrue(draft.simulation)
        self.assertEqual({},config.locks)
        self.assertEqual([],draft.evidence['evaluation']['plans'])
        with self.repo.connection() as db:
            for table in ('plans','opportunities','observations','plan_actions'):
                self.assertEqual(0,db.execute('SELECT count(*) FROM '+table).fetchone()[0])
        self.assertEqual({},self.config.view()['config']['policy']['locks'])


if __name__=='__main__':unittest.main()
