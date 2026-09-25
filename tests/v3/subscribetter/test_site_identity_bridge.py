"""Bridge tests use real bounded CandidateService, isolated DB and fake providers."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy
import tempfile
import time
import unittest
from test_planner import load


def body(douban='36439868', imdb='tt28014327'):
    return '<div id="kdescr">[url=https://movie.douban.com/subject/'+douban+'/]Douban[/url] [url=https://www.imdb.com/title/'+imdb+'/]IMDb[/url]</div>'

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.module=load('site_identity_bridge')
        self.c=load('candidates');r=load('repository')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=r.Repository(Path(self.tmp.name)/'test.db')
        self.rows=[dict(site=1,torrent_id='354238',title='Mayday 2026 1080p',description='own description',page_url='https://site/detail?id=354238&passkey=SECRET')]
        self.media=NS(type='电影',media_source='themoviedb',media_id='1137844',year='2026',imdb_id='tt28014327',tmdb_info={'id':1137844,'imdb_id':'tt28014327'},douban_id=None,douban_info={})
        self.bodies={'354238':body()};self.recognized={};self.calls=[]
        outer=self
        class Adapter:
            def sites(self):return [{'id':1}]
            def page_size(self,site,word):outer.calls.append(('page_size',));return None
            def search(self,site,word,page):outer.calls.append(('search',word));return outer.rows
            def site_description(self,raw,sites,*,deadline):outer.calls.append(('detail',raw['torrent_id']));return outer.bodies[raw['torrent_id']]
            def recognize(self,meta,declared,*,media_type):outer.calls.append(('recognize',meta.title,declared,media_type));return outer.recognized.get(meta.title,outer.media)
            identity=staticmethod(lambda m:(m.media_source,m.media_id))
            source_identity=staticmethod(self.c.HostCandidateAdapter.source_identity)
        self.service=self.c.CandidateService(self.repo,Adapter())
        class Meta:
            def parse(self,key,title,description):
                outer.calls.append(('parse',title,description));return NS(status='OK',meta=NS(title=title))
        self.meta=Meta();self.checks=0
        self.budget=self.c.SearchBudget(keywords=2,pages=1,concurrency=1,results=20,requests=6,interval=0)
    def check(self):self.checks+=1
    def run_bridge(self,**kwargs):
        return self.module.resolve_site_identity(self.service,self.meta,media=NS(type='电影',title='求救信号',original_title='Mayday',year='2026'),douban_id='36439868',selected_sites=[1],budget=kwargs.get('budget',self.budget),deadline=kwargs.get('deadline',time.monotonic()+10),checkpoint=kwargs.get('checkpoint',self.check))
    def test_independent_original_candidate_and_unmodified_provider(self):
        before=copy.deepcopy(vars(self.media));result=self.run_bridge()
        self.assertEqual(result['state'],'VERIFIED');self.assertIs(result['media'],self.media)
        self.assertEqual(result['evidence']['canonical'],['themoviedb','1137844'])
        self.assertEqual(result['evidence']['douban_id'],'36439868')
        self.assertEqual(result['evidence']['media_type'],'电影')
        self.assertEqual(vars(self.media),before)
        self.assertIn(('recognize','Mayday 2026 1080p',('themoviedb',None),'电影'),self.calls)
        self.assertIn(('parse','Mayday 2026 1080p','own description'),self.calls)
        self.assertNotIn('SECRET',str(result['evidence']));self.assertGreater(self.checks,4)
    def test_conflicts_and_wrong_type_never_verified(self):
        for changes in ({'tmdb_info':{'id':999,'imdb_id':'tt28014327'}},{'year':'2025'},{'imdb_id':'tt9'},{'type':'电视剧'},{'douban_id':'999'},{'douban_id':'36439868','douban_info':{'id':'999'}},{'tmdb_info':{'external_ids':{'imdb_id':'tt8'}}}):
            with self.subTest(changes=changes):
                before=vars(self.media).copy();vars(self.media).update(changes)
                self.assertEqual(self.run_bridge()['state'],'CONFLICT');vars(self.media).clear();vars(self.media).update(before)
    def test_other_douban_is_skipped_without_recognition(self):
        self.bodies['354238']=body('99')
        self.assertEqual(self.run_bridge()['state'],'UNKNOWN')
        self.assertFalse(any(c[0]=='recognize' for c in self.calls))
    def test_all_bounded_matching_bodies_checked_for_conflict(self):
        self.rows.append(dict(self.rows[0],torrent_id='2',title='Other own title'))
        self.bodies['2']=body(imdb='tt123')
        self.recognized['Other own title']=NS(**dict(vars(self.media),media_id='2',imdb_id='tt123',tmdb_info={'id':2,'imdb_id':'tt123'}))
        self.assertEqual(self.run_bridge()['state'],'CONFLICT')
        self.assertEqual(len([c for c in self.calls if c[0]=='detail']),2)
    def test_absent_imdb_and_provider_failure_stay_unknown(self):
        self.media.imdb_id=None;self.media.tmdb_info={}
        self.assertEqual(self.run_bridge()['state'],'UNKNOWN')
        def failed(*args,**kwargs):raise OSError('PRIVATE URL should not leak')
        self.service.adapter.site_description=failed
        result=self.run_bridge()
        self.assertEqual(result['state'],'UNKNOWN');self.assertNotIn('PRIVATE',str(result))

    def test_conflicting_canonical_for_same_imdb_is_not_first_match_win(self):
        self.rows.append(dict(self.rows[0],torrent_id='2',title='Second'))
        self.bodies['2']=body()
        self.recognized['Second']=NS(**dict(vars(self.media),media_id='99',tmdb_info={'id':99,'imdb_id':'tt28014327'}))
        self.assertEqual(self.run_bridge()['state'],'CONFLICT')

    def test_budget_caps_and_checkpoint_deadline_propagate(self):
        self.rows=[dict(self.rows[0],torrent_id=str(i)) for i in range(20)]
        self.bodies={str(i):body() for i in range(20)}
        self.run_bridge()
        self.assertLessEqual(len([c for c in self.calls if c[0] in ('detail','search','page_size')]),6)
        self.assertEqual(len([c for c in self.calls if c[0]=='detail']),4)
        with self.assertRaisesRegex(ValueError,'TICK_DEADLINE'):self.run_bridge(deadline=0)
        def cancelled():raise RuntimeError('CANCELLED')
        with self.assertRaisesRegex(RuntimeError,'CANCELLED'):self.run_bridge(checkpoint=cancelled)
        tiny=self.c.SearchBudget(keywords=1,pages=1,concurrency=1,results=1,requests=2,interval=0)
        self.calls.clear();self.assertEqual(self.run_bridge(budget=tiny)['state'],'UNKNOWN');self.assertEqual(self.calls,[])

    def test_one_result_slot_falls_back_to_primary_trusted_name(self):
        def selective(site, word, page):
            self.calls.append(('search', word))
            return self.rows if word == 'Primary Movie' else []
        self.service.adapter.search = selective
        budget = self.c.SearchBudget(keywords=2, pages=1, concurrency=1,
                                     results=1, requests=4, interval=0)
        found = self.service.search([1], ['Primary Movie', 'Other Alias'], budget)
        self.assertEqual(['Other Alias', 'Primary Movie'],
                         [call[1] for call in self.calls if call[0] == 'search'])
        self.assertEqual(['site:1:354238'], [row['candidate_key'] for row in found])
        self.calls.clear()
        tight = self.c.SearchBudget(keywords=2, pages=1, concurrency=1,
                                    results=1, requests=2, interval=0)
        self.assertEqual(['site:1:354238'],
                         [row['candidate_key'] for row in self.service.search(
                             [1], ['Primary Movie', 'Other Alias'], tight)])
        self.assertEqual(['Primary Movie'], [call[1] for call in self.calls if call[0] == 'search'])

    def test_tv_title_year_match_without_cross_source_id_stays_unknown(self):
        source=NS(type='电视剧',media_source='douban',media_id='37029663',douban_id='37029663',
            title='侠女内莉',original_title='Neagley',year='2026',
            douban_info={'id':'37029663','aka':['妮格莉','内格利']})
        canonical=NS(type='电视剧',media_source='themoviedb',media_id='273207',tmdb_id='273207',
            title='侠女内莉',original_title='Neagley',year='2026',tmdb_info={'id':273207})
        outer=self
        class Adapter:
            @staticmethod
            def recognize(meta,declared,*,media_type):
                outer.calls.append(('title-recognize',meta.title,declared,media_type))
                return canonical if meta.title.startswith(('侠女内莉','Neagley')) else None
            identity=staticmethod(lambda media:(media.media_source,media.media_id))
            source_identity=staticmethod(self.c.HostCandidateAdapter.source_identity)
        class Meta:
            @staticmethod
            def parse(key,title,*args):return NS(status='OK',meta=NS(title=title),record=lambda:{})
        result=self.module.resolve_title_identity(Adapter(),Meta(),media=source,douban_id='37029663',
            media_type='电视剧',deadline=time.monotonic()+10,checkpoint=self.check)
        self.assertEqual('UNKNOWN',result['state'])
        self.assertIsNone(result['media'])
        self.assertEqual('CROSS_SOURCE_ID_REQUIRED',result['evidence']['reason'])
        self.assertEqual('douban-linked-title-year-v2',result['evidence']['rule_version'])
        self.assertTrue(any(call[1].startswith('侠女内莉') for call in self.calls if call[0]=='title-recognize'))
        self.assertTrue(any(call[1].startswith('Neagley') for call in self.calls if call[0]=='title-recognize'))

        canonical.douban_id='37029663'
        linked=self.module.resolve_title_identity(Adapter(),Meta(),media=source,douban_id='37029663',
            media_type='电视剧',deadline=time.monotonic()+10,checkpoint=self.check)
        self.assertEqual('VERIFIED',linked['state'])
        self.assertIs(canonical,linked['media'])
        self.assertEqual(['themoviedb','273207'],linked['evidence']['canonical'])

        conflict=NS(**dict(vars(canonical),media_id='999',tmdb_id='999',tmdb_info={'id':999},
                           title='内格利',original_title='Neagley'))
        Adapter.recognize=staticmethod(lambda meta,declared,*,media_type: conflict if meta.title.startswith('内格利') else canonical if meta.title.startswith(('侠女内莉','Neagley')) else None)
        self.assertEqual('CONFLICT',self.module.resolve_title_identity(Adapter(),Meta(),media=source,
            douban_id='37029663',media_type='电视剧',deadline=time.monotonic()+10,checkpoint=self.check)['state'])

if __name__=='__main__':unittest.main()
