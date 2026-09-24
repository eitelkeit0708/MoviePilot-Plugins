"""W04 deterministic SQLite authority checks; no downloader/cloud acceptance implied."""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / 'plugins.v3/subscribetter'


def load(name):
    package = sys.modules.setdefault('w04_subscribetter', types.ModuleType('w04_subscribetter'))
    package.__path__ = [str(PLUGIN)]
    spec = importlib.util.spec_from_file_location('w04_subscribetter.' + name, PLUGIN / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = load('repository')
        if (PLUGIN / 'planner.py').exists():
            cls.m = load('planner')
            cls.s = load('scheduler')

    def setUp(self):
        self.assertTrue((PLUGIN / 'planner.py').exists(), 'W04 target authority missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.repo = self.r.Repository(self.path)
        task = self.repo.submit('intent', self.r.Target('电视剧', 'tmdb', '00042', 0, 'specials'), {}, 'admin', 42, True)
        self.repo.complete_handoff(task['id'], task['generation'])
        self.task_id = task['id']
        self.units = [self.m.TargetUnit(self.r.Target.from_task(task), episode) for episode in (7, 8)]
        self.keys = [unit.key for unit in self.units]
        self.schedule = self.s.Scheduler(self.repo)
        self.schedule.open_opportunity('round', self.task_id, self.units, mode='ONESHOT',
                                       config=self.s.ScheduleConfig(observation_enabled=False, supersession_limit=3,
                                                                   supersession_seconds=3600, failure_limit=2), now=NOW)
        self.auth = self.m.Authority(self.repo)
        self.auth.set_revisions('policy-1', 'parse-1')

    def spec(self, keys=None, candidate='A', shared=False):
        keys = keys or self.keys
        files = [{'index': i, 'path': f'E{7+i:02}.mkv', 'size': 100, 'role': 'video',
                  'targets': [key], 'requires': []} for i, key in enumerate(keys)]
        if shared:
            files = [{'index': 0, 'path': 'E07-E08.mkv', 'size': 200, 'role': 'video', 'targets': keys, 'requires': []}]
        return dict(candidate_key=candidate, infohash=('a' if candidate == 'A' else 'b') * 40,
                    downloader='isolated', save_path='/test', policy_revision='policy-1', parse_revision='parse-1',
                    current={key: {'revision': 0, 'state': 'MISSING'} for key in keys},
                    targets={key: {'action': 'ACQUIRE', 'reason': 'MISSING', 'evidence_keys': [], 'quality': [1 if candidate == 'A' else 2], 'evidence_source': 'none'} for key in keys},
                    torrent_files=files, selected_indices=[f['index'] for f in files],
                    verified=dict(identity=True, scope=True, admission=True, files=True, configuration=True))

    def prepare(self, pid='A', keys=None, **kw):
        self.auth.prepare(pid, 'round', self.spec(keys, pid, **kw), now=NOW)
        return pid

    def claim(self, pid='A', keys=None, **kw):
        self.prepare(pid, keys, **kw)
        return self.auth.claim(pid, self.auth.vector(keys or self.keys), now=NOW)

    def publish(self, pid, vector, indices, key='publish'):
        return self.auth.begin_publish(key, pid, vector, indices,
                                       validation={'policy_revision': 'policy-1', 'parse_revision': 'parse-1',
                                                   'current_revisions': {k: 0 for k in vector},
                                                   'checks': {k: {'identity': True, 'admission': True, 'scope': True,
                                                                  'not_excluded': True, 'current_allows': True,
                                                                  'assets_complete': True, 'remote_verified': True} for k in vector}}, now=NOW)

    def test_atomic_claim_before_callbacks_two_connections(self):
        self.prepare('A')
        self.prepare('B')
        initial = self.auth.vector(self.keys)
        gate = threading.Barrier(2)
        def attempt(pid):
            auth = self.m.Authority(self.r.Repository(self.path))
            gate.wait()
            try:
                return auth.claim(pid, initial, now=NOW)
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ('A', 'B')))
        self.assertEqual(1, sum(r is not None for r in results))
        owners = {v['owner_plan_id'] for v in self.auth.vector(self.keys).values()}
        self.assertEqual(1, len(owners))
        self.assertNotIn(None, owners)

    def test_no_partial_claim_and_frozen_snapshot(self):
        original = self.spec()
        self.auth.prepare('A', 'round', original, now=NOW)
        original['torrent_files'][0]['path'] = 'MUTATED'
        vector = self.auth.vector(self.keys)
        vector[self.keys[1]]['generation'] = 99
        with self.assertRaises(ValueError):
            self.auth.claim('A', vector, now=NOW)
        self.assertTrue(all(v['owner_plan_id'] is None for v in self.auth.vector(self.keys).values()))
        self.assertNotIn('MUTATED', str(self.auth.plan('A')))

    def test_new_plan_rejects_old_selected_attachment_scope(self):
        spec = self.spec()
        spec['torrent_files'].append({'index': 2, 'path': 'font.ttf', 'size': 20, 'role': 'attachment',
                                      'targets': [self.keys[0]], 'requires': []})
        spec['torrent_files'][0]['requires'] = [2]
        spec['selected_indices'].append(2)
        with self.assertRaisesRegex(ValueError, 'ASSET_SCOPE_REBIND_REQUIRED'):
            self.auth.prepare('old-style', 'round', spec, now=NOW)

    def test_frozen_legacy_ten_asset_plan_remains_idempotent_and_immutable(self):
        spec = self.spec()
        historical = [('subtitle.ass', 'subtitle'), ('subtitle.idx', 'subtitle'), ('subtitle.sub', 'subtitle'),
                      ('font.ttf', 'attachment'), ('font.otf', 'attachment'), ('LICENSE.txt', 'other'),
                      ('poster.jpg', 'other'), ('notes.nfo', 'other')]
        spec['torrent_files'].extend(dict(index=index, path=path, size=10, role=role,
                                          targets=[self.keys[0]], requires=[])
                                             for index, (path, role) in enumerate(historical, 2))
        spec['selected_indices'] = list(range(10))
        text = self.m.encoded(spec)
        with self.repo.connection(write=True) as db:
            generation = db.execute('SELECT generation FROM tasks WHERE id=?', (self.task_id,)).fetchone()[0]
            db.execute("INSERT INTO plans(id,opportunity_id,task_id,snapshot,authorization,transfer_phase,created_at,task_generation) VALUES(?,?,?,?,'PREPARED','PENDING',?,?)",
                       ('legacy', 'round', self.task_id, text, self.s.stamp(NOW), generation))
            for key in self.keys:
                db.execute("INSERT INTO plan_targets(plan_id,target_key,state,action) VALUES('legacy',?,'PREPARED','ACQUIRE')", (key,))
        self.assertEqual(list(range(10)), self.auth.prepare('legacy', 'round', spec, now=NOW)['snapshot']['selected_indices'])
        changed = self.spec()
        with self.assertRaisesRegex(ValueError, 'immutable plan id reused'):
            self.auth.prepare('legacy', 'round', changed, now=NOW)

    def test_superseded_rapid_receipt_never_revives_or_dispatches(self):
        a = self.claim()
        attempt = self.auth.begin_attempt('rapid-A', 'A', a, 'RAPID', [0, 1], {'isolated': True}, now=NOW)
        self.assertTrue(attempt['dispatch'])
        self.prepare('B')
        b = self.auth.supersede('B', a, reason='QUALITY_UPGRADE', safe_isolation=True, now=NOW + timedelta(seconds=1))
        self.assertTrue(all(x['generation'] == 2 for x in b.values()))
        self.auth.record_result('rapid-A', 'SUCCEEDED', {'object_id': 'old-isolated'}, now=NOW + timedelta(days=1))
        self.assertEqual('SUPERSEDED', self.auth.plan('A')['authorization'])
        with self.assertRaises(ValueError):
            self.auth.begin_attempt('late-CD2', 'A', a, 'CD2_UPLOAD', [0], {'isolated': True}, now=NOW)
        self.assertEqual('B', self.auth.vector(self.keys)[self.keys[0]]['owner_plan_id'])
        self.assertIsNone(self.schedule.target(self.keys[0])['last_ingest_confirmed_at'])

    def test_publish_barrier_survives_unknown_stop_and_restart(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.assertTrue(self.publish('A', a, [0, 1])['dispatch'])
        self.auth.record_result('publish', 'UNKNOWN', {'reason': 'timeout'}, now=NOW)
        self.repo.set_state(self.task_id, 'STOPPED', 'admin')
        restarted = self.m.Authority(self.r.Repository(self.path))
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', restarted.vector(self.keys)[self.keys[0]]['publish_phase'])
        with self.assertRaises(ValueError):
            self.repo.begin_release(self.task_id, self.repo.get_task(self.task_id)['generation'], 'admin')
        with self.assertRaises(ValueError):
            restarted.resolve_publish('publish', 'NOT_SENT', {'lease_expired': True}, now=NOW + timedelta(days=30))

    def test_publish_and_supersede_serialize(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.prepare('B')
        gate = threading.Barrier(2)
        def run(kind):
            gate.wait()
            try:
                if kind == 'publish':
                    self.publish('A', a, [0, 1])
                else:
                    self.auth.supersede('B', a, reason='QUALITY_UPGRADE', safe_isolation=True, now=NOW)
                return kind
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ('publish', 'replace')))
        self.assertEqual(1, sum(v is not None for v in results))

    def test_partial_supersession_preserves_sibling_and_blocks_old_whole_batch(self):
        a = self.claim()
        self.prepare('B', [self.keys[0]])
        self.auth.supersede('B', {self.keys[0]: a[self.keys[0]]}, reason='QUALITY_UPGRADE', safe_isolation=True, now=NOW)
        self.assertEqual('ACTIVE', self.auth.plan('A')['authorization'])
        self.auth.set_transfer_phase('A', {self.keys[1]: a[self.keys[1]]}, 'READY_TO_PUBLISH')
        with self.assertRaises(ValueError):
            self.publish('A', {self.keys[1]: a[self.keys[1]]}, [0, 1])
        self.assertTrue(self.publish('A', {self.keys[1]: a[self.keys[1]]}, [1])['dispatch'])
        self.assertEqual([1], self.auth.active_files('isolated', 'a' * 40, '/test'))


    def test_queue_replay_and_unknown_attempt_do_not_duplicate_rpc(self):
        a = self.claim()
        self.auth.queue_attempt('queued', 'A', a, 'ADD', [0, 1], {}, now=NOW)
        self.assertTrue(self.auth.begin_attempt('queued', 'A', a, 'ADD', [0, 1], {}, now=NOW)['dispatch'])
        self.assertFalse(self.auth.begin_attempt('queued', 'A', a, 'ADD', [0, 1], {}, now=NOW)['dispatch'])
        with self.assertRaises(ValueError):
            self.auth.begin_attempt('different-key', 'A', a, 'ADD', [0, 1], {}, now=NOW)
        self.auth.queue_attempt('resume', 'A', a, 'RESUME', [0, 1], {}, now=NOW)
        self.prepare('B')
        self.auth.supersede('B', a, reason='better', safe_isolation=True, now=NOW)
        self.assertEqual('CANCELLED', self.auth.action('resume')['state'])
        with self.assertRaises(ValueError):
            self.auth.begin_attempt('resume', 'A', a, 'RESUME', [0, 1], {}, now=NOW)

    def test_current_revision_and_exclusion_changes_block_dispatch_and_publish(self):
        a = self.claim()
        self.auth.update_current(self.keys[0], {'state': 'PRESENT', 'evidence_ref': 'new-high-version'}, expected_revision=0)
        with self.assertRaises(ValueError):
            self.auth.begin_attempt('resume', 'A', a, 'RESUME', [0, 1], {}, now=NOW)
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        with self.assertRaises(ValueError):
            self.publish('A', a, [0, 1])

    def test_shared_physical_video_keeps_reference_but_cannot_publish_half(self):
        a = self.claim(shared=True)
        self.prepare('B', [self.keys[0]])
        self.auth.supersede('B', {self.keys[0]: a[self.keys[0]]}, reason='better', safe_isolation=True, now=NOW)
        self.assertEqual([0], self.auth.active_files('isolated', 'a' * 40, '/test'))
        self.auth.set_transfer_phase('A', {self.keys[1]: a[self.keys[1]]}, 'READY_TO_PUBLISH')
        with self.assertRaises(ValueError):
            self.publish('A', {self.keys[1]: a[self.keys[1]]}, [0])

    def test_sibling_phase_does_not_allow_preemption_of_healthy_download(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'DOWNLOADING')
        self.auth.set_transfer_phase('A', {self.keys[0]: a[self.keys[0]]}, 'RAPID_WAIT')
        self.prepare('B', [self.keys[1]])
        with self.assertRaises(ValueError):
            self.auth.supersede('B', {self.keys[1]: a[self.keys[1]]}, reason='better', safe_isolation=True, now=NOW)

    def test_bounded_failure_recovery_reuses_resource_in_new_plan_only(self):
        a = self.claim()
        self.prepare('B')
        b = self.auth.supersede('B', a, reason='better', safe_isolation=True, now=NOW)
        self.schedule.record_failure('round', 'B-failed', 'CONFIRMED_BAD_RESOURCE', now=NOW)
        self.auth.prepare('A-new', 'round', self.spec(candidate='A'), now=NOW)
        new = self.auth.recover('A-new', b, failure_id='B-failed', safe_isolation=True, now=NOW)
        self.assertEqual('A-new', new[self.keys[0]]['owner_plan_id'])
        self.assertEqual('SUPERSEDED', self.auth.plan('A')['authorization'])
        self.assertEqual(1, self.schedule.opportunity('round')['supersessions'])
        self.assertEqual(1, self.schedule.opportunity('round')['failures'])
        self.auth.prepare('another', 'round', self.spec(candidate='B'), now=NOW)
        with self.assertRaises(ValueError):
            self.auth.recover('another', new, failure_id='B-failed', safe_isolation=True, now=NOW)

    def test_T120_failed_B_replans_superseded_A_but_permanent_exclusion_blocks_it(self):
        policy_module, candidates, execution = load('policy'), load('candidates'), load('execution')
        policy = policy_module.Policy({'tv': '欧美剧'}, 7)
        facts = policy.normalize({'title': 'Fictional 2160p WEB-DL -HHWEB 中文字幕',
                                  'description': '', 'labels': []})
        candidate = dict(candidate_key='A', infohash='a' * 40, downloader='isolated',
                         save_path='/test', parse_revision='parse-1',
                         facts={key: facts for key in self.keys},
                         classification={'state': 'complete', 'policy_revision': 7,
                                         'effective': {'category_id': 'tv'}},
                         torrent_files=self.spec()['torrent_files'], available=True,
                         identity_ok=True, scope_ok=True, parse_status='OK',
                         files_verified=True, configuration_verified=True)
        current = {key: {'state': 'MISSING', 'revision': 0, 'versions': []} for key in self.keys}
        pipeline = candidates.CandidatePipeline(types.SimpleNamespace(repository=self.repo), None,
            policy, lambda _: current, lambda _: object())

        a = self.claim()
        self.auth.queue_attempt('old-A-resume', 'A', a, 'RESUME', [0, 1], {}, now=NOW)
        self.prepare('B')
        b = self.auth.supersede('B', a, reason='QUALITY_UPGRADE', safe_isolation=True, now=NOW)
        self.assertEqual('CANCELLED', self.auth.action('old-A-resume')['state'])
        self.schedule.record_failure('round', 'B-failed', 'CONFIRMED_BAD_RESOURCE', now=NOW)
        self.assertTrue(pipeline._evaluate(candidate, self.keys, 'episode')['plans'])

        exclusions = execution.Exclusions(self.repo)
        exclusions.add('permanent-A', {'candidate_key': 'A'}, reason='USER_EXCLUDED')
        self.assertEqual([], pipeline._evaluate(candidate, self.keys, 'episode')['plans'])
        self.assertEqual('B', self.auth.vector(self.keys)[self.keys[0]]['owner_plan_id'])
        exclusions.revoke('permanent-A')
        self.assertTrue(pipeline._evaluate(candidate, self.keys, 'episode')['plans'])
        self.auth.prepare('A-new', 'round', self.spec(candidate='A'), now=NOW)
        recovered = self.auth.recover('A-new', b, failure_id='B-failed', safe_isolation=True, now=NOW)
        self.assertEqual('A-new', recovered[self.keys[0]]['owner_plan_id'])
        self.assertEqual('SUPERSEDED', self.auth.plan('A')['authorization'])
        with self.assertRaises(ValueError):
            self.auth.begin_attempt('revived-old-A', 'A', a, 'RESUME', [0, 1], {}, now=NOW)
        self.assertTrue(self.auth.begin_attempt('new-A-add', 'A-new', recovered,
            'ADD', [0, 1], {}, now=NOW)['dispatch'])
        self.assertEqual((1, 1), (self.schedule.opportunity('round')['failures'],
                                   self.schedule.opportunity('round')['supersessions']))

    def test_early_preemption_requires_real_selected_file_cost_and_status(self):
        from dataclasses import asdict
        import json
        config = self.s.ScheduleConfig(supersession_limit=3, supersession_seconds=3600, failure_limit=2,
                                       normal_download_preemption=True, max_downloaded_bytes=50, min_remaining_seconds=30)
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE opportunities SET config=? WHERE id=?', (json.dumps(asdict(config)), 'round'))
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'DOWNLOADING')
        self.prepare('B')
        for cost in ({}, {'downloaded_bytes': 0, 'remaining_seconds': float('nan')},
                     {'downloaded_bytes': 70, 'remaining_seconds': 50}, {'downloaded_bytes': 20, 'remaining_seconds': 5}):
            with self.assertRaises(ValueError):
                self.auth.supersede('B', a, reason='better', safe_isolation=True, now=NOW,
                                    progress={'A': dict(cost, scope=sorted(self.keys), sampled_at=self.s.stamp(NOW), status='DOWNLOADING')})
        self.assertTrue(self.auth.supersede('B', a, reason='better', safe_isolation=True, now=NOW,
                        progress={'A': dict(downloaded_bytes=20, remaining_seconds=60, scope=sorted(self.keys), sampled_at=self.s.stamp(NOW), status='DOWNLOADING')}))


    def test_formal_replacement_requires_actual_improvement_not_title(self):
        a = self.claim()
        spec = self.spec(candidate='B')
        for target in spec['targets'].values():
            target['quality'] = [0]
        self.auth.prepare('lower', 'round', spec, now=NOW)
        with self.assertRaises(ValueError):
            self.auth.supersede('lower', a, reason='BETTER_TITLE', safe_isolation=True, now=NOW)
        self.assertEqual('A', self.auth.vector(self.keys)[self.keys[0]]['owner_plan_id'])

    def test_progress_is_selected_file_evidence_unknown_not_torrent_zero(self):
        a = self.claim()
        sample = self.auth.record_progress('A', [0], {0: {'downloaded_bytes': None, 'speed': None}},
                                          torrent={'progress': 0.6}, status='CHECKING', now=NOW)
        self.assertIsNone(sample['downloaded_bytes'])
        self.assertEqual(100, sample['total_bytes'])
        self.assertEqual('CHECKING', sample['status'])
        self.assertEqual(sample, self.m.Authority(self.r.Repository(self.path)).progress('A', [0]))
        sample = self.auth.record_progress('A', [0], {0: {'downloaded_bytes': 20, 'speed': 2}},
                                          torrent={'progress': 0.6}, status='DOWNLOADING', now=NOW)
        self.assertEqual((20, 40), (sample['downloaded_bytes'], sample['remaining_seconds']))


    def test_cancel_preserves_unknown_barrier_and_settled_proof_releases_it(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'UNKNOWN', {}, now=NOW)
        self.auth.cancel('A', a, reason='explicit-exclusion')
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', self.auth.vector(self.keys)[self.keys[0]]['publish_phase'])
        self.auth.resolve_publish('publish', 'SETTLED', dict(sender_stopped=True, remote_operation_settled=True,
                                  consumer_cannot_apply=True, evidence_ref='verified-finished-old-consumer'), now=NOW)
        self.assertEqual('SETTLED', self.auth.vector(self.keys)[self.keys[0]]['publish_phase'])

    def test_schema2_migration_and_sqlite_backup_preserve_business_state(self):
        import sqlite3
        from contextlib import closing
        old_path = Path(self.tmp.name) / 'old.sqlite3'
        old = self.r.Repository(old_path)
        task = old.submit('original', self.r.Target('电影', 'tmdb', '42'), {}, 'admin')
        keep = {'tasks','intents','outbox','audit','settings','parse_revisions','parse_samples','parse_history'}
        with old.connection(write=True) as db:
            for name in [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if row[0] not in keep]:
                db.execute('DROP TABLE "' + name + '"')
            db.execute('PRAGMA user_version=2')
        migrated = self.r.Repository(old_path)
        self.assertEqual(task, migrated.get_task(task['id']))
        backup = Path(self.tmp.name) / 'backup.sqlite3'
        with migrated.connection() as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
        self.assertEqual(task, self.r.Repository(backup).get_task(task['id']))


    def test_review_R1_settled_cancelled_target_releases_without_replacement_budget(self):
        from dataclasses import asdict
        import json
        config = self.s.ScheduleConfig(observation_enabled=False)
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE opportunities SET config=? WHERE id=?', (json.dumps(asdict(config)), 'round'))
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        first, sibling = {self.keys[0]: a[self.keys[0]]}, {self.keys[1]: a[self.keys[1]]}
        self.publish('A', first, [0], 'publish-first')
        self.publish('A', sibling, [1], 'publish-sibling')
        self.auth.record_result('publish-first', 'UNKNOWN', {'reason': 'timeout'}, now=NOW)
        self.auth.cancel('A', first, reason='cancel-first')
        self.auth.resolve_publish('publish-first', 'NOT_SENT', dict(sender_stopped=True, remote_operation_settled=True,
                                  consumer_cannot_apply=True, evidence_ref='verified-never-sent'), now=NOW)
        vector = self.auth.vector(self.keys)
        self.assertIsNone(vector[self.keys[0]]['owner_plan_id'])
        self.assertEqual(a[self.keys[0]]['generation'] + 1, vector[self.keys[0]]['generation'])
        self.assertEqual('A', vector[self.keys[1]]['owner_plan_id'])
        self.assertEqual('PUBLISHING', vector[self.keys[1]]['publish_phase'])
        self.auth.prepare('A-new', 'round', self.spec([self.keys[0]], candidate='A'), now=NOW)
        self.assertEqual('A-new', self.auth.claim('A-new', {self.keys[0]: vector[self.keys[0]]}, now=NOW)[self.keys[0]]['owner_plan_id'])
        self.assertEqual('RESOLVED_NOT_SENT', self.auth.action('publish-first')['state'])
        self.assertEqual('ACTIVE', self.auth.plan('A')['authorization'])

    def test_review_R2_pause_resume_revokes_queued_and_unqueued_old_execution(self):
        a = self.claim()
        self.auth.queue_attempt('old-add', 'A', a, 'ADD', [0, 1], {}, now=NOW)
        self.repo.set_state(self.task_id, 'PAUSED', 'admin')
        self.repo.set_state(self.task_id, 'PASSIVE', 'admin')
        self.assertEqual(3, self.repo.get_task(self.task_id)['generation'])
        for operation_id, kind in (('old-add', 'ADD'), ('fresh-key-old-plan', 'RESUME')):
            with self.subTest(operation_id=operation_id), self.assertRaises(ValueError):
                self.auth.begin_attempt(operation_id, 'A', a, kind, [0, 1], {}, now=NOW)
        self.auth.cancel('A', a, reason='explicit-reauthorization')
        self.auth.prepare('A-new', 'round', self.spec(candidate='A'), now=NOW)
        renewed = self.auth.claim('A-new', self.auth.vector(self.keys), now=NOW)
        self.assertTrue(self.auth.begin_attempt('new-add', 'A-new', renewed, 'ADD', [0, 1], {}, now=NOW)['dispatch'])

    def test_review_R2_pause_resume_keeps_issued_publish_receipts_and_barrier(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.repo.set_state(self.task_id, 'PAUSED', 'admin')
        self.repo.set_state(self.task_id, 'PASSIVE', 'admin')
        self.auth.record_result('publish', 'UNKNOWN', {'timeout': True}, now=NOW)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', self.auth.vector(self.keys)[self.keys[0]]['publish_phase'])
        self.auth.record_result('publish', 'HANDED_OFF', {'late-verified-location': True}, now=NOW)
        self.assertEqual('HANDED_OFF', self.auth.vector(self.keys)[self.keys[0]]['publish_phase'])


    def test_review_R2_schema3_migration_keeps_unknown_barrier_and_requires_reauthorization(self):
        a = self.claim()
        self.auth.set_transfer_phase('A', a, 'READY_TO_PUBLISH')
        self.publish('A', a, [0, 1])
        self.auth.record_result('publish', 'UNKNOWN', {'timeout': True}, now=NOW)
        frozen = self.auth.plan('A')['snapshot']
        with self.repo.connection(write=True) as db:
            for table in ('plans', 'plan_actions'):
                if 'task_generation' in [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]:
                    db.execute('ALTER TABLE ' + table + ' DROP COLUMN task_generation')
            for table in ('organized_assets','managed_downloads','exclusions','candidates'):
                db.execute('DROP TABLE '+table)
            for table in ('management_operations','management_previews','candidate_decisions','archive_scan_baselines','migration_history','migration_receipts','discovery_targets','discovery_records','discovery_sources','ai_usage','ai_requests','ai_runtime','delivery_bundles','local_observations','reconcile_checkpoints','archive_assets','archive_sources','archive_scan_items','archive_scans','archive_locations','archive_contents','archive_versions','archive_targets'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=3')
        migrated = self.r.Repository(self.path)
        auth = self.m.Authority(migrated)
        self.assertEqual(0, auth.plan('A').get('task_generation'), 'legacy plan authorization must be unknown, not silently current')
        self.assertEqual(0, auth.action('publish').get('task_generation'))
        self.assertEqual(frozen, auth.plan('A')['snapshot'])
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', auth.vector(self.keys)[self.keys[0]]['publish_phase'])
        auth.record_result('publish', 'HANDED_OFF', {'late': True}, now=NOW)
        self.assertEqual('HANDED_OFF', auth.vector(self.keys)[self.keys[0]]['publish_phase'])


class PlanSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load('planner')
        cls.p = load('policy')
        cls.r = load('repository')

    def setUp(self):
        self.assertTrue(hasattr(self.m, 'Planner'), 'W04 quality/file planner missing')
        self.policy = self.p.Policy({'tv': '欧美剧'}, 7)
        self.planner = self.m.Planner(self.policy)
        self.keys = [self.m.TargetUnit(self.r.Target('电视剧', 'tmdb', '42', 1), e).key for e in (7, 8)]
        self.classification = {'state': 'complete', 'policy_revision': 7, 'effective': {'category_id': 'tv'}}
        self.current = {k: {'state': 'MISSING', 'revision': 0, 'versions': []} for k in self.keys}

    def facts(self, quality, current=False):
        return self.policy.normalize({'title': f'Fictional {quality} WEB-DL -HHWEB 中文字幕',
                                     'description': '', 'labels': [], 'subtitle_description': ''}, current=current)

    def candidate(self, key='A', quality='2160p', shared=False):
        files = [{'index': i, 'path': f'E{7+i}.mkv', 'size': 100, 'role': 'video', 'targets': [k], 'requires': []} for i,k in enumerate(self.keys)]
        if shared:
            files = [{'index': 0, 'path': 'E07-E08.mkv', 'size': 200, 'role': 'video', 'targets': self.keys, 'requires': []}]
        return dict(candidate_key=key, infohash='a'*40, downloader='test', save_path='/test',
                    parse_revision='parse-1', facts={k: self.facts(quality) for k in self.keys},
                    classification=self.classification, torrent_files=files, available=True,
                    identity_ok=True, scope_ok=True, parse_status='OK', files_verified=True, configuration_verified=True)

    def test_unknown_or_provider_error_never_becomes_acquire(self):
        for state in ('UNKNOWN', 'ERROR', 'PRESENT'):
            self.current[self.keys[0]]['state'] = state
            out = self.planner.evaluate(self.candidate(), self.current, self.keys, mode='season')
            self.assertEqual([], out['plans'])
            self.assertNotEqual('ACQUIRE', out['decisions'][self.keys[0]]['action'])
        self.current[self.keys[0]]['state'] = 'MISSING'
        self.assertEqual(1, len(self.planner.evaluate(self.candidate(), self.current, self.keys, mode='season')['plans']))

    def test_pack_cannot_degrade_but_episode_mode_selects_only_valuable_files(self):
        self.current[self.keys[0]] = {'state': 'PRESENT', 'revision': 1, 'versions': [self.p.Version('high', self.facts('2160p HDR', True))]}
        self.current[self.keys[1]] = {'state': 'PRESENT', 'revision': 1, 'versions': [self.p.Version('low', self.facts('1080p', True))]}
        self.assertEqual([], self.planner.evaluate(self.candidate(), self.current, self.keys, mode='season')['plans'])
        plan = self.planner.evaluate(self.candidate(), self.current, self.keys, mode='episode')['plans'][0]
        self.assertEqual([1], plan['selected_indices'])
        self.assertEqual({self.keys[1]}, set(plan['targets']))
        self.assertEqual('QUALITY_UPGRADE', plan['targets'][self.keys[1]]['action'])

    def test_indivisible_video_excluded_target_and_incomplete_season(self):
        candidate = self.candidate(shared=True)
        self.assertEqual([], self.planner.evaluate(candidate, self.current, self.keys, excluded={self.keys[0]})['plans'])
        self.assertEqual([], self.planner.evaluate(candidate, self.current, [self.keys[0]])['plans'])
        candidate = self.candidate()
        candidate['torrent_files'] = candidate['torrent_files'][:1]
        self.assertEqual([], self.planner.evaluate(candidate, self.current, self.keys, mode='season')['plans'])
        candidate['candidate_key'] = 'Complete'
        self.assertEqual([], self.planner.evaluate(candidate, self.current, self.keys, mode='season')['plans'])

    def test_unchanged_sibling_keeps_text_subtitle_and_rejects_invalid_dependency(self):
        self.current[self.keys[0]] = {'state': 'PRESENT', 'revision': 0, 'versions': [self.p.Version('same', self.facts('2160p', True))]}
        candidate = self.candidate()
        candidate['torrent_files'].append({'index': 2, 'path': 'subtitle.ass', 'size': 10, 'role': 'subtitle', 'targets': [self.keys[1]], 'requires': []})
        plan = self.planner.evaluate(candidate, self.current, self.keys, mode='season')['plans'][0]
        self.assertEqual([0, 1, 2], plan['selected_indices'])
        self.assertEqual('UNCHANGED', plan['targets'][self.keys[0]]['action'])
        candidate['torrent_files'][-1]['requires'] = [999]
        self.assertEqual([], self.planner.evaluate(candidate, self.current, self.keys)['plans'])

    def test_same_video_sidecar_plan_tolerates_unknown_current_text_rank(self):
        key = self.keys[0]
        current = self.policy.normalize({'title': 'Fictional 2160p WEB-DL',
                                         'missing_fields': ['description', 'labels'],
                                         'technical': {'resolution': 2160, 'picture': 0, 'audio': 0}}, current=True)
        self.current[key] = {'state': 'PRESENT', 'revision': 1,
                             'versions': [self.p.Version('same-video', current)], 'sidecar_missing': True}
        candidate = self.candidate()
        candidate['same_video_verified'] = {key: True}
        candidate['torrent_files'].append({'index': 2, 'path': 'E7.zh-Hans.srt', 'size': 10,
                                           'role': 'subtitle', 'targets': [key], 'requires': []})
        result = self.planner.evaluate(candidate, self.current, [key])
        self.assertEqual([2], result['plans'][0]['selected_indices'])
        self.assertEqual('SIDECAR_SUPPLEMENT', result['plans'][0]['targets'][key]['action'])

    def test_direct_old_scope_candidate_requires_rebinding(self):
        candidate = self.candidate()
        candidate['torrent_files'].append({'index': 2, 'path': 'subtitle.ass', 'size': 10,
                                           'role': 'subtitle', 'targets': [self.keys[1]], 'requires': [3]})
        candidate['torrent_files'].append({'index': 3, 'path': 'font.ttf', 'size': 20,
                                           'role': 'attachment', 'targets': [self.keys[1]], 'requires': []})
        result = self.planner.evaluate(candidate, self.current, self.keys, mode='season')
        self.assertEqual([], result['plans'])
        self.assertEqual('ASSET_SCOPE_REBIND_REQUIRED', result['reason'])

    def test_deterministic_priority_current_multi_version_and_bounded_cover(self):
        self.current[self.keys[0]] = {'state': 'PRESENT', 'revision': 1, 'versions': [self.p.Version('low', self.facts('1080p', True)), self.p.Version('best', self.facts('2160p HDR', True))]}
        a, b = self.candidate('A', '2160p'), self.candidate('B', '2160p HDR')
        result = self.planner.select([a, b], self.current, self.keys)
        self.assertEqual('B', result[0]['candidate_key'])
        self.assertEqual(result, self.planner.select([b, a], self.current, self.keys))
        self.assertEqual({self.keys[1]}, set(result[0]['targets']))


if __name__ == '__main__':
    unittest.main()
