"""A paused zero-byte plan can release stale authority for a fresh decision."""
import json
import unittest
from types import SimpleNamespace

from test_runtime_fix4 import partial_fixture


class StalePlanReplanTests(unittest.TestCase):
    def run_stale(self,client_state,completed,expected,unknown=False):
        with partial_fixture(supersede=False) as d:
            runtime=d['runtime'];repo=d['f'].repo;auth=d['f'].auth
            plan=auth.plan('A');snapshot=plan['snapshot'];key=d['f'].keys[0]
            auth.update_current(key,dict(state='MISSING',evidence_ref='new-independent-scan'),expected_revision=0)
            d['q'].current[key]=dict(state='MISSING',revision=1,versions=[])
            runtime.pipeline.rounds.clear()
            client=SimpleNamespace(
                task=lambda _:dict(id='fixture-id',infohash=snapshot['infohash'],
                                   save_path=snapshot['save_path'],state=client_state,markers=['fixture-marker']),
                files=lambda _:[dict(id=f['index'],path=f['path'],size=f['size'],
                                     wanted=f['index'] in snapshot['selected_indices'],completed=completed)
                                for f in snapshot['torrent_files']])
            runtime.pipeline.clients=lambda _:client
            runtime.authority=auth
            runtime.verify_input=lambda _:repo.get_task(plan['task_id'])
            runtime.inventory=lambda *args,**kwargs:dict(state='KNOWN')
            repo.setting('runtime-input:round',{'effective':{'template':{'id':'fixture'}}})
            with repo.connection(write=True) as db:
                db.execute("UPDATE managed_downloads SET client_id='fixture-id',state='PAUSED_VERIFIED'")
                if unknown:
                    db.execute('INSERT INTO plan_actions(id,plan_id,kind,targets,files,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                               ('uncertain','A','ADD',json.dumps(list(snapshot['targets'])),
                                json.dumps(snapshot['selected_indices']),'{}','UNKNOWN',
                                '2026-09-24T00:00:00+00:00','2026-09-24T00:00:00+00:00'))
            if expected:
                result=runtime.work({'id':'round','mode':'CONTINUOUS'},immediate=True)
                self.assertEqual('WAIT_REPLAN',result['state'])
                self.assertEqual('CANCELLED',auth.plan('A')['authorization'])
                self.assertTrue(all(v['owner_plan_id'] is None for v in auth.vector(list(snapshot['targets'])).values()))
            else:
                with self.assertRaisesRegex(ValueError,'COLD_PLAN_CHANGED'):
                    runtime.work({'id':'round','mode':'CONTINUOUS'},immediate=True)
                self.assertEqual('ACTIVE',auth.plan('A')['authorization'])
                self.assertTrue(all(v['owner_plan_id']=='A' for v in auth.vector(list(snapshot['targets'])).values()))
            self.assertEqual(client_state,client.task(snapshot['infohash'])['state'])

    def test_cold_archive_revision_drift_releases_only_safe_paused_plan(self):
        self.run_stale('PAUSED',0,True)

    def test_started_or_progressing_download_keeps_stale_authority(self):
        for state,completed in (('DOWNLOADING',0),('PAUSED',1)):
            with self.subTest(state=state,completed=completed):
                self.run_stale(state,completed,False)

    def test_unknown_add_outcome_keeps_stale_authority(self):
        self.run_stale('PAUSED',0,False,unknown=True)


if __name__=='__main__':unittest.main()
