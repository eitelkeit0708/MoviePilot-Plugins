"""T021/T022/T027 pure/local boundary evidence; no host or provider calls."""
import re
from types import SimpleNamespace as NS
import unittest

import test_candidates as candidates_tests
import test_planner as planner_tests
import test_policy as policy_tests


class PolicyBoundaryTests(unittest.TestCase):
    def policy_fixture(self):
        policy_tests.PolicyTests.setUpClass()
        f = policy_tests.PolicyTests()
        f.setUp()
        return f

    def test_t022_two_quality_values_and_separate_pgs_explicit_evidence(self):
        f = self.policy_fixture()
        plain = f.facts(current=True, subtitle_formats=['ASS'], fonts=15)
        pgs = f.facts(current=True, chinese_pgs=True)
        explicit = f.facts('2160p WEB-DL -HHWEB 简繁特效PGS字幕')
        self.assertEqual((False, 'none'), (plain.special_zh_subtitles, plain.evidence))
        self.assertEqual((True, 'inferred_pgs'), (pgs.special_zh_subtitles, pgs.evidence))
        self.assertEqual((True, 'explicit'), (explicit.special_zh_subtitles, explicit.evidence))
        self.assertEqual({False, True}, {x.special_zh_subtitles for x in (plain, pgs, explicit)})
        self.assertTrue(all(type(x.special_zh_subtitles) is bool for x in (plain, pgs, explicit)))
        self.assertEqual(f.p.rank(pgs, '欧美剧'), f.p.rank(explicit, '欧美剧'))
        ordinary = f.facts(current=True)
        self.assertEqual(f.p.rank(ordinary, '欧美剧'), f.p.rank(plain, '欧美剧'))
        self.assertEqual('QUALITY_UPGRADE', f.compare(explicit, plain).reason)
        self.assertEqual('EVIDENCE_UPGRADE', f.compare(explicit, pgs).reason)
        self.assertTrue(all(item['dimension'] == 'equal' for item in f.compare(explicit, pgs).comparisons))

    def test_t027_present_old_missing_group_is_not_missing_or_fabricated(self):
        planner_tests.PlanSelectionTests.setUpClass()
        f = planner_tests.PlanSelectionTests()
        f.setUp()
        for category, old_title, new_title, status, action, reason in (
            ('欧美剧', '1080p WEB-DL 中文字幕', '2160p WEB-DL -HHWEB 中文字幕', 'ALLOW', 'QUALITY_UPGRADE', 'QUALITY_UPGRADE'),
            ('欧美剧', '2160p WEB-DL 中文字幕', '2160p WEB-DL -HHWEB 中文字幕', 'ALLOW', 'UNCHANGED', 'EQUIVALENT'),
            ('日番', '1080p WEB-DL 中文字幕', '1080p -VCB-Studio 中文字幕', 'DEFER', 'NONE', 'CURRENT_EVIDENCE_MISSING:anime'),
        ):
            with self.subTest(category=category, old=old_title):
                policy = f.p.Policy({'tv': category}, 7)
                old = policy.normalize({'title': old_title, 'description': '', 'labels': []}, current=True)
                new = policy.normalize({'title': new_title, 'description': '', 'labels': []})
                self.assertIsNone(old.group)
                self.assertIsNone(old.official)
                self.assertIsNone(old.vcb)
                self.assertIn(old.resolution, (1080, 2160))
                self.assertIsInstance(old.audio, int)
                current = {key: dict(state='PRESENT', revision=9, versions=[f.p.Version('existing', old)]) for key in f.keys}
                candidate = f.candidate()
                candidate['facts'] = {key: new for key in f.keys}
                result = f.m.Planner(policy).evaluate(candidate, current, f.keys)
                for decision in result['decisions'].values():
                    self.assertEqual((status, action, reason), (decision['status'], decision['action'], decision['reason']))
                    self.assertNotEqual('ACQUIRE', decision['action'])
                self.assertTrue(all(v['state'] == 'PRESENT' and v['revision'] == 9 and v['versions'][0].facts is old for v in current.values()))
                if action != 'QUALITY_UPGRADE':
                    self.assertEqual([], result['plans'])
                else:
                    self.assertEqual(1, len(result['plans']))

    def test_t021_candidate_season_scope_and_unbound_route_have_no_unconditional_plan(self):
        fixture = candidates_tests.CandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        p = planner_tests.load('policy')
        planner = planner_tests.load('planner')
        from torrentool.api import Bencode
        policy = p.Policy({'tv': '欧美剧'}, 7)
        classification = {'state': 'complete', 'policy_revision': 7, 'effective': {'category_id': 'tv'}}
        class Adapter:
            season = 1
            def recognize(self, meta, declared): return NS(title='Fiction')
            def identity(self, media): return ('tmdb', '42')
            def acquire(self, raw):
                return Bencode.encode({'info': {'name': 'Pack', 'piece length': 16384, 'pieces': b'x'*20,
                    'files': [{'path': [f'Fiction.S{self.season:02}E01.2160p.DV.WEB-DL-HHWEB.mkv'], 'length': 100}]}})
            def classify(self, media): return classification
        class Meta:
            corrector = NS(revision='fixture-parser')
            def parse(self, key, title, *args, **kwargs):
                match = re.search(r'S(\d+)E(\d+)', title)
                season, episode = (int(x) for x in match.groups()) if match else (1, 1)
                return NS(status='OK', meta=NS(begin_season=season, end_season=None, begin_episode=episode, end_episode=None), record=lambda: {'status': 'OK'})
        adapter = Adapter()
        service = fixture.m.CandidateService(fixture.repo, adapter)
        row = service.observe(dict(site=1, torrent_id='scope', title='Fiction 2160p DV WEB-DL-HHWEB 中文字幕', description='', labels=[]))
        target = fixture.r.Target('电视剧', 'tmdb', '42', 1)
        key = planner.TargetUnit(target, 1).key
        old = policy.normalize({'title': 'Fiction 1080p WEB-DL 中文字幕', 'description': '', 'labels': []}, current=True)
        current = {key: dict(state='PRESENT', revision=4, versions=[p.Version('old', old)])}
        pipeline = fixture.m.CandidatePipeline(service, Meta(), policy, lambda keys: current, lambda name: object())
        def evaluate(): return pipeline.evaluate(row['candidate_key'], target, [key], downloader='test', save_path='/test')
        control = evaluate()
        self.assertEqual('QUALITY_UPGRADE', control['plans'][0]['targets'][key]['action'])
        adapter.season = 2
        cross_season = evaluate()
        self.assertEqual([], cross_season['plans'])
        self.assertEqual('CURRENT_OR_CANDIDATE_UNKNOWN', cross_season['decisions'][key]['reason'])
        adapter.season = 1
        for effective in ({}, {'category_id': 'unbound'}):
            classification['effective'] = effective
            result = evaluate()
            self.assertEqual([], result['plans'])
            self.assertEqual(('ERROR', 'CATEGORY_UNBOUND'), (result['decisions'][key]['status'], result['decisions'][key]['reason']))
        self.assertEqual(1, target.season)
        self.assertEqual('PRESENT', current[key]['state'])


if __name__ == '__main__':
    unittest.main()
