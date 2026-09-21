"""T070 remaining local zero/fixed-origin assertions; no live-service evidence."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_acceptance_runtime_clocks as clocks
import test_discovery as discovery_fixture
import test_planner as planner
import test_runtime as runtime_fixture


class ExpiryContractTests(unittest.TestCase):
    prepare = clocks.RuntimeClockTests.prepare
    publish = clocks.RuntimeClockTests.publish
    business_rows = clocks.RuntimeClockTests.business_rows

    def admitted(self, media_type, anchor, days):
        self.f = runtime_fixture.CommonAdmissionTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        scope = f.provider.resolve(f.target)
        season = 1 if media_type == '电视剧' else None
        f.target = f.r.Target(media_type, 'themoviedb', '42', season)
        self.key = f.p.TargetUnit(f.target, 1 if season else None).key
        scope.update(target_key=f.target.key, season=season, episodes=[1] if season else [],
                     units=[self.key], provider_rows=[dict(id=1, episode_number=1)] if season else [])
        f.provider = SimpleNamespace(resolve=lambda _: copy.deepcopy(scope))
        self.host = discovery_fixture.Host()
        self.native_lists = []
        self.host.list = lambda: self.native_lists.append('list') or list(self.host.rows.values())
        f.plugin.ownership = planner.load('ownership').Ownership(f.repo, self.host)
        f.plugin.config.auto_types = ['电影', '电视剧']
        f.plugin._auto_scope = lambda row: row['type'] in f.plugin.config.auto_types
        # Set real configuration before admission/frozen Runtime inputs exist.
        f.plugin.config.lifecycle.movie_days = days if media_type == '电影' else 2
        f.plugin.config.lifecycle.tv_days = days if media_type == '电视剧' else 2
        f.plugin.config.lifecycle.expiry_mode = anchor
        f.plugin.config.schedule.update(cooldown_enabled=True, cooldown_seconds=100)
        f.runtime = f.m.Runtime(f.plugin, provider=f.provider, clients=lambda _: object())
        self.row = f.runtime.submit('expiry-contract', f.target, {'name': 'Fiction'}, 'admin')
        with f.repo.connection() as db:
            self.opportunity = db.execute('SELECT id FROM opportunities').fetchone()[0]
        self.revisions = dict(policy_revision=f.runtime.policy.semantic_hash,
                              parse_revision=f.runtime.meta.corrector.revision)
        self.ingest('A', 1, planner.NOW + timedelta(seconds=10))
        observed = f.plugin.scheduler.observe(self.opportunity, self.key, 'next-quality', [2], eligible=True,
                                              now=planner.NOW + timedelta(seconds=11))
        self.assertIsNotNone(observed['deadline'])
        self.assertEqual(1, self.host.creates)

    def ingest(self, plan_id, quality, at):
        f = self.f
        self.prepare(plan_id, quality)
        vector = f.plugin.authority.claim(plan_id, f.plugin.authority.vector([self.key]), now=at, immediate=True)
        action_id = 'publication-' + plan_id
        self.publish(plan_id, vector, action_id, at)
        f.plugin.authority.record_result(action_id, 'HANDED_OFF', {}, now=at)
        proof = dict(receipt_id='receipt-' + plan_id, version_id='version-' + plan_id,
                     association_verified=True, all_assets_verified=True, improvement_verified=True,
                     consumer_settled=True, evidence_ref='fictional-association-' + plan_id)
        result = f.plugin.authority.confirm_ingest(action_id, {self.key: proof}, now=at)
        self.assertTrue(result['accepted'])
        self.assertFalse(result['duplicate'])
        f.runtime.bootstrap(time.monotonic() + 2)
        self.assertEqual('ACTIVE', f.plugin.scheduler.opportunity(self.opportunity)['state'])
        self.assertEqual(f.s.stamp(at), f.plugin.scheduler.target(self.key)['last_ingest_confirmed_at'])
        self.assertEqual(f.s.stamp(at + timedelta(seconds=100)), f.plugin.scheduler.target(self.key)['cooldown_until'])

    def test_zero_days_with_populated_movie_tv_fixed_and_idle_anchors_remains_unlimited(self):
        for media_type in ('电影', '电视剧'):
            for anchor in ('LAST_INGEST', 'COMPLETE_COLLECTED'):
                with self.subTest(media_type=media_type, anchor=anchor):
                    self.admitted(media_type, anchor, 0)
                    f = self.f
                    completed_at = planner.NOW + timedelta(seconds=20)
                    f.plugin.scheduler.update_completion(self.row['id'], [self.key], scope_closed=True,
                                                         collected=True, now=completed_at)
                    life = f.plugin.scheduler.lifecycle(self.row['id'])
                    field = 'last_ingest_at' if anchor == 'LAST_INGEST' else 'complete_collected_at'
                    at = planner.NOW + timedelta(seconds=10) if anchor == 'LAST_INGEST' else completed_at
                    self.assertEqual(f.s.stamp(at), life[field])
                    self.assertIsNotNone(life[field], 'zero must be tested with a populated selected anchor')
                    config = json.loads(life['config'])
                    selected = 'movie_days' if media_type == '电影' else 'tv_days'
                    other = 'tv_days' if media_type == '电影' else 'movie_days'
                    self.assertEqual((0, 2, anchor), (config[selected], config[other], config['anchor']))
                    self.assertIsNone(life['expires_at'])
                    reopened = f.s.Scheduler(f.r.Repository(f.repo.path))
                    self.assertEqual(life, reopened.lifecycle(self.row['id']))
                    later = at + timedelta(days=365)
                    self.assertEqual(1, reopened.tick(now=later)['checked'])
                    after = reopened.lifecycle(self.row['id'])
                    self.assertEqual(life, after)
                    self.assertEqual('ACTIVE', after['state'])
                    self.assertTrue(reopened.ready(self.opportunity, self.key, 'QUALITY_UPGRADE', now=later)['ready'])
                    self.assertEqual('ACTIVE', f.repo.get_task(self.row['id'])['state'])

    def test_fixed_origin_survives_new_ingest_maintenance_and_creation_off_expiry(self):
        self.admitted('电影', 'COMPLETE_COLLECTED', 1)
        f = self.f
        completed_at = planner.NOW + timedelta(seconds=30)
        schedule = f.plugin.scheduler
        schedule.update_completion(self.row['id'], [self.key], scope_closed=True, collected=False,
                                   now=completed_at - timedelta(seconds=1))
        uncollected = schedule.lifecycle(self.row['id'])
        self.assertIsNotNone(uncollected['last_ingest_at'])
        self.assertIsNone(uncollected['complete_collected_at'])
        self.assertIsNone(uncollected['expires_at'])
        schedule.update_completion(self.row['id'], [self.key], scope_closed=True, collected=True, now=completed_at)
        fixed = schedule.lifecycle(self.row['id'])
        expiry = completed_at + timedelta(days=1)
        self.assertEqual(f.s.stamp(completed_at), fixed['complete_collected_at'])
        self.assertEqual(f.s.stamp(expiry), fixed['expires_at'])
        schedule.update_completion(self.row['id'], [self.key], scope_closed=True, collected=True,
                                   now=completed_at + timedelta(hours=1))
        self.assertEqual(fixed, schedule.lifecycle(self.row['id']))
        self.ingest('B', 2, completed_at + timedelta(hours=2))
        after_ingest = schedule.lifecycle(self.row['id'])
        self.assertEqual(f.s.stamp(completed_at + timedelta(hours=2)), after_ingest['last_ingest_at'])
        for field in ('complete_collected_at', 'expires_at'):
            self.assertEqual(fixed[field], after_ingest[field])
        with f.repo.connection() as db:
            self.assertEqual(2, db.execute('SELECT COUNT(*) FROM ingest_receipts').fetchone()[0])
            self.assertEqual(2, db.execute('SELECT COUNT(*) FROM evidence_consumption').fetchone()[0])

        candidate = planner.load('candidates').CandidateService(f.repo, None).observe(
            dict(site=1, torrent_id='fixed-clock', title='Fiction 2160p', description='', labels=[]))
        before = self.business_rows()
        self.assertEqual('CANDIDATES', f.runtime.reprofile()['phase'])
        result = f.runtime.reprofile()
        self.assertEqual(('COMPLETE', 1), (result['phase'], result['checked']))
        self.assertTrue(f.repo.setting('runtime-policy:candidates:' + candidate['candidate_key'])['revision'])
        self.assertEqual(before, self.business_rows())
        local = Path(f.tmp.name) / 'organized'
        local.mkdir()
        original = local / 'movie.mkv'
        original.write_bytes(b'fictional fixed-window media')
        rule = dict(id='fixed-scan', enabled=True, local_root=str(local), read_roots=[str(local)],
                    cloud_scope_id='fixture', staging_root='/fixture/staging', incoming_root='/fixture/incoming',
                    consumer_roots=['/fixture/incoming'], excluded_local_roots=[], stable_seconds=0,
                    scan_interval=60, rapid_misses=2, rapid_interval=60, fallback=False,
                    fallback_gb=None, unlimited=False)
        scanner = planner.load('delivery').LocalReconciler(f.repo, [rule])
        first = scanner.scan('fixed-scan', now=completed_at + timedelta(hours=3))
        self.assertEqual('COMPLETE', first['state'])
        renamed = local / 'renamed.mkv'
        original.rename(renamed)
        second = scanner.scan('fixed-scan', force=True, now=completed_at + timedelta(hours=4))
        self.assertEqual('COMPLETE', second['state'])
        self.assertNotEqual(first['epoch'], second['epoch'])
        with f.repo.connection() as db:
            paths = {row['path']: json.loads(row['data'])['state'] for row in db.execute('SELECT * FROM local_observations')}
        self.assertEqual({str(original): 'MISSING', str(renamed): 'PRESENT'}, paths)
        self.assertEqual(before, self.business_rows())

        f.plugin.config.auto_types = []
        restarted = f.m.Runtime(f.plugin, provider=f.provider, clients=lambda _: object())
        self.assertEqual('COMPLETE_COLLECTED', restarted.config.lifecycle.expiry_mode)
        self.assertEqual([], restarted.config.auto_types)
        self.assertTrue(restarted.config.enabled)
        self.host.rows[999] = dict(self.host.rows[self.row['native_id']], id=999, media_id='99')
        before_lists = list(self.native_lists)
        restarted.bootstrap(time.monotonic() + 2)
        self.assertEqual(before_lists, self.native_lists)
        for at, state in ((expiry - timedelta(seconds=1), 'ACTIVE'), (expiry + timedelta(seconds=1), 'EXPIRED')):
            f.repo.setting('runtime-lane', 4)
            with patch.object(f.s, 'instant', side_effect=lambda value=None: value if value is not None else at):
                self.assertEqual('REPROFILE', restarted._run_tick()['state'])
            life = schedule.lifecycle(self.row['id'])
            self.assertEqual(state, life['state'])
            for field in ('complete_collected_at', 'expires_at', 'last_ingest_at'):
                self.assertEqual(after_ingest[field], life[field])
        self.assertEqual('ACTIVE', f.repo.get_task(self.row['id'])['state'])
        self.assertEqual(1, self.host.creates)
        with f.repo.connection() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
