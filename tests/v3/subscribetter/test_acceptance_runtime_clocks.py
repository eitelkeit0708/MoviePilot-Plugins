"""T070/T175 local runtime evidence, with actual persisted ingest/quality clocks."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_discovery as discovery_fixture
import test_planner as planner
import test_runtime as runtime_fixture
from test_meta import native


class RuntimeClockTests(unittest.TestCase):
    def setUp(self):
        self.f = runtime_fixture.CommonAdmissionTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        self.host = discovery_fixture.Host()
        self.native_lists = []
        self.host.list = lambda: self.native_lists.append('list') or list(self.host.rows.values())
        owner = planner.load('ownership').Ownership(f.repo, self.host)
        f.plugin.ownership = owner
        f.plugin.config.auto_types = ['电影', '电视剧']
        f.plugin._auto_scope = lambda row: row['type'] in f.plugin.config.auto_types
        f.plugin.config.lifecycle.movie_days = 1
        f.plugin.config.lifecycle.expiry_mode = 'LAST_INGEST'
        f.plugin.config.schedule.update(cooldown_enabled=True, cooldown_seconds=100,
                                        supersession_limit=3, supersession_seconds=3600)
        f.runtime = f.m.Runtime(f.plugin, provider=f.provider, clients=lambda _: object())
        self.row = f.runtime.submit('manual', f.target, {'name': 'Fiction'}, 'admin')
        self.key = f.p.TargetUnit(f.target).key
        with f.repo.connection() as db:
            self.opportunity = db.execute('SELECT id FROM opportunities').fetchone()[0]
        self.revisions = dict(policy_revision=f.runtime.policy.semantic_hash,
                              parse_revision=f.runtime.meta.corrector.revision)
        self.prepare('A', 1)
        vector = f.plugin.authority.claim('A', f.plugin.authority.vector([self.key]),
                                         now=planner.NOW, immediate=True)
        self.publish('A', vector, 'ingest-publication', planner.NOW)
        f.plugin.authority.record_result('ingest-publication', 'HANDED_OFF', {}, now=planner.NOW)
        proof = dict(receipt_id='real-local-ingest', version_id='version-1', association_verified=True,
                     all_assets_verified=True, improvement_verified=True, consumer_settled=True,
                     evidence_ref='fictional-final-association')
        self.ingested_at = planner.NOW + timedelta(seconds=10)
        self.assertTrue(f.plugin.authority.confirm_ingest('ingest-publication', {self.key: proof},
                                                        now=self.ingested_at)['accepted'])
        # Actual bootstrap reopens the same completed continuous opportunity.
        f.runtime.bootstrap(time.monotonic() + 2)
        self.assertEqual('ACTIVE', f.plugin.scheduler.opportunity(self.opportunity)['state'])
        self.assertEqual(1, self.host.creates)
        target = f.plugin.scheduler.target(self.key)
        self.assertEqual(f.s.stamp(self.ingested_at), target['last_ingest_confirmed_at'])
        self.assertEqual(f.s.stamp(self.ingested_at + timedelta(seconds=100)), target['cooldown_until'])
        lifecycle = f.plugin.scheduler.lifecycle(self.row['id'])
        self.assertEqual(f.s.stamp(self.ingested_at), lifecycle['last_ingest_at'])
        self.assertEqual(f.s.stamp(self.ingested_at + timedelta(days=1)), lifecycle['expires_at'])
        f.plugin.scheduler.observe(self.opportunity, self.key, 'candidate-B', [2], eligible=True,
                                   now=planner.NOW + timedelta(seconds=111))
        observation = f.plugin.scheduler.observe(self.opportunity, self.key, 'candidate-C', [3], eligible=True,
                                                 now=planner.NOW + timedelta(seconds=120))
        self.assertEqual(f.s.stamp(planner.NOW + timedelta(seconds=111)), observation['first_seen'])
        self.assertEqual(f.s.stamp(planner.NOW + timedelta(seconds=120)), observation['last_better'])
        self.assertEqual(f.s.stamp(planner.NOW + timedelta(seconds=141)), observation['deadline'])

    def prepare(self, plan_id, quality):
        f = self.f
        revision = f.plugin.authority.vector([self.key])[self.key]['current_revision']
        snapshot = dict(candidate_key='candidate-' + plan_id, infohash=plan_id.lower() * 40,
                        downloader='isolated', save_path='/test', **self.revisions,
                        current={self.key: dict(revision=revision, state='PRESENT' if revision else 'MISSING')},
                        targets={self.key: dict(action='QUALITY_UPGRADE' if revision else 'ACQUIRE',
                                               reason='BETTER' if revision else 'MISSING', quality=[quality],
                                               evidence_keys=['physical-' + plan_id], evidence_source='explicit')},
                        torrent_files=[dict(index=0, path='movie.mkv', size=100, role='video',
                                            targets=[self.key], requires=[])], selected_indices=[0],
                        verified=dict(identity=True, scope=True, admission=True, files=True, configuration=True))
        f.plugin.authority.prepare(plan_id, self.opportunity, snapshot, now=planner.NOW)

    def publish(self, plan_id, vector, action_id, at):
        authority = self.f.plugin.authority
        authority.set_transfer_phase(plan_id, vector, 'READY_TO_PUBLISH')
        authority.begin_publish(action_id, plan_id, vector, [0], now=at,
            validation=dict(**self.revisions,
                current_revisions={self.key: vector[self.key]['current_revision']},
                checks={self.key: dict(identity=True, admission=True, scope=True, not_excluded=True,
                                      current_allows=True, assets_complete=True, remote_verified=True)}))

    def business_rows(self):
        tables = ('tasks', 'opportunities', 'opportunity_targets', 'observations', 'target_units',
                  'task_lifecycle', 'plans', 'plan_targets', 'plan_actions', 'action_receipts',
                  'ingest_receipts', 'evidence_consumption')
        with self.f.repo.connection() as db:
            rows = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in tables}
        for table, values in rows.items():
            self.assertTrue(values, table + ' must contain actual state before invariance is claimed')
        return rows

    def test_directed_discovery_rejudgment_keeps_ingest_observation_budgets_and_unknown(self):
        f = self.f
        self.prepare('B', 2)
        vector = f.plugin.authority.claim('B', f.plugin.authority.vector([self.key]),
                                         now=planner.NOW + timedelta(seconds=121), immediate=True)
        self.prepare('C', 3)
        vector = f.plugin.authority.supersede('C', vector, reason='QUALITY_UPGRADE', safe_isolation=True,
                                            now=planner.NOW + timedelta(seconds=122), immediate=True)
        self.publish('C', vector, 'unknown-publication', planner.NOW + timedelta(seconds=123))
        f.plugin.authority.record_result('unknown-publication', 'UNKNOWN', {'reason': 'timeout'}, now=planner.NOW)
        f.plugin.scheduler.record_failure(self.opportunity, 'confirmed-failure', 'CONFIRMED_BAD_RESOURCE',
                                          now=planner.NOW + timedelta(seconds=123))
        opportunity = f.plugin.scheduler.opportunity(self.opportunity)
        self.assertEqual((1, 1), (opportunity['failures'], opportunity['supersessions']))

        d = planner.load('discovery')
        meta = planner.load('meta')
        service_meta = meta.MetaService(f.repo, meta.MetaCorrector(),
            lambda title, **_: native(title, type='电影', begin_episode=None))
        media = SimpleNamespace(type=SimpleNamespace(value='电影'), identity=('themoviedb', '42'),
                                title='Fiction', year='2026', category='movie', tmdb_info={}, douban_id='35322132')
        recognizer = discovery_fixture.Recognizer(media)
        config = d.DiscoveryConfig.model_validate(dict(enabled=True, rsshub_base_url='http://fixture.invalid',
            request_budget=discovery_fixture.ONE_BUDGET,
            sources=[dict(id='weekly', kind='rsshub', route_key='movie_weekly_best',
                          destination_templates={'movie': 'movie-destination'},
                          destination_category_bindings={'fixture.movie': 'movie'})]))
        clock = discovery_fixture.Clock()
        async def fetch(*_):
            return d.FetchResult(discovery_fixture.SYNTHETIC_RSS)
        service = d.DiscoveryService(f.repo, f.runtime, service_meta, recognizer, config,
            fetch=fetch, clock=clock, inventory=lambda _: dict(state='MISSING', evidence_ref='fixture:inventory'),
            authorized=lambda *_: True, excluded=lambda _: False, current=lambda: True,
            owner_check=discovery_fixture.owner_receipt,
            owner_snapshot=lambda *_: discovery_fixture.OWNER_SNAPSHOT, instance_id='SubscriBetter')
        original_business = self.business_rows()
        asyncio.run(service.run())
        records = service.records()
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual('ALREADY_MANAGED', record['state'])
        self.assertEqual('ALREADY_MANAGED', record['targets'][0]['state'])
        self.assertEqual(self.row['id'], record['targets'][0]['task_id'])
        self.assertEqual(original_business, self.business_rows())
        sample = 'discovery:' + record['raw_revision']
        self.assertEqual(1, len(f.repo.parse_history(sample)))
        before = self.business_rows()
        vector = f.plugin.authority.vector([self.key])
        calls = len(recognizer.calls)
        service.meta_service = meta.MetaService(f.repo, meta.MetaCorrector(['Protected 86']), service_meta.parser)
        self.assertEqual(1, service.reprocess([record['id']]))
        self.assertEqual('REPROCESS_REQUESTED', service.records()[0]['reason'])
        clock.advance(10)
        asyncio.run(service.run())
        after = service.records()[0]
        self.assertEqual((record['id'], 'ALREADY_MANAGED'), (after['id'], after['state']))
        self.assertEqual(calls + 1, len(recognizer.calls))
        self.assertEqual(2, len(f.repo.parse_history(sample)))
        self.assertEqual(before, self.business_rows())
        self.assertEqual(vector, f.plugin.authority.vector([self.key]))
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN', f.plugin.authority.action('unknown-publication')['state'])
        self.assertEqual(1, self.host.creates)

    def test_actual_reprofile_and_directory_rename_scan_preserve_populated_clocks(self):
        f = self.f
        candidates = planner.load('candidates').CandidateService(f.repo, None)
        observed = candidates.observe(dict(site=1, torrent_id='clock', title='Fiction 2160p',
                                           description='', labels=[]))
        before = self.business_rows()
        self.assertEqual('CANDIDATES', f.runtime.reprofile()['phase'])
        result = f.runtime.reprofile()
        self.assertEqual(('COMPLETE', 1), (result['phase'], result['checked']))
        profile = f.repo.setting('runtime-policy:candidates:' + observed['candidate_key'])
        self.assertTrue(profile['revision'])
        self.assertTrue(profile['admission_only'])
        self.assertEqual(before, self.business_rows())

        local = Path(f.tmp.name) / 'organized'
        local.mkdir()
        original = local / 'movie.mkv'
        original.write_bytes(b'fictional media bytes')
        delivery = planner.load('delivery')
        rule = dict(id='clock-scan', enabled=True, local_root=str(local), read_roots=[str(local)],
                    cloud_scope_id='fixture', staging_root='/fixture/staging', incoming_root='/fixture/incoming',
                    consumer_roots=['/fixture/incoming'], excluded_local_roots=[], stable_seconds=0,
                    scan_interval=60, rapid_misses=2, rapid_interval=60, fallback=False,
                    fallback_gb=None, unlimited=False)
        scanner = delivery.LocalReconciler(f.repo, [rule])
        first = scanner.scan('clock-scan', now=planner.NOW)
        self.assertEqual('COMPLETE', first['state'])
        renamed = local / 'renamed.mkv'
        original.rename(renamed)
        second = scanner.scan('clock-scan', force=True, now=planner.NOW + timedelta(seconds=30))
        self.assertEqual('COMPLETE', second['state'])
        self.assertNotEqual(first['epoch'], second['epoch'])
        with f.repo.connection() as db:
            paths = {row['path']: json.loads(row['data'])['state']
                     for row in db.execute('SELECT * FROM local_observations')}
        self.assertEqual({str(original): 'MISSING', str(renamed): 'PRESENT'}, paths)
        self.assertEqual(before, self.business_rows())

    def test_creation_off_keeps_existing_expiry_in_actual_runtime_tick(self):
        f = self.f
        f.plugin.config.auto_types = []
        restarted = f.m.Runtime(f.plugin, provider=f.provider, clients=lambda _: object())
        self.assertEqual([], restarted.config.auto_types)
        self.assertTrue(restarted.config.enabled)
        self.host.rows[999] = dict(self.host.rows[self.row['native_id']], id=999, media_id='99')
        before_lists = list(self.native_lists)
        restarted.bootstrap(time.monotonic() + 2)
        self.assertEqual(before_lists, self.native_lists, 'creation-off must not enumerate native candidates')
        anchored = f.plugin.scheduler.lifecycle(self.row['id'])
        expiry = self.ingested_at + timedelta(days=1)
        for at, state in ((expiry - timedelta(seconds=1), 'ACTIVE'),
                          (expiry + timedelta(seconds=1), 'EXPIRED')):
            with self.subTest(state=state):
                f.repo.setting('runtime-lane', 4)
                # Control only wall-clock input. Runtime tick and Scheduler.tick run normally.
                with patch.object(f.s, 'instant', side_effect=lambda value=None: value if value is not None else at):
                    result = restarted._run_tick()
                self.assertEqual('REPROFILE', result['state'])
                lifecycle = f.plugin.scheduler.lifecycle(self.row['id'])
                self.assertEqual(state, lifecycle['state'])
                for field in ('last_ingest_at', 'expires_at', 'complete_collected_at'):
                    self.assertEqual(anchored[field], lifecycle[field])
        self.assertEqual('ACTIVE', f.repo.get_task(self.row['id'])['state'])
        self.assertEqual(1, self.host.creates)
        with f.repo.connection() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM opportunities').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
