"""W05 raw-source and physical asset checks; no live provider is contacted."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import types
import unittest
from test_planner import load, PLUGIN


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PLUGIN / 'candidates.py').exists(), 'W05 candidates missing')
        self.m = load('candidates')
        self.r = load('repository')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.r.Repository(Path(self.tmp.name) / 'state.db')

    def test_identity_never_uses_title_size_or_target_injection(self):
        key = self.m.candidate_key
        a = dict(site=1, title='Same', description='Same', size=12, page_url='https://site/details.php?id=42&passkey=SECRET')
        b = dict(a, page_url='https://site/details.php?id=43&passkey=SECRET')
        self.assertNotEqual(key(a), key(b))
        self.assertNotIn('SECRET', key(a))
        with self.assertRaises(ValueError):
            key(dict(site=1, title='Same', size=12))
        self.assertFalse(self.m.identity_matches(('tmdb', '42'), ('tmdb', '43')))
        self.assertIsNone(self.m.identity_matches(('tmdb', '42'), None))

    def test_no_site_zero_requests_and_continue_unrelated_keyword(self):
        calls = []
        class Adapter:
            def sites(self):
                return [dict(id=1), dict(id=2)]
            def page_size(self, site, word):
                return None
            def search(self, site, word, page):
                calls.append((site['id'], word, page))
                return [dict(site=site['id'], torrent_id=word, title=word, description='')]
        service = self.m.CandidateService(self.repo, Adapter())
        budget = self.m.SearchBudget(keywords=2, pages=2, concurrency=1, results=20, requests=4, interval=0)
        self.assertEqual([], service.search([], ['unrelated', 'wanted'], budget))
        self.assertEqual([], calls)
        rows = service.search([1], ['unrelated', 'wanted'], budget)
        self.assertEqual(['unrelated', 'wanted'], [r['title'] for r in rows])
        self.assertEqual([(1, 'unrelated', 0), (1, 'wanted', 0)], calls)
        self.assertEqual(2, len(service.records()))

    def test_rss_incomplete_and_secret_redaction(self):
        service = self.m.CandidateService(self.repo, None)
        row = service.observe(dict(site=1, torrent_id='42', title='a', enclosure='https://s/passkey=SECRET', site_cookie='SECRET'), source='rss')
        self.assertIn('description', row['missing_fields'])
        self.assertNotIn('SECRET', str(service.records()))
        self.assertEqual('DEFER', row['status'])

    def test_full_table_retains_indices_but_only_video_and_text_subtitles_are_managed(self):
        target = self.r.Target('电影', 'tmdb', '42')
        table = [('Pack/Movie.ass', 7), ('Pack/font.ttf', 11), ('Pack/Movie.en.srt', 8),
                 ('Pack/Movie.zh.idx', 9), ('Pack/Movie.zh-Hans.srt', 10),
                 ('Pack/Movie.zh-Hant.srt', 10), ('Pack/Movie.mkv', 100),
                 ('Pack/orphan.sub', 10), ('Pack/LICENSE.txt', 12), ('Pack/poster.jpg', 13)]
        files = self.m.bind_files(table, target, dependencies={0: [1], 1: [8]})
        self.assertEqual(list(range(10)), [item['index'] for item in files])
        self.assertEqual([0, 2, 4, 5, 6], [item['index'] for item in files if item['targets']])
        self.assertTrue(all(files[i]['role'] == 'other' and files[i]['targets'] == [] and files[i]['requires'] == []
                            for i in (1, 3, 7, 8, 9)))
        self.assertTrue(all(files[i]['targets'] == files[6]['targets'] for i in (0, 2, 4, 5)))
        self.assertEqual([], files[0]['requires'])

        for dependencies in ({1: [1]}, {0: [99]}, {'0': [1]}, {0: '1'}, [], '', 0, False):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                self.m.bind_files(table, target, dependencies=dependencies)
        for suffix in ('.ssa', '.vtt', '.sup'):
            with self.subTest(suffix=suffix):
                subtitle = self.m.bind_files([('Pack/Movie.mkv', 100), ('Pack/Movie' + suffix, 1)], target)[1]
                self.assertEqual(('subtitle', [self.m.TargetUnit(target).key], []),
                                 (subtitle['role'], subtitle['targets'], subtitle['requires']))

    def test_unsafe_paths_and_ambiguous_subtitle_refused(self):
        target = self.r.Target('电视剧', 'tmdb', '42', 1)
        for path in ('../x.mkv', '/x.mkv', 'C:/x.mkv', 'x\\y.mkv', 'x//y.mkv'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.m.bind_files([(path, 1)], target)
        with self.assertRaises(ValueError):
            self.m.bind_files([('Show.S01E01.mkv', 1), ('../ignored.ttf', 1)], target)
        for table in ([('Show.S01E01.mkv', 1), ('ignored.ttf', -1)],
                      [('Show.S01E01.mkv', 1), ('ignored.ttf', 1), ('ignored.ttf', 2)]):
            with self.subTest(table=table), self.assertRaises(ValueError):
                self.m.bind_files(table, target)
        with self.assertRaises(ValueError):
            self.m.bind_files([('Show.S01E01.mkv', 1), ('Show.S01E02.mkv', 2), ('中文.srt', 3)], target)
        files = self.m.bind_files([('Show.S01E01-E02.mkv', 3)], target)
        self.assertEqual(2, len(files[0]['targets']))

    def test_real_torrent_bytes_hash_table_and_symlink_rejection(self):
        from torrentool.api import Bencode
        from hashlib import sha1
        info={'name':'Pack','piece length':16384,'pieces':b'x'*20,'files':[{'path':['Show.S01E01.mkv'],'length':100},{'path':['中文.srt'],'length':7}]}
        content=Bencode.encode({'info':info})
        infohash,table=self.m.torrent_table(content)
        self.assertEqual(sha1(Bencode.encode(info)).hexdigest(),infohash)
        self.assertEqual([('Pack/Show.S01E01.mkv',100),('Pack/中文.srt',7)],table)
        info['files'][0].update({'attr':'l','symlink path':['outside']})
        with self.assertRaises(ValueError):
            self.m.torrent_table(Bencode.encode({'info':info}))

    def test_native_global_filter_is_not_the_candidate_entry(self):
        self.assertTrue(hasattr(self.m,'HostCandidateAdapter'),'W05 host candidate adapter missing')
        import sys
        from unittest.mock import patch
        calls=[]
        class Search:
            def search_site_torrents(self,**kw):
                calls.append(kw);return ['1080-independent']
            def process(self,**kw):
                raise AssertionError('native global 1080 rule must not run')
        fake=types.ModuleType('app.chain.search');fake.SearchChain=Search
        with patch.dict(sys.modules,{'app.chain.search':fake}):
            self.assertEqual(['1080-independent'],self.m.HostCandidateAdapter().search({'id':1},'Example',0))
        self.assertEqual(None,calls[0]['mtype'])

    def test_provider_conflict_never_recognizes_using_target_id(self):
        calls=[]
        class Adapter:
            def recognize(self,meta,declared):
                calls.append(declared);return None
        service=self.m.CandidateService(self.repo,Adapter())
        row=service.observe(dict(site=1,torrent_id='a',title='Show',media_source='tmdb',media_id='43'))
        result=service.recognize(row['candidate_key'],self.r.Target('电视剧','tmdb','42',1),None)
        self.assertEqual('REJECT',result['status']);self.assertEqual([],calls)

    def test_actual_pipeline_to_policy_and_fresh_revalidation(self):
        self.assertTrue(hasattr(self.m,'CandidatePipeline'),'actual candidate pipeline missing')
        from torrentool.api import Bencode
        p=load('policy');planner=load('planner')
        policy=p.Policy({'tv':'欧美剧'},7)
        content=Bencode.encode({'info':{'name':'Pack','piece length':16384,'pieces':b'x'*20,'files':[{'path':['Show.S01E01.1080p.WEB-DL-HHWEB.mkv'],'length':100},{'path':['中文.srt'],'length':7}]}})
        classification={'state':'complete','policy_revision':7,'effective':{'category_id':'tv'}}
        class Adapter:
            def recognize(self,meta,declared):
                self.declared=declared;return types.SimpleNamespace(title='Show')
            def identity(self,media):return ('tmdb','42')
            def acquire(self,raw):return content
            def classify(self,media):return classification
        class Meta:
            corrector=types.SimpleNamespace(revision='meta-1')
            def parse(self,*args,**kwargs):
                return types.SimpleNamespace(status='OK',revision='meta-1',meta=types.SimpleNamespace(begin_season=1,end_season=None,begin_episode=1,end_episode=None),record=lambda:{'status':'OK'})
        service=self.m.CandidateService(self.repo,Adapter())
        row=service.observe(dict(site=1,torrent_id='42',title='Show 1080p WEB-DL-HHWEB 中文字幕',description='',labels=[]))
        target=self.r.Target('电视剧','tmdb','42',1);key=planner.TargetUnit(target,1).key
        current={key:{'state':'MISSING','revision':0,'versions':[]}}
        pipeline=self.m.CandidatePipeline(service,Meta(),policy,lambda keys:current,lambda name:object())
        result=pipeline.evaluate(row['candidate_key'],target,[key],downloader='test',save_path='/test')
        self.assertEqual([0,1],result['plans'][0]['selected_indices'])
        self.assertIsNone(service.adapter.declared)
        pipeline.revalidate({'snapshot':result['plans'][0]})
        classification['policy_revision']=8
        with self.assertRaises(ValueError):pipeline.revalidate({'snapshot':result['plans'][0]})


if __name__ == '__main__':
    unittest.main()
