"""Full work-loop recovery; fictional adapters and disposable SQLite only."""
import copy
import time
import unittest
from unittest.mock import patch
from test_planner import load
import test_runtime as fixtures
from test_runtime_fix4 import partial_fixture


class ColdWorkTests(unittest.TestCase):
    def test_owned_download_work_refreshes_before_sample_warm_and_restarted(self):
        for restart in (False,True):
            with self.subTest(restart=restart):
                f=fixtures.ColdExecutionTests('test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers')
                f.setUp();self.addCleanup(f.doCleanups)
                advance=f.m.Runtime.advance;seen=[]
                def entered(r,plan,saved,**kwargs):
                    result=advance(r,plan,saved,**kwargs)
                    if result.get('state')!='DOWNLOADING' or seen:return result
                    seen.append(True)
                    if restart:
                        cm=load('candidates');f.m.CandidatePipeline=cm.CandidatePipeline
                        f.plugin.candidates=cm.CandidateService(f.repo,r.candidates.adapter)
                        r=f.m.Runtime(f.plugin,provider=f.provider,clients=r.clients)
                        self.assertEqual({},r.pipeline.rounds);self.assertEqual({},r.candidates.runtime)
                    else:r.pipeline.rounds[plan['snapshot']['candidate_key']]['acquired']=time.monotonic()-301
                    r.inventory=lambda *a,**kw:dict(state='MISSING')
                    before=copy.deepcopy(r.authority.plan(plan['id'])['snapshot']);events=[]
                    refresh=r.candidates.refresh;executor=type(r.pipeline.executor());sample=executor.sample
                    def refreshed(key,budget):events.append(('refresh',key));return refresh(key,budget)
                    def sampled(ex,*a,**kw):events.append(('sample',a[0]));return sample(ex,*a,**kw)
                    opportunity=r.scheduler.opportunity(plan['opportunity_id'])
                    with r.repository.connection() as db:
                        actions=[tuple(x) for x in db.execute('SELECT * FROM plan_actions ORDER BY id')]
                    with patch.object(r.candidates,'refresh',refreshed),patch.object(executor,'sample',sampled),patch.object(f.m.Runtime,'advance',advance):
                        try:worked=r.work(opportunity,deadline=time.monotonic()+10)
                        except ValueError as error:worked=dict(state='DEFER',reason=str(error))
                    self.assertEqual('DOWNLOADING',worked['state'],worked)
                    self.assertEqual(('refresh',plan['snapshot']['candidate_key']),events[0])
                    self.assertEqual(1,sum(event[0]=='refresh' for event in events))
                    self.assertTrue(any(event[0]=='sample' for event in events))
                    self.assertEqual(before,r.authority.plan(plan['id'])['snapshot'])
                    self.assertEqual(saved,r.repository.setting('runtime-input:'+opportunity['id']))
                    with r.repository.connection() as db:
                        self.assertEqual(actions,[tuple(x) for x in db.execute('SELECT * FROM plan_actions ORDER BY id')])
                    r.pipeline.rounds.clear();events.clear()
                    with patch.object(r.candidates,'refresh',refreshed),patch.object(executor,'sample',sampled):
                        with self.assertRaisesRegex(ValueError,'TICK_DEADLINE'):r.work(opportunity,deadline=time.monotonic()-1)
                        r.inventory=lambda *a,**kw:dict(state='UNKNOWN')
                        self.assertEqual('WAIT_INVENTORY',r.work(opportunity)['state'])
                        r.inventory=lambda *a,**kw:dict(state='MISSING')
                        f.plugin._ordinary_work_active=lambda:False
                        with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):r.work(opportunity)
                        self.assertEqual([],events);f.plugin._ordinary_work_active=lambda:True
                    return result
                with patch.object(f.m.Runtime,'advance',entered):
                    f.test_cold_exact_resource_rebuild_executes_strict_selection_and_changed_hash_defers()
                self.assertEqual([True],seen)

    def test_partial_owned_work_recovers_only_surviving_progress(self):
        for cold in (False,True):
            with self.subTest(cold=cold),partial_fixture() as d:
                r=d['runtime'];p=d['pipeline'];f=d['f'];events=[]
                r.repository=f.repo;r.authority=f.auth;r.scheduler=f.schedule
                r.verify_input=lambda saved:f.repo.get_task(f.task_id)
                r.inventory=lambda *a,**kw:dict(state='MISSING')
                r.delivery=fixtures.SimpleNamespace(rules={'rule':{'local_root':'/unused'}})
                p.executor=lambda:d['executor'];p.execute=lambda *a,**kw:d['executor'].execute('A',b'fixture',resume=True)
                p.organize=lambda *a:events.append('organize') or dict(state='WAITING')
                saved=dict(created_at=load('repository').utcnow(),effective=dict(template=dict(id='fixture',organized_rule='rule'),schedule=dict(supersession_limit=0),lifecycle=dict(oneshot_seconds=600)))
                f.repo.setting('runtime-input:round',saved)
                before=copy.deepcopy(f.auth.plan('A')['snapshot'])
                if cold:p.rounds.clear()
                else:p.rounds['A']['acquired']=time.monotonic()-301
                try:result=r.work(f.schedule.opportunity('round'))
                except ValueError as error:result=dict(state='DEFER',reason=str(error))
                self.assertEqual('WAITING',result['state'],result);self.assertEqual(['organize'],events)
                self.assertEqual([f.keys[0]],f.auth.progress('A',[0])['scope'])
                self.assertEqual(before,f.auth.plan('A')['snapshot'])

    def test_replacement_cost_refresh_uses_old_frozen_input(self):
        import test_planner as t
        with partial_fixture(supersede=False) as d:
            r=d['runtime'];p=d['pipeline'];f=d['f'];recovered=[]
            r.repository=f.repo;r.authority=f.auth;r.scheduler=f.schedule
            old_saved=dict(original='old-scope')
            f.auth.set_transfer_phase('A',f.auth.vector(f.keys),'DOWNLOADING')
            p.executor=lambda:d['executor']
            p.rounds['B']=dict(candidate=d['newer'],media=object(),acquired=time.monotonic(),mode='episode')
            p.rounds['A']['acquired']=time.monotonic()-301
            with self.assertRaisesRegex(ValueError,'ORIGINAL_RUNTIME_INPUT_REQUIRED'):
                r.candidate_plan(f.schedule.opportunity('round'),dict(replacement='new-scope'),d['new'],round_plans=[d['new']])
            self.assertIsNone(f.auth.progress('A',[1]))
            f.repo.setting('runtime-input:round',old_saved)
            recover=r.recover
            def recovered_old(plan,saved):recovered.append((plan['id'],saved));return recover(plan,saved)
            with patch.object(r,'recover',recovered_old),patch.object(d['em'],'instant',return_value=t.NOW):
                try:result=r.candidate_plan(f.schedule.opportunity('round'),dict(replacement='new-scope'),d['new'],round_plans=[d['new']])
                except ValueError as error:result=dict(authorization='DEFER',reason=str(error))
            self.assertEqual(dict(authorization='DEFER',reason='healthy download continues'),result)
            self.assertEqual([('A',old_saved)],recovered)
            self.assertEqual([f.keys[1]],f.auth.progress('A',[1])['scope'])


if __name__=='__main__':unittest.main()
