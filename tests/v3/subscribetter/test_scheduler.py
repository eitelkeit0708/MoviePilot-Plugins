"""W04 clock and lifecycle evidence with timezone-aware fictional time."""
from datetime import timedelta
from dataclasses import asdict
import json
import unittest
import test_planner as fixture


class SchedulerTests(unittest.TestCase):
    setUpClass = classmethod(fixture.AuthorityTests.setUpClass.__func__)
    setUp = fixture.AuthorityTests.setUp
    spec = fixture.AuthorityTests.spec
    prepare = fixture.AuthorityTests.prepare
    claim = fixture.AuthorityTests.claim
    publish = fixture.AuthorityTests.publish

    def configure(self, **overrides):
        config = dict(observation_enabled=True, base_seconds=20, quiet_seconds=10, max_seconds=60,
                      cooldown_enabled=True, cooldown_seconds=100, supersession_limit=3,
                      supersession_seconds=3600, failure_limit=2)
        config.update(overrides)
        config = self.s.ScheduleConfig(**config)
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE opportunities SET config=? WHERE id=?', (json.dumps(asdict(config), sort_keys=True), 'round'))
        return config

    def observe(self, index, seconds, quality=(1080, 0), key='candidate'):
        return self.schedule.observe('round', self.keys[index], key, quality, eligible=True,
                                     now=fixture.NOW + timedelta(seconds=seconds))

    def test_observation_repeats_and_better_do_not_move_hard_cap(self):
        self.configure()
        first = self.observe(0, 0)
        repeat = self.observe(0, 5, key='other-site')
        self.assertEqual(first['first_seen'], repeat['first_seen'])
        self.assertEqual(first['last_better'], repeat['last_better'])
        improved = self.observe(0, 19, (2160, 0))
        self.assertEqual(self.s.stamp(fixture.NOW + timedelta(seconds=29)), improved['deadline'])
        last = self.observe(0, 59, (2160, 2))
        self.assertEqual(self.s.stamp(fixture.NOW + timedelta(seconds=60)), last['deadline'])
        reopened = self.s.Scheduler(self.r.Repository(self.path))
        self.assertTrue(reopened.ready('round', self.keys[0], 'QUALITY_UPGRADE', now=fixture.NOW + timedelta(seconds=60))['ready'])

    def test_config_requires_explicit_finite_durations_and_aware_time(self):
        for changes in ({'max_seconds': None}, {'max_seconds': float('inf')}, {'max_seconds': 0},
                        {'base_seconds': 61}, {'quiet_seconds': -1}, {'cooldown_seconds': float('nan')},
                        {'supersession_limit': True}, {'normal_download_preemption': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configure(**changes)
        self.configure(base_seconds=0, quiet_seconds=0)
        self.assertTrue(self.observe(0, 0))
        with self.assertRaises(ValueError):
            self.schedule.observe('round', self.keys[0], 'x', (1,), eligible=True, now=fixture.NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            self.schedule.observe('round', self.keys[0], 'x', (1,), eligible=False, now=fixture.NOW)

    def test_each_target_must_mature_and_immediate_is_time_only(self):
        self.configure()
        self.observe(0, 0)
        self.observe(1, 15)
        self.prepare()
        vector = self.auth.vector(self.keys)
        with self.assertRaises(ValueError):
            self.auth.claim('A', vector, now=fixture.NOW + timedelta(seconds=20))
        self.assertTrue(all(v['owner_plan_id'] is None for v in self.auth.vector(self.keys).values()))
        a = self.auth.claim('A', vector, now=fixture.NOW + timedelta(seconds=20), immediate=True)
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.prepare('B')
        with self.assertRaises(ValueError):
            self.auth.supersede('B', a, reason='better', safe_isolation=True, now=fixture.NOW, immediate=True)

    def confirmation(self, key):
        return {'receipt_id': 'ingest-' + str(self.keys.index(key)), 'version_id': 'v-' + str(self.keys.index(key)),
                'association_verified': True, 'all_assets_verified': True, 'improvement_verified': True,
                'consumer_settled': True, 'evidence_ref': 'fictional-final-association'}

    def test_ingest_only_clock_and_oneshot_archive_persist(self):
        self.configure(observation_enabled=False)
        a = self.claim()
        self.auth.begin_attempt('rapid', 'A', a, 'RAPID', [0, 1], {'isolated': True}, now=fixture.NOW)
        self.auth.record_result('rapid', 'SUCCEEDED', {'hash_verified': True}, now=fixture.NOW)
        self.assertIsNone(self.schedule.target(self.keys[0])['cooldown_until'])
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'HANDED_OFF', {'entry_verified': True}, now=fixture.NOW)
        self.assertIsNone(self.schedule.target(self.keys[0])['cooldown_until'])
        proofs = {key: self.confirmation(key) for key in self.keys}
        self.assertTrue(self.auth.confirm_ingest('publish', proofs, now=fixture.NOW + timedelta(seconds=10))['accepted'])
        before = self.schedule.target(self.keys[0])
        duplicate = self.auth.confirm_ingest('publish', proofs, now=fixture.NOW + timedelta(days=2))
        self.assertFalse(duplicate['accepted'], 'duplicate must not authorize a second archive write')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(before['cooldown_until'], self.schedule.target(self.keys[0])['cooldown_until'])
        self.assertEqual('ARCHIVED', self.schedule.opportunity('round')['state'])
        self.assertEqual(before, self.s.Scheduler(self.r.Repository(self.path)).target(self.keys[0]))
        self.assertEqual('ARCHIVED', self.schedule.open_opportunity('round', self.task_id, self.units, mode='ONESHOT',
                          config=self.s.ScheduleConfig(**json.loads(self.schedule.opportunity('round')['config'])), now=fixture.NOW)['state'])

    def test_cooldown_overlaps_observation_and_does_not_block_repairs(self):
        self.configure()
        self.observe(0, 0)
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE target_units SET cooldown_until=? WHERE target_key=?', (self.s.stamp(fixture.NOW + timedelta(seconds=40)), self.keys[0]))
        for action in ('QUALITY_UPGRADE', 'EVIDENCE_UPGRADE'):
            value = self.schedule.ready('round', self.keys[0], action, now=fixture.NOW + timedelta(seconds=25))
            self.assertEqual(self.s.stamp(fixture.NOW + timedelta(seconds=40)), value['earliest'])
            self.assertFalse(value['ready'])
        for action in ('ACQUIRE', 'REPLACE_INVALID', 'SIDECAR_SUPPLEMENT'):
            self.assertTrue(self.schedule.ready('round', self.keys[0], action, now=fixture.NOW + timedelta(seconds=25))['ready'])
        self.observe(0, 45, (2160, 2))
        self.assertEqual(self.s.stamp(fixture.NOW + timedelta(seconds=55)), self.schedule.ready('round', self.keys[0], 'QUALITY_UPGRADE', now=fixture.NOW + timedelta(seconds=45))['earliest'])

    def test_expiry_fixed_idle_and_unknown_end_keep_missing_eligible(self):
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE opportunities SET mode='CONTINUOUS' WHERE id='round'")
        self.schedule.configure_lifecycle(self.task_id, self.keys, movie_days=1, tv_days=2,
                                          anchor='COMPLETE_COLLECTED', now=fixture.NOW)
        self.schedule.update_completion(self.task_id, self.keys, scope_closed=False, collected=True, now=fixture.NOW)
        self.schedule.tick(now=fixture.NOW + timedelta(days=10))
        self.assertEqual('ACTIVE', self.schedule.lifecycle(self.task_id)['state'])
        self.schedule.update_completion(self.task_id, self.keys, scope_closed=True, collected=True, now=fixture.NOW)
        self.schedule.tick(now=fixture.NOW + timedelta(days=3))
        self.assertEqual('EXPIRED', self.schedule.lifecycle(self.task_id)['state'])
        self.assertFalse(self.schedule.ready('round', self.keys[0], 'QUALITY_UPGRADE', now=fixture.NOW + timedelta(days=3))['ready'])
        self.assertTrue(self.schedule.ready('round', self.keys[0], 'ACQUIRE', now=fixture.NOW + timedelta(days=3))['ready'])
        self.assertEqual('ACTIVE', self.repo.get_task(self.task_id)['state'])
        self.schedule.configure_lifecycle(self.task_id, self.keys, movie_days=1, tv_days=0,
                                          anchor='LAST_INGEST', now=fixture.NOW)
        self.assertIsNone(self.schedule.lifecycle(self.task_id)['expires_at'])

    def test_opportunity_lineage_failure_budget_and_stopped(self):
        first = self.schedule.opportunity('round')
        config = self.s.ScheduleConfig(**json.loads(first['config']))
        self.assertEqual('round', self.schedule.open_opportunity('duplicate-rss', self.task_id, self.units, mode='ONESHOT', config=config, now=fixture.NOW)['id'])
        self.schedule.record_failure('round', 'failure-1', 'CONFIRMED_BAD_RESOURCE', now=fixture.NOW)
        self.schedule.record_failure('round', 'failure-1', 'CONFIRMED_BAD_RESOURCE', now=fixture.NOW)
        self.assertEqual(1, self.schedule.opportunity('round')['failures'])
        self.schedule.record_failure('round', 'failure-2', 'CONFIRMED_BAD_RESOURCE', now=fixture.NOW)
        with self.assertRaises(ValueError):
            self.schedule.record_failure('round', 'failure-3', 'CONFIRMED_BAD_RESOURCE', now=fixture.NOW)
        self.repo.set_state(self.task_id, 'STOPPED', 'admin')
        with self.assertRaises(ValueError):
            self.schedule.open_opportunity('revive', self.task_id, self.units, mode='ONESHOT', config=config, now=fixture.NOW)


    def test_out_of_order_better_candidate_never_rewinds_quiet_window(self):
        self.configure()
        self.observe(0, 0)
        latest = self.observe(0, 40, (2160, 0))
        delayed = self.observe(0, 30, (2160, 2))
        self.assertEqual(latest['last_better'], delayed['last_better'])
        self.assertEqual(latest['deadline'], delayed['deadline'])

    def test_final_archive_transaction_rollback_and_old_receipt_do_not_consume(self):
        self.configure(observation_enabled=False)
        spec = self.spec()
        for target in spec['targets'].values():
            target['evidence_keys'] = ['one-shared-physical-version']
        self.auth.prepare('A', 'round', spec, now=fixture.NOW)
        a = self.auth.claim('A', self.auth.vector(self.keys), now=fixture.NOW)
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'HANDED_OFF', {}, now=fixture.NOW)
        proofs = {key: self.confirmation(key) for key in self.keys}
        with self.assertRaisesRegex(RuntimeError, 'archive failure'):
            with self.repo.connection(write=True) as db:
                self.assertTrue(self.auth.confirm_ingest('publish', proofs, db=db, now=fixture.NOW)['accepted'])
                raise RuntimeError('archive failure')
        self.assertEqual('HANDED_OFF', self.auth.action('publish')['state'])
        self.assertIsNone(self.schedule.target(self.keys[0])['cooldown_until'])
        with self.repo.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])
        self.assertTrue(self.auth.confirm_ingest('publish', proofs, now=fixture.NOW)['accepted'])
        with self.repo.connection() as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])

    def test_sidecar_ingest_preserves_quality_clock(self):
        self.configure(observation_enabled=False)
        spec = self.spec()
        for target in spec['targets'].values():
            target['action'], target['reason'] = 'SIDECAR_SUPPLEMENT', 'MISSING_REQUIRED_SUBTITLE'
        self.auth.prepare('A', 'round', spec, now=fixture.NOW)
        a = self.auth.claim('A', self.auth.vector(self.keys), now=fixture.NOW)
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'HANDED_OFF', {}, now=fixture.NOW)
        self.auth.confirm_ingest('publish', {key: self.confirmation(key) for key in self.keys}, now=fixture.NOW)
        self.assertIsNone(self.schedule.target(self.keys[0])['last_ingest_confirmed_at'])

    def test_real_bounded_tick_is_wired_to_host_reconcile(self):
        import test_ownership
        from unittest.mock import Mock
        test_ownership.PluginTests.setUpClass()
        plugin_test = test_ownership.PluginTests()
        plugin_test.setUp()
        try:
            plugin = plugin_test.plugin
            self.assertTrue(hasattr(plugin, 'scheduler'), 'W04 bounded lifecycle service not wired')
            plugin.scheduler.tick = Mock(return_value={'checked': 0})
            plugin.reconcile(generation=plugin.generation)
            plugin.scheduler.tick.assert_called_once_with()
            plugin.stop_service()
            plugin.scheduler.tick.reset_mock()
            plugin.reconcile(generation=plugin.generation - 1)
            plugin.scheduler.tick.assert_not_called()
        finally:
            plugin_test.doCleanups()


    def test_stopped_late_ingest_is_receipt_only_and_continuous_round_closes(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'HANDED_OFF', {}, now=fixture.NOW)
        self.repo.set_state(self.task_id, 'STOPPED', 'admin')
        proofs = {key: self.confirmation(key) for key in self.keys}
        result = self.auth.confirm_ingest('publish', proofs, now=fixture.NOW)
        self.assertFalse(result['accepted'])
        self.assertIsNone(self.schedule.target(self.keys[0])['last_ingest_confirmed_at'])
        self.assertEqual('HANDED_OFF', self.auth.vector(self.keys)[self.keys[0]]['publish_phase'])

    def test_continuous_success_closes_round_but_keeps_task_and_target_cooldown(self):
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE opportunities SET mode='CONTINUOUS' WHERE id='round'")
        self.configure(observation_enabled=False)
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'HANDED_OFF', {}, now=fixture.NOW)
        self.auth.confirm_ingest('publish', {key: self.confirmation(key) for key in self.keys}, now=fixture.NOW)
        self.assertEqual('COMPLETED', self.schedule.opportunity('round')['state'])
        self.assertEqual('ACTIVE', self.repo.get_task(self.task_id)['state'])
        self.assertIsNotNone(self.schedule.target(self.keys[0])['cooldown_until'])


if __name__ == '__main__':
    unittest.main()
