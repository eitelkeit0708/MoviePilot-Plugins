"""T070/T175 local state assertions; no provider or real-host acceptance."""
from datetime import timedelta
import unittest

import test_planner as planner
import test_scheduler as scheduler
from test_meta import native


class AcceptanceClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scheduler.SchedulerTests.setUpClass()

    def setUp(self):
        self.f = scheduler.SchedulerTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.configure(observation_enabled=False)
        with self.f.repo.connection(write=True) as db:
            db.execute("UPDATE opportunities SET mode='CONTINUOUS' WHERE id='round'")
        self.f.schedule.configure_lifecycle(self.f.task_id, self.f.keys,
            movie_days=1, tv_days=2, anchor='LAST_INGEST', now=planner.NOW)

    def handoff(self):
        f = self.f
        vector = f.claim()
        f.auth.begin_attempt('rapid', 'A', vector, 'RAPID', [0, 1], {}, now=planner.NOW)
        f.auth.record_result('rapid', 'SUCCEEDED', {'hash_verified': True}, now=planner.NOW)
        f.auth.set_transfer_phase('A', vector, 'READY_TO_PUBLISH')
        f.publish('A', vector, [0, 1])
        f.auth.record_result('publish', 'HANDED_OFF', {'entry_verified': True}, now=planner.NOW)
        return {key: f.confirmation(key) for key in f.keys}

    def test_nonzero_idle_expiry_starts_at_confirmed_ingest_and_survives_reopen(self):
        f = self.f
        proofs = self.handoff()
        at = planner.NOW + timedelta(seconds=10)
        self.assertTrue(f.auth.confirm_ingest('publish', proofs, now=at)['accepted'])
        lifecycle = f.schedule.lifecycle(f.task_id)
        self.assertEqual(f.s.stamp(at), lifecycle['last_ingest_at'])
        self.assertEqual(f.s.stamp(at + timedelta(days=2)), lifecycle['expires_at'])
        reopened = f.s.Scheduler(f.r.Repository(f.path))
        self.assertEqual(lifecycle, reopened.lifecycle(f.task_id))
        reopened.tick(now=at + timedelta(days=2) - timedelta(seconds=1))
        self.assertEqual('ACTIVE', reopened.lifecycle(f.task_id)['state'])
        reopened.tick(now=at + timedelta(days=2, seconds=1))
        self.assertEqual('EXPIRED', reopened.lifecycle(f.task_id)['state'])

    def test_rapid_handoff_completion_and_duplicate_receipt_do_not_shift_idle_clock(self):
        f = self.f
        proofs = self.handoff()
        f.schedule.update_completion(f.task_id, f.keys, scope_closed=True,
                                     collected=True, now=planner.NOW + timedelta(days=1))
        before = f.schedule.lifecycle(f.task_id)
        self.assertIsNone(before['last_ingest_at'])
        self.assertIsNone(before['expires_at'])
        at = planner.NOW + timedelta(days=1, seconds=10)
        self.assertTrue(f.auth.confirm_ingest('publish', proofs, now=at)['accepted'])
        anchored = f.schedule.lifecycle(f.task_id)
        f.schedule.update_completion(f.task_id, f.keys, scope_closed=False,
                                     collected=False, now=planner.NOW + timedelta(days=2))
        duplicate = f.auth.confirm_ingest('publish', proofs, now=planner.NOW + timedelta(days=3))
        self.assertTrue(duplicate['duplicate'])
        self.assertFalse(duplicate['accepted'])
        after = f.schedule.lifecycle(f.task_id)
        self.assertEqual(anchored['last_ingest_at'], after['last_ingest_at'])
        self.assertEqual(anchored['expires_at'], after['expires_at'])

    def test_meta_replay_preserves_real_clocks_budgets_authority_and_unknown_publish(self):
        f = self.f
        vector = f.claim()
        f.prepare('B')
        vector = f.auth.supersede('B', vector, reason='QUALITY_UPGRADE',
                                  safe_isolation=True, now=planner.NOW + timedelta(seconds=1))
        f.auth.set_transfer_phase('B', vector, 'READY_TO_PUBLISH')
        f.publish('B', vector, [0, 1])
        f.auth.record_result('publish', 'UNKNOWN', {'reason': 'timeout'}, now=planner.NOW)
        f.configure()
        f.observe(0, 0)
        f.observe(0, 19, (2160, 0))
        f.schedule.record_failure('round', 'failure-1', 'CONFIRMED_BAD_RESOURCE', now=planner.NOW)
        # Seed an existing confirmed-ingest cooldown in its real persisted row.
        with f.repo.connection(write=True) as db:
            db.execute('UPDATE target_units SET last_ingest_confirmed_at=?,cooldown_until=?',
                       (f.s.stamp(planner.NOW), f.s.stamp(planner.NOW + timedelta(seconds=100))))
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', f.auth.action('publish')['state'])
        opportunity = f.schedule.opportunity('round')
        self.assertEqual(1, opportunity['failures'])
        self.assertEqual(1, opportunity['supersessions'])
        meta = planner.load('meta')
        parser = lambda *args, **kwargs: native('GAT')
        service = meta.MetaService(f.repo, meta.MetaCorrector(), parser)
        service.parse('site:1:clock', 'GATE24', task_id=f.task_id)
        tables = ('tasks', 'opportunities', 'observations', 'target_units', 'task_lifecycle',
                  'plans', 'plan_targets', 'plan_actions', 'action_receipts', 'evidence_consumption')
        def rows():
            with f.repo.connection() as db:
                return {table: [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                        for table in tables}
        before = rows()
        before_vector = f.auth.vector(f.keys)
        self.assertTrue(before['observations'])
        self.assertTrue(before['action_receipts'])
        service = meta.MetaService(f.repo, meta.MetaCorrector(['Protected 86']), parser)
        result = service.replay(['site:1:clock'])
        self.assertEqual(1, len(result))
        self.assertEqual(2, len(f.repo.parse_history('site:1:clock')))
        self.assertEqual(before, rows())
        restarted = f.m.Authority(f.r.Repository(f.path))
        self.assertEqual(before_vector, restarted.vector(f.keys))
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', restarted.action('publish')['state'])


if __name__ == '__main__':
    unittest.main()
