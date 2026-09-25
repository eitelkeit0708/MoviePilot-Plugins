"""T001: one 1080 candidate across native and independent quality paths."""
import sys
import types
import unittest
from unittest.mock import patch

from test_planner import load


class T001Integration(unittest.TestCase):
    def test_same_1080_native_reject_managed_admit(self):
        raw={'title':'Example S01E01 1080p WEB-DL -HHWEB 中文字幕'}
        calls=[]

        class Search:
            def search_site_torrents(self,**kwargs):
                calls.append(('raw',kwargs))
                return [raw]
            def process(self,**kwargs):
                if kwargs['mediainfo'] is not raw:
                    raise AssertionError('control changed candidate')
                calls.append(('native-filter',None))
                return []

        fake=types.ModuleType('app.chain.search')
        fake.SearchChain=Search
        with patch.dict(sys.modules,{'app.chain.search':fake}):
            self.assertEqual([raw],load('candidates').HostCandidateAdapter().search({'id':1},'Example',0))
            self.assertEqual(1,len(calls))
            self.assertEqual([],Search().process(mediainfo=raw))
        self.assertEqual(('native-filter',None),calls[-1])
        self.assertIsNone(calls[0][1]['mtype'])

        policy=load('policy').Policy({'stable-id':'欧美剧'},7)
        classification={'state':'complete','policy_revision':7,'effective':{'category_id':'stable-id'}}
        self.assertEqual('ALLOW',policy.admit(policy.normalize(raw),classification,
                                              identity_ok=True,scope_ok=True).status)
