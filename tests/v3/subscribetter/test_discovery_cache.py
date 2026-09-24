"""Douban cache and admission checks use the existing discovery fixtures, no network."""
import json
import types
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_discovery as fixtures


class StoredRecognizer(fixtures.Recognizer):
    @staticmethod
    def dump_media(media):
        return json.loads(json.dumps(vars(media)))

    @staticmethod
    def load_media(data):
        return types.SimpleNamespace(**data)


class DoubanCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DiscoveryTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.DiscoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.clock, self.repo, self.d = self.fixture.clock, self.fixture.repo, self.fixture.d
        self.media = types.SimpleNamespace(type='电影', identity=('douban', '35322132'),
            douban_id='35322132', title='Fixture', year='2026', category='movie', tmdb_info={})
        self.recognizer = StoredRecognizer(self.media)

    def service(self):
        service = self.fixture.service(recognizer=self.recognizer)
        service.sleep = self.clock.advance
        return service

    def test_cache_survives_feeds_revisions_and_restart_but_refreshes(self):
        service = self.service()
        item = self.d.parse_rss(fixtures.SYNTHETIC_RSS, max_bytes=16384, max_items=1)[0]
        source = service.config.sources[0]
        service._observe(source, item)
        # Same subject in a new source and changed RSS revision still uses native facts.
        other = source.model_copy(update={'id': 'other'})
        service._save_source(other)
        changed = self.d.RSSItem(**{**vars(item), 'raw_revision': 'changed', 'title': 'Different display title'})
        restarted = self.service()
        restarted._save_source(other)
        restarted._observe(other, changed)
        self.assertEqual(len(self.recognizer.calls), 1)
        self.assertEqual(len(restarted.owner.calls), 1)  # Cache does not skip admission.
        self.clock.advance(86401)
        changed = self.d.RSSItem(**{**vars(changed), 'raw_revision': 'expired'})
        restarted._observe(other, changed)
        self.assertEqual(len(self.recognizer.calls), 2)
        with self.repo.connection() as db:
            row_id = db.execute('SELECT max(id) FROM discovery_records').fetchone()[0]
        restarted.reprocess([row_id])
        restarted._observe(other, changed)
        self.assertEqual(len(self.recognizer.calls), 3)

    def test_spacing_persists_and_failure_cools_only_same_subject(self):
        service = self.service()
        meta = fixtures.Meta('Fixture')
        start = self.clock()
        service._recognize_douban(meta, ('douban', '35322132'), '电影')
        self.recognizer.media = None
        with self.assertRaises(self.d.DoubanDeferred) as first:
            self.service()._recognize_douban(meta, ('douban', '2'), '电影')
        self.assertEqual(self.clock() - start, 5)
        self.assertEqual(first.exception.retry_at, self.clock() + 60)
        with self.assertRaises(self.d.DoubanDeferred):
            self.service()._recognize_douban(meta, ('douban', '3'), '电影')
        self.assertEqual(len(self.recognizer.calls), 3)
        self.assertEqual(self.clock() - start, 10)
        with self.assertRaises(self.d.DoubanDeferred):
            self.service()._recognize_douban(meta, ('douban', '2'), '电影', refresh=True)
        self.assertEqual(len(self.recognizer.calls), 3)
        # Successful cached subjects remain usable during an upstream outage.
        self.assertIsNotNone(self.service()._recognize_douban(meta, ('douban', '35322132'), '电影'))
        self.clock.advance(60)
        with self.assertRaises(self.d.DoubanDeferred) as second:
            service._recognize_douban(meta, ('douban', '2'), '电影')
        self.assertEqual(second.exception.retry_at, self.clock() + 60)
        self.assertEqual(len(self.recognizer.calls), 4)
        self.clock.advance(120)
        self.recognizer.media = self.media
        service._recognize_douban(meta, ('douban', '35322132'), '电影', refresh=True)
        service.current = lambda: False
        with self.assertRaises(self.d.DoubanDeferred):
            service._recognize_douban(meta, ('douban', '9'), '电影')
        self.assertEqual(len(self.recognizer.calls), 5)

    def test_actual_recognition_starts_remain_five_seconds_apart_after_gate_write(self):
        service = self.service()
        original_setting = self.repo.setting
        first_write = [True]
        starts = []
        def setting(key, value=None):
            if key == self.d._DOUBAN_GATE and value is not None and first_write[0]:
                first_write[0] = False
                self.clock.advance(.25)  # First SQLite write takes longer than the second.
            return original_setting(key, value)
        def recognize(meta, declared, *, media_type=None):
            starts.append(self.clock())
            return self.media
        self.repo.setting = setting
        self.recognizer.recognize = recognize
        service._recognize_douban(fixtures.Meta('Fixture'), ('douban', '35322132'), '电影')
        service._recognize_douban(fixtures.Meta('Fixture'), ('douban', '999'), '电影')
        self.assertGreaterEqual(starts[1] - starts[0], 5)

    def test_unavailable_is_deferred_and_does_not_exhaust_record(self):
        self.recognizer.media = None
        service = self.service()
        item = self.d.parse_rss(fixtures.SYNTHETIC_RSS, max_bytes=16384, max_items=1)[0]
        for _ in range(5):
            self.assertEqual(service._observe(service.config.sources[0], item), 'DEFERRED')
            with self.repo.connection() as db:
                row = dict(db.execute('SELECT * FROM discovery_records').fetchone())
            self.assertEqual(row['reason'], 'DOUBAN_DETAIL_UNAVAILABLE')
            self.assertEqual(row['retry_count'], 0)
            self.clock.value = row['next_due']
        self.assertEqual(len(self.recognizer.calls), 5)
        self.assertEqual(service.owner.calls, [])

    def test_pending_refresh_survives_cooldown_without_feed(self):
        service = self.service()
        item = self.d.parse_rss(fixtures.SYNTHETIC_RSS, max_bytes=16384, max_items=1)[0]
        service._observe(service.config.sources[0], item)
        with self.repo.connection() as db:
            row_id = db.execute('SELECT id FROM discovery_records').fetchone()[0]
        service.reprocess([row_id])
        self.recognizer.media = None
        self.assertEqual(service._observe(service.config.sources[0], item), 'DEFERRED')
        # Failed explicit refresh retains a still-valid snapshot for ordinary readers.
        self.assertIsNotNone(service._recognize_douban(fixtures.Meta('Fixture'), ('douban', '35322132'), '电影'))
        self.assertEqual(len(self.recognizer.calls), 2)
        with self.repo.connection() as db:
            due = db.execute('SELECT next_due FROM discovery_records').fetchone()[0]
        self.clock.value = due
        self.recognizer.media = self.media
        async def empty(*_):
            return self.d.FetchResult(b'<rss><channel/></rss>')
        restarted = self.service()
        restarted.fetch = empty
        result = fixtures.DiscoveryTests.wait(restarted.run())
        self.assertEqual(result['sources']['weekly']['history']['states'], ['SUBMITTED'])
        self.assertEqual(len(self.recognizer.calls), 3)

    def test_concurrent_miss_coalesces_and_conflicting_identity_is_not_cached(self):
        a, b = self.service(), self.service()
        meta = fixtures.Meta('Fixture')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service._recognize_douban(
                meta, ('douban', '35322132'), '电影'), [a, b]))
        self.assertTrue(all(results))
        self.assertEqual(len(self.recognizer.calls), 1)
        # Host accidentally returns another subject; later calls must be allowed to correct it.
        a._recognize_douban(meta, ('douban', '999'), '电影')
        b._recognize_douban(meta, ('douban', '999'), '电影')
        self.assertEqual(len(self.recognizer.calls), 3)

    def test_unavailable_entries_cannot_starve_later_subjects_each_day(self):
        service = self.service()
        calls = []
        def recognize(meta, declared, **_):
            calls.append(declared[1])
            return (types.SimpleNamespace(**{**vars(self.media), 'identity': ('douban', '3'),
                                             'douban_id': '3'}) if declared[1] == '3' else None)
        self.recognizer.recognize = recognize
        body = ('<rss><channel>' + ''.join(
            f'<item><guid>{i}</guid><title>Fixture {i}</title><link>https://movie.douban.com/subject/{i}/</link></item>'
            for i in (1, 2, 3)) + '</channel></rss>').encode()
        async def fetch(*_):
            return self.d.FetchResult(body)
        service.fetch = fetch
        for _ in range(3):
            fixtures.DiscoveryTests.wait(service.run())
            self.assertIn('3', calls)  # Valid later subject succeeds on the first run too.
            self.clock.advance(86401)
        self.assertIn('3', calls)
        self.assertEqual(len(service.owner.calls), 1)

    def test_cancelled_spacing_preserves_explicit_history_refresh(self):
        service = self.service()
        item = self.d.parse_rss(fixtures.SYNTHETIC_RSS, max_bytes=16384, max_items=1)[0]
        service._observe(service.config.sources[0], item)
        with self.repo.connection() as db:
            row_id = db.execute('SELECT id FROM discovery_records').fetchone()[0]
        service.reprocess([row_id])
        active = [True]
        service.current = lambda: active[0]
        service.sleep = lambda _: active.__setitem__(0, False)
        self.assertEqual(service._observe(service.config.sources[0], item), 'DEFERRED')
        self.assertEqual(service.records()[0]['reason'], 'REPROCESS_REQUESTED')
        self.clock.advance(10)
        restarted = self.service()
        async def empty(*_):
            return self.d.FetchResult(b'<rss><channel/></rss>')
        restarted.fetch = empty
        self.assertEqual(fixtures.DiscoveryTests.wait(restarted.run())['sources']['weekly']['history']['states'], ['SUBMITTED'])
        self.assertEqual(len(self.recognizer.calls), 2)

    def test_failed_history_rotates_after_untouched_requests_with_one_item_budget(self):
        self.recognizer.media = None
        service = self.service()
        item = self.d.parse_rss(fixtures.SYNTHETIC_RSS, max_bytes=16384, max_items=1)[0]
        for index in range(3):
            service._observe(service.config.sources[0], self.d.RSSItem(**{
                **vars(item), 'item_key': f'item{index}', 'raw_revision': f'rev{index}',
                'douban_subject_id': str(index + 1), 'link': f'https://movie.douban.com/subject/{index + 1}/'}))
        ids = [row['id'] for row in service.records()]
        service.reprocess(ids)
        service.config.request_budget.items = 1
        async def empty(*_):
            return self.d.FetchResult(b'<rss><channel/></rss>')
        service.fetch = empty
        attempted = []
        for _ in range(3):
            self.clock.advance(86401)
            before = len(self.recognizer.calls)
            fixtures.DiscoveryTests.wait(service.run())
            attempted.extend(call[0][1] for call in self.recognizer.calls[before:])
        self.assertEqual(attempted, ['1', '2', '3'])


if __name__ == '__main__':
    unittest.main()
