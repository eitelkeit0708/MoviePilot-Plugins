"""Explicit numeric season locks use exact scope, separate from quality ranking."""
import json
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_candidates as candidate_tests
import test_planner as planner_tests


class SeasonLockTests(unittest.TestCase):
    def setUp(self):
        planner_tests.PlanSelectionTests.setUpClass()
        self.f = planner_tests.PlanSelectionTests()
        self.f.setUp()
        self.c = candidate_tests.CandidateTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.config = planner_tests.load('configuration')

    def pipeline(self, locks):
        f = self.f
        service = self.c.m.CandidateService(self.c.repo, NS(classify=lambda _: f.classification))
        return self.c.m.CandidatePipeline(service, NS(corrector=NS(revision='parse-1')),
            f.policy, lambda _: f.current, lambda _: object(), locked=locks)

    def test_matching_and_mismatching_explicit_season_with_quality_locks(self):
        f = self.f
        for locks, expected in (({'season': 1}, 'ACQUIRE'), ({'season': 2}, 'LOCK_MISMATCH:season'),
                                ({'season': 1, 'resolution': 1080}, 'LOCK_MISMATCH:resolution'),
                                ({'season': 1, 'resolution': 2160}, 'ACQUIRE')):
            with self.subTest(locks=locks):
                typed = self.config.PolicyConfig(locks=locks)
                result = self.pipeline(typed.locks)._evaluate(f.candidate(), f.keys, 'episode')
                self.assertTrue(all(d['reason'] == expected or d['action'] == expected for d in result['decisions'].values()))
                self.assertEqual(1 if expected == 'ACQUIRE' else 0, len(result['plans']))

    def test_invalid_season_and_unknown_scope_fail_closed(self):
        f = self.f
        for bad in (True, -1, 1000, '1', 1.0, None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError): self.config.PolicyConfig(locks={'season': bad})
                result = f.planner.evaluate(f.candidate(), f.current, f.keys, locked={'season': bad})
                self.assertEqual([], result['plans'])
                self.assertEqual('INVALID_SEASON_LOCK', result['reason'])
        for scope in (['not-json'], [json.dumps(['电视剧', 'tmdb', '42', True, '', 1])]):
            result = f.planner.evaluate(f.candidate(), f.current, scope, locked={'season': 1})
            self.assertEqual([], result['plans'])
            self.assertEqual('SEASON_SCOPE_UNCONFIRMED', result['reason'])

    def test_explicit_task_override_and_revalidation_keep_the_same_precedence(self):
        f = self.f
        pipeline = self.pipeline({'season': 2, 'resolution': 1080})
        candidate = f.candidate()
        override = {'season': 1, 'resolution': 2160}
        accepted = pipeline._evaluate(candidate, f.keys, 'episode', locks=override)
        self.assertEqual(1, len(accepted['plans']))
        pipeline.rounds['A'] = dict(candidate=candidate, media=NS(), acquired=time.monotonic(), mode='episode', locks=override)
        plan = dict(snapshot=accepted['plans'][0])
        self.assertEqual(candidate, pipeline.revalidate(plan))
        pipeline.rounds['A']['locks'] = None
        with self.assertRaisesRegex(ValueError, 'CANDIDATE_POLICY_OR_CURRENT_CHANGED'):
            pipeline.revalidate(plan)
        # An explicit empty task override retains the existing meaning: no inherited locks.
        self.assertEqual(1, len(pipeline._evaluate(candidate, f.keys, 'episode', locks={})['plans']))

    def test_mixed_season_physical_file_is_never_partially_selected(self):
        f = self.f
        candidate = f.candidate(shared=True)
        candidate['torrent_files'][0]['targets'] = list(f.keys)
        outside = f.m.TargetUnit(f.r.Target('电视剧', 'tmdb', '42', 2), 7).key
        candidate['torrent_files'][0]['targets'].append(outside)
        candidate['facts'][outside] = f.facts('2160p')
        result = f.planner.evaluate(candidate, f.current, f.keys, locked={'season': 1})
        self.assertEqual([], result['plans'])
        self.assertTrue(all(d['status'] == 'ALLOW' for d in result['decisions'].values()))

    def test_native_publication_gate_reads_global_and_exact_task_override(self):
        f = self.f
        host = planner_tests.load('host_delivery_contract')
        candidate = f.candidate()
        snapshot = f.planner.evaluate(candidate, f.current, f.keys)['plans'][0]
        plan = dict(snapshot=snapshot, opportunity_id='round', task_id=7, task_generation=1)
        publication = {key: dict(raw=dict(candidate['facts'][key].raw), classification=f.classification) for key in f.keys}
        sources = NS(classify_target=lambda _: f.classification)
        archive = NS(policy=f.policy, sources=sources, current=lambda _: f.current)
        plugin = NS(repository=self.c.repo, config=NS(policy=self.config.PolicyConfig(locks={'season': 2})))
        config = dict(cloud_scopes={}, libraries={}, policy_bindings={'tv': '欧美剧'}, classification_revision=7, mappings=[], rules=[])
        with patch.object(host, 'HostArchiveSources', return_value=sources), patch.object(host, 'Archive', return_value=archive), patch.object(host, 'HostDeliveryCloud', return_value=NS()):
            worker = host.build_delivery(plugin, config)
        with self.assertRaisesRegex(ValueError, 'LOCK_MISMATCH:season'):
            worker.validate_publication(plan, publication)
        saved = dict(task_id=7, task_generation=1, locks={'season': 1, 'resolution': 2160})
        self.c.repo.setting('runtime-input:round', saved)
        self.assertEqual(publication, worker.validate_publication(plan, publication))
        saved['locks']['resolution'] = 1080
        self.c.repo.setting('runtime-input:round', saved)
        with self.assertRaisesRegex(ValueError, 'LOCK_MISMATCH:resolution'):
            worker.validate_publication(plan, publication)
        saved.update(task_id=8, locks={})
        self.c.repo.setting('runtime-input:round', saved)
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_RUNTIME_INPUT_STALE'):
            worker.validate_publication(plan, publication)

    def test_raw_reprofile_defers_unproved_season_and_rechecks_lock_changes(self):
        f = self.f
        service = self.c.m.CandidateService(self.c.repo, None)
        record = service.observe(dict(site=1, torrent_id='profile', title='Fiction 2160p WEB-DL -HHWEB 中文字幕', description='', labels=[]))
        with self.c.repo.connection(write=True) as db:
            data = json.loads(db.execute('SELECT data FROM candidates').fetchone()[0])
            data['classification'] = f.classification
            db.execute('UPDATE candidates SET data=?', (json.dumps(data),))
        runtime = object.__new__(planner_tests.load('runtime').Runtime)
        runtime.repository = self.c.repo
        runtime.policy = f.policy
        runtime.config = NS(policy=self.config.PolicyConfig(), recovery=NS(entries=10))
        runtime.check = lambda: None
        for _ in range(2): runtime.reprofile()
        setting = 'runtime-policy:candidates:' + record['candidate_key']
        self.assertEqual('ALLOW', self.c.repo.setting(setting)['status'])
        runtime.config.policy.locks = {'season': 1}
        for _ in range(2): runtime.reprofile()
        after = self.c.repo.setting(setting)
        self.assertEqual(('DEFER', 'SEASON_SCOPE_UNCONFIRMED'), (after['status'], after['reason']))

    def test_management_simulation_defers_season_without_guessing_scope(self):
        import test_management as management_tests
        management_tests.ManagementAPITests.setUpClass()
        api = management_tests.ManagementAPITests()
        api.setUp()
        self.addCleanup(api.doCleanups)
        runtime = NS(policy=self.f.policy, config=NS(policy=self.config.PolicyConfig()),
                     meta=NS(corrector=NS(revision='parse-1')), public_evidence=lambda value: value)
        api.plugin.runtime = runtime
        body = dict(category_id='tv', candidate=dict(title='Fiction.S01E01.2160p.WEB-DL-HHWEB 中文字幕'),
                    config_revision=api.plugin.configuration.view()['revision'], runtime_generation=api.plugin.generation)
        self.assertEqual(401, api.client.post('/policies/simulate', json=body).status_code)
        for locks, title, expected in (
            ({}, 'S01E01', ('ALLOW', 'MISSING')),
            ({'season': 1}, 'S01E01', ('DEFER', 'SEASON_SCOPE_UNCONFIRMED')),
            ({'season': 1}, 'S02E01', ('DEFER', 'SEASON_SCOPE_UNCONFIRMED')),
            ({'resolution': 1080}, 'S01E01', ('REJECT', 'LOCK_MISMATCH:resolution')),
            ({'season': True}, 'S01E01', ('ERROR', 'INVALID_SEASON_LOCK')),
        ):
            with self.subTest(locks=locks, title=title):
                runtime.config.policy.locks = locks
                body['candidate']['title'] = f'Fiction.{title}.2160p.WEB-DL-HHWEB 中文字幕'
                response = api.client.post('/policies/simulate', headers=api.headers, json=body)
                self.assertEqual(200, response.status_code, response.text)
                record = response.json()
                evaluation = record['evidence']['evaluation']
                self.assertEqual(expected, (evaluation['status'], evaluation['reason']))
                self.assertTrue(record['simulation'])
                self.assertEqual([], evaluation['plans'])
                self.assertEqual([], record['evidence']['scope'])
                self.assertEqual([], record['evidence']['plan_digests'])
        with api.plugin.repository.connection() as db:
            self.assertEqual(5, db.execute('SELECT count(*) FROM candidate_decisions WHERE simulation=1').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT count(*) FROM plans').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT count(*) FROM managed_downloads').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
