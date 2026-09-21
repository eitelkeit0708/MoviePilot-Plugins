"""Numeric-title prefix regression; recorded native shape, no host/network."""
import unittest
import subprocess
import sys
from types import SimpleNamespace

from test_meta import load, native


TITLE = 'GATE24 The Border S01 2026 1080p NF WEB-DL H.264 AAC2.0-HHWEB'
SUBTITLE = '大机场～GATE24 ～ | 第09集 | 1080p  | 类型: 电视剧 | 导演: 常广丈太 | 主演: 趣里'


class NumericPrefixTests(unittest.TestCase):
    def setUp(self):
        self.meta = load('meta')
        self.corrector = self.meta.MetaCorrector()

    def test_full_numeric_prefix_and_only_evidenced_episode(self):
        for title, subtitle, episode in ((TITLE, None, None), (TITLE, SUBTITLE, 9),
                (TITLE+'['+SUBTITLE+']', None, 9), (TITLE.replace('S01', 'S01E24'), None, 24),
                (TITLE.replace('S01', 'S01 E24'), None, 24)):
            with self.subTest(title=title, subtitle=subtitle):
                original = native(None, begin_season=1)
                before = self.meta.snapshot(original)
                result = self.corrector.correct(original, title, subtitle)
                self.assertEqual('OK', result.status)
                self.assertEqual('GATE24 The Border', result.meta.en_name)
                if 'S01 E24' not in title:
                    self.assertEqual('2026', result.meta.year)
                self.assertEqual((1, episode, None, int(episode is not None)),
                    (result.meta.begin_season, result.meta.begin_episode, result.meta.end_episode, result.meta.total_episode))
                self.assertEqual(before, self.meta.snapshot(original))
                for key in ('resource_pix', 'video_encode', 'resource_effect', 'resource_team', 'audio_encode'):
                    self.assertEqual(before[key], getattr(result.meta, key))

    def test_numeric_token_evidence_not_the_full_name_suffix(self):
        for name in (None, 'GAT', 'GATE24', 'GATE24 The Border'):
            result = self.corrector.correct(native(name), TITLE)
            self.assertEqual('GATE24 The Border', result.meta.en_name)
            self.assertIsNone(result.meta.begin_episode)
        result = self.corrector.correct(native('GATE24 The Border', begin_episode=7), TITLE)
        self.assertEqual(7, result.meta.begin_episode)

    def test_numeric_prefix_hyphens_have_bounded_backtracking(self):
        # Run an adversarial no-boundary input outside the test process. The
        # timeout is a hang guard, not a machine-dependent speed benchmark.
        code = ('import re,sys; pattern=re.compile(sys.argv[1], re.I); '
                'assert pattern.match("GATE24 " + "A-"*30 + "X") is None; '
                'match=pattern.match("GATE24-The-Border-S01-2026-1080p"); '
                'assert match and match[1] == "GATE24-The-Border"')
        subprocess.run([sys.executable, '-B', '-c', code, self.meta.NUMERIC_HEAD.pattern],
            timeout=3, check=True, capture_output=True, text=True)

    def test_numeric_prefix_stops_before_existing_technical_tail(self):
        for marker in ('WEB-DL', 'REMUX', 'BluRay', 'mkv', 'mp4', 'flac', 'mka', 'aac', 'dts'):
            for name in ('GATE24', 'GATE24 The Border'):
                with self.subTest(marker=marker, name=name):
                    result = self.corrector.correct(native(None), name+' '+marker+' 2026 1080p')
                    self.assertEqual(name, result.meta.en_name)
                    self.assertIsNone(result.meta.begin_episode)

    def test_no_technical_boundary_no_prefix_invention(self):
        for title in ('GATE24 Other Story', '[GATE24] Other Story.2024',
                      'Unrelated GATE24.2024', 'GATE24 The Border 2 S01 1080p'):
            result = self.corrector.correct(native(None), title)
            self.assertEqual('DEFER', result.status)
            self.assertIsNone(result.meta.en_name)
        plain = native('Border 24', begin_episode=7)
        result = self.corrector.correct(plain, 'Border 24 S01 1080p')
        self.assertEqual(('Border 24', 7), (result.meta.en_name, result.meta.begin_episode))

    def test_locks_words_and_unknown_context_still_win(self):
        result = self.corrector.correct(native('Chosen'), TITLE, locks=('name', 'episode'))
        self.assertEqual(('Chosen', 24), (result.meta.en_name, result.meta.begin_episode))
        original = native('Chosen', begin_season=1, total_season=1, apply_words=['GATE24 => Chosen'])
        self.assertEqual(self.meta.snapshot(original),
            self.meta.snapshot(self.corrector.correct(original, TITLE, SUBTITLE).meta))
        original = native(None, begin_season=1)
        result = self.corrector.correct(original, TITLE, context_known=False)
        self.assertEqual(('DEFER', ('RUST_LOCK_CONTEXT_UNKNOWN',)), (result.status, result.reasons))
        self.assertEqual(self.meta.snapshot(original), self.meta.snapshot(result.meta))

    def test_numeric_prefix_year_is_only_independent_main_technical_year(self):
        for title in (TITLE, TITLE.replace('S01 ', ''), TITLE.replace('S01 ', 'S01E24 ')):
            self.assertEqual('2026', self.corrector.correct(native(None), title).meta.year)
        for title, subtitle in ((TITLE.replace('2026', '2026-09-21'), None),
                                (TITLE.replace('2026 ', ''), '2026-09-21 第09集'),
                                (TITLE.replace('2026', '2026 Remastered'), None)):
            self.assertIsNone(self.corrector.correct(native(None), title, subtitle).meta.year)
        locked = self.corrector.correct(native(None, year='2000'), TITLE, locks=('year',))
        self.assertEqual('2000', locked.meta.year)

    def bridge(self, parser, locks=()):
        # Existing HTTPX fake transport and disposable SQLite fixture; no live owner proof.
        import test_ai
        fixture = test_ai.AITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runtime = fixture.runtime(name_recognize_bridge=True)
        runtime.owner_check = lambda *args: fixture.m.owner_receipt('offline', *args, fingerprint='a'*64)
        runtime.owner_snapshot = lambda *args: dict(fingerprint='a'*64, overlaps=[], unclassified=[])
        real = self.meta.MetaService(fixture.repo, self.corrector, parser=parser)
        service = SimpleNamespace(corrector=self.corrector,
            parse=lambda *args, **kwargs: real.parse(*args, locks=locks, **kwargs))
        return fixture, runtime, service

    def test_bridge_publishes_evidenced_numeric_repair_without_http(self):
        for title, episode in ((TITLE, None), (TITLE+'['+SUBTITLE+']', 9)):
            with self.subTest(title=title):
                fixture, runtime, service = self.bridge(lambda *args, **kwargs: native(None, begin_season=1))
                event = SimpleNamespace(event_data={'title': title})
                runtime.name_event(event, service)
                self.assertNotIn('name', event.event_data)
                runtime.drain(service)
                runtime.name_event(event, service)
                self.assertEqual(dict(title=title, name='GATE24 The Border', year='2026', season=1, episode=episode), event.event_data)
                self.assertEqual([], fixture.requests)
                self.assertEqual(0, runtime.stats()['counts'].get('api_calls', 0))

    def test_bridge_keeps_words_locks_and_publication_fences(self):
        for mode in ('words', 'name-lock', 'owner', 'stop'):
            with self.subTest(mode=mode):
                def parser(*args, **kwargs):
                    if mode == 'owner':
                        runtime.owner_snapshot = lambda *a: dict(fingerprint='a'*64, overlaps=['other'], unclassified=[])
                    if mode == 'stop':
                        runtime.close()
                    return native('Chosen' if mode in ('words', 'name-lock') else None,
                        begin_season=1, apply_words=['chosen'] if mode == 'words' else [])
                fixture, runtime, service = self.bridge(parser, ('name',) if mode == 'name-lock' else ())
                event = SimpleNamespace(event_data={'title': TITLE})
                runtime.name_event(event, service)
                runtime.drain(service)
                runtime.name_event(event, service)
                self.assertNotIn('name', event.event_data)
                self.assertEqual([], fixture.requests)
                self.assertFalse(any(payload for _, payload in runtime.bridge_cache.values()))


if __name__ == '__main__':
    unittest.main()
