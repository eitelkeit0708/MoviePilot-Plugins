"""Pure statement extraction; live fragment is sanitized OurBits 354238 body."""
import importlib.util
from hashlib import sha256
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[3] / 'plugins.v3/subscribetter/site_identity.py'
spec = importlib.util.spec_from_file_location('site_identity', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
extract = module.extract_site_identity
BODY = '''<div class="ubbcode">◎上映日期 2026-09-04(美国网络)
◎IMDb链接 [url=https://www.imdb.com/title/tt28014327/]https://www.imdb.com/title/tt28014327/[/url]
◎豆瓣链接 [url=https://movie.douban.com/subject/36439868/]https://movie.douban.com/subject/36439868/[/url]
◎片长 110分钟</div>'''

def page(body=BODY):
    return '<html><body><div id="kdescr">'+body+'</div></body></html>'

class SiteIdentityTests(unittest.TestCase):
    def test_actual_body_statement_is_not_verified(self):
        result=extract(page(),media_type='电影')
        self.assertEqual(result['state'],'DECLARED')
        self.assertEqual(result['provider_ids'],{'douban':'36439868','imdb':'tt28014327'})
        self.assertEqual(result['media_type'],'电影')
        self.assertEqual(result['locator'],'#kdescr')
        self.assertEqual(result['body_sha256'],sha256(BODY.encode('utf8')).hexdigest())
        self.assertNotIn('html',result)

    def test_anchor_and_duplicate_same_id(self):
        body=BODY+'<a href="https://www.imdb.com/title/tt28014327/">IMDb</a>'
        self.assertEqual(extract(page(body),media_type='电视剧')['state'],'DECLARED')
        anchors='<a href="https://movie.douban.com/subject/36439868/">豆瓣</a><a href="https://imdb.com/title/tt28014327/">IMDb</a>'
        self.assertEqual(extract(page(anchors),media_type='电影')['state'],'DECLARED')

    def test_wrong_host_path_and_transport(self):
        for bad in ('https://imdb.com.evil/title/tt28014327/','https://evil@imdb.com/title/tt28014327/','http://imdb.com/title/tt28014327/','https://imdb.com:444/title/tt28014327/','https://imdb.com/name/tt28014327/','https://imdb.com/title/tt0/'):
            with self.subTest(bad=bad):
                self.assertEqual(extract(page(BODY.replace('https://www.imdb.com/title/tt28014327/',bad)),media_type='电影')['state'],'UNKNOWN')

    def test_conflicting_body_ids(self):
        for added in ('<a href="https://imdb.com/title/tt123/">other</a>','[url=https://movie.douban.com/subject/42/]other[/url]'):
            self.assertEqual(extract(page(BODY+added),media_type='电影')['state'],'CONFLICT')

    def test_navigation_recommendations_and_scripts_are_not_evidence(self):
        self.assertEqual(extract('<nav>'+BODY+'</nav>'+page('empty')+'<aside>'+BODY+'</aside>',media_type='电影')['state'],'UNKNOWN')
        for tag in ('script','style','nav','aside'):
            self.assertEqual(extract(page('<'+tag+'>'+BODY+'</'+tag+'>'),media_type='电影')['state'],'UNKNOWN')
        self.assertEqual(extract(page()+ '<aside><a href="https://imdb.com/title/tt123/">Recommendation</a></aside>',media_type='电影')['state'],'DECLARED')

    def test_bbcode_label_conflict_and_leading_zero_imdb(self):
        conflict=BODY.replace(']https://www.imdb.com/title/tt28014327/', ']https://www.imdb.com/title/tt0111161/')
        self.assertEqual(extract(page(conflict),media_type='电影')['state'],'CONFLICT')
        leading=BODY.replace('tt28014327','tt0111161')
        self.assertEqual(extract(page(leading),media_type='电影')['provider_ids']['imdb'],'tt0111161')

    def test_missing_multiple_or_unclosed_body_and_untyped_input(self):
        self.assertEqual(extract(BODY,media_type='电影')['state'],'UNSUPPORTED')
        self.assertEqual(extract(page()+page(),media_type='电影')['state'],'CONFLICT')
        self.assertEqual(extract('<div id="kdescr">'+BODY,media_type='电影')['state'],'UNKNOWN')
        self.assertEqual(extract(page(),media_type=None)['state'],'UNKNOWN')

    def test_hidden_subtrees_and_hidden_container_do_not_declare_ids(self):
        for opening, closing in (('<div hidden>', '</div>'),
                                 ('<span style="display: none">', '</span>'),
                                 ('<div style="visibility:hidden">', '</div>'),
                                 ('<template>', '</template>')):
            with self.subTest(opening=opening):
                self.assertEqual(extract(page(opening+BODY+closing),media_type='电影')['state'],'UNKNOWN')
                self.assertEqual(extract(page(opening+'hidden'+closing+BODY),media_type='电影')['state'],'DECLARED')
        self.assertEqual(extract('<aside>'+page()+'</aside>',media_type='电影')['state'],'UNKNOWN')
        self.assertEqual(extract(page().replace('id="kdescr"','id="kdescr" hidden'),media_type='电影')['state'],'UNKNOWN')

    def test_duplicate_attributes_and_overlong_ids_are_unknown(self):
        for html in (page('<div style="display:none" style="">'+BODY+'</div>'),
                     page(BODY).replace('id="kdescr"', 'id="other" id="kdescr"'),
                     page(BODY+'<a href="https://imdb.com/title/tt123/" href="https://imdb.com/title/tt28014327/">other</a>')):
            with self.subTest(html=html[:80]):
                self.assertEqual(extract(html,media_type='电影')['state'],'UNKNOWN')
        for source, path in (('imdb','title/tt'+'1'*5000), ('movie.douban','subject/'+'1'*5000)):
            long_url=f'https://{source}.com/{path}/'
            with self.subTest(source=source):
                other = 'https://movie.douban.com/subject/42/' if source == 'imdb' else 'https://imdb.com/title/tt123/'
                self.assertEqual(extract(page('<a href="'+other+'">other</a><a href="'+long_url+'">long</a>'),media_type='电影')['state'],'UNKNOWN')

if __name__=='__main__':unittest.main()
