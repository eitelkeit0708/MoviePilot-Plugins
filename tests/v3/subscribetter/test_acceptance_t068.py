"""T068 exact E07 failure, ingested E08 and shared-asset retention."""
import json
from pathlib import Path
import tempfile
import unittest

from test_planner import load


def scenario():
    repository, planner, scheduler, execution = (
        load('repository'), load('planner'), load('scheduler'), load('execution'))
    with tempfile.TemporaryDirectory() as temporary:
        repo = repository.Repository(Path(temporary) / 'state.db')
        task = repo.submit('t068', repository.Target('电视剧', 'themoviedb', '42', 1), {}, 'admin', 42, True)
        repo.complete_handoff(task['id'], task['generation'])
        target = repository.Target.from_task(task)
        units = [planner.TargetUnit(target, episode) for episode in (7, 8)]
        e07, e08 = [unit.key for unit in units]
        scheduler.Scheduler(repo).open_opportunity('round', task['id'], units, mode='ONESHOT',
            config=scheduler.ScheduleConfig(observation_enabled=False), now=scheduler.instant())
        with repo.connection(write=True) as db:
            db.execute('UPDATE target_units SET current_revision=1,current_facts=? WHERE target_key=?',
                       (json.dumps({'version_id': 'mp-history-e08', 'evidence_ref': 'archive:e08'}), e08))
        authority = planner.Authority(repo)
        authority.set_revisions('policy', 'parse')
        files = [
            dict(index=0, path='Show.S01E07.mkv', size=70, role='video', targets=[e07], requires=[]),
            dict(index=1, path='Show.S01E08.mkv', size=80, role='video', targets=[e08], requires=[]),
            dict(index=2, path='Show.S01.shared.ass', size=8, role='subtitle', targets=[e07, e08], requires=[]),
        ]
        snapshot = dict(candidate_key='site:1:pack', infohash='6' * 40, downloader='qb', save_path='/test',
            policy_revision='policy', parse_revision='parse',
            current={e07: dict(state='MISSING', revision=0), e08: dict(state='PRESENT', revision=1)},
            targets={
                e07: dict(action='ACQUIRE', reason='MISSING', evidence_keys=[], quality=[1], evidence_source='none'),
                e08: dict(action='UNCHANGED', reason='EQUIVALENT', evidence_keys=['archive:e08'], quality=[1], evidence_source='explicit'),
            }, torrent_files=files, selected_indices=[0, 1, 2],
            verified=dict(identity=True, scope=True, admission=True, files=True, configuration=True))
        authority.prepare('A', 'round', snapshot, now=scheduler.instant())
        vector = authority.claim('A', authority.vector([e07, e08]), now=scheduler.instant())
        before = authority.active_files('qb', '6' * 40, '/test')
        exclusions = execution.Exclusions(repo)
        exclusions.add('fail-e07', {'targets': [e07]}, reason='confirmed E07 failure')
        authority.cancel('A', {e07: vector[e07]}, reason='E07_FAILED')
        failed = authority.vector([e07, e08])
        retained = authority.active_files('qb', '6' * 40, '/test')
        with repo.connection() as db:
            plan_a = {row['target_key']: row['state'] for row in db.execute(
                "SELECT target_key,state FROM plan_targets WHERE plan_id='A'")}
        exclusions.revoke('fail-e07')
        recovery = json.loads(json.dumps(snapshot))
        recovery.update(candidate_key='site:1:e07-recovery', current={e07: snapshot['current'][e07]},
                        targets={e07: snapshot['targets'][e07]}, selected_indices=[0])
        authority.prepare('B', 'round', recovery, now=scheduler.instant())
        authority.claim('B', authority.vector([e07]), now=scheduler.instant())
        recovered = authority.vector([e07, e08])
        return {
            'before_active_files': before,
            'failure': {'plan_targets': {'E07': plan_a[e07], 'E08': plan_a[e08]},
                        'owners': {'E07': failed[e07]['owner_plan_id'], 'E08': failed[e08]['owner_plan_id']},
                        'active_files': retained, 'e07_excluded': True},
            'recovery': {'owners': {'E07': recovered[e07]['owner_plan_id'], 'E08': recovered[e08]['owner_plan_id']},
                         'active_files': authority.active_files('qb', '6' * 40, '/test'),
                         'shared_file_owner': 'A'},
        }


class T068AcceptanceTests(unittest.TestCase):
    def test_e07_failure_and_recovery_preserve_ingested_e08_and_shared_reference(self):
        proof = scenario()
        self.assertEqual([0, 1, 2], proof['before_active_files'])
        self.assertEqual({'E07': 'CANCELLED', 'E08': 'ACTIVE'}, proof['failure']['plan_targets'])
        self.assertEqual([1, 2], proof['failure']['active_files'])
        self.assertEqual({'E07': None, 'E08': 'A'}, proof['failure']['owners'])
        self.assertEqual({'E07': 'B', 'E08': 'A'}, proof['recovery']['owners'])
        self.assertEqual([0, 1, 2], proof['recovery']['active_files'])
        self.assertEqual('A', proof['recovery']['shared_file_owner'])


if __name__ == '__main__':
    print(json.dumps(scenario(), ensure_ascii=False, indent=2))
