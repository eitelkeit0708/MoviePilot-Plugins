"""A legacy pseudo-success only links to independently verified current media."""
import json
import unittest

import test_archive as archive_fixture
from test_configuration import PrivateFixture
from test_planner import load


class LegacyHistoryLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive_fixture.ArchiveTests.setUpClass()
        from test_ownership import PluginTests
        PluginTests.setUpClass()

    def setUp(self):
        self.case = archive_fixture.ArchiveTests('test_current_old_library_independent_item_id_churn_multi_versions')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.repo = self.case.repo
        store = PrivateFixture()
        config = load('configuration').Configuration(self.repo, 'SubscriBetter', store, lambda _: None)
        config.initialize({})
        self.migration = load('migration').Migration(self.repo, config, store, lambda: {})
        target = self.case.r.Target('电影', 'themoviedb', '42')
        self.task = self.repo.submit('existing', target, {'name': 'Fiction', 'year': 2026}, 'admin')
        self.repo.complete_handoff(self.task['id'], self.task['generation'])
        assert self.case.archive.reconcile('test', '10')['status'] == 'COMPLETE'
        assert self.case.archive.current([self.case.key])[self.case.key]['state'] == 'PRESENT'
        with self.repo.connection() as db:
            self.version = db.execute('SELECT id FROM archive_versions WHERE active=1').fetchone()[0]
        raw = json.dumps({'history': [
            {'title': 'Fiction', 'year': 2026, 'type': '电影', 'unique': 'opaque',
             'tmdbid': 0, 'status': '已添加订阅'},
            {'title': 'Another film', 'type': '电影', 'unique': 'opaque',
             'tmdbid': None, 'status': '订阅已存在'}]}, ensure_ascii=False).encode()
        preview = self.migration.preview_import(raw, 'legacy', '1', None, 'admin')
        self.migration.import_page(preview['receipt_id'], preview['revision'], preview['digest'],
                                   0, 100, 'page', 'admin')
        self.receipt = preview['receipt_id']

    def test_explicit_current_link_retains_old_status_without_new_work(self):
        rows = self.migration.history(self.receipt, 100, 0)
        before = self.repo.get_action(self.task['id'])
        with self.assertRaisesRegex(ValueError, 'LEGACY_IDENTITY_UNVERIFIED'):
            self.migration.link_history(self.receipt, 1, rows[1]['digest'],
                                        self.task['id'], self.version, self.case.archive, 'admin')
        linked = self.migration.link_history(self.receipt, 0, rows[0]['digest'],
                                             self.task['id'], self.version, self.case.archive, 'admin')
        self.assertEqual('LEGACY_UNVERIFIED', linked['state'])
        self.assertEqual('已添加订阅', linked['raw']['status'])
        self.assertEqual(self.task['id'], linked['link']['task_id'])
        self.assertEqual(self.version, linked['link']['version_id'])
        self.assertEqual('test', linked['link']['service'])
        self.assertEqual('10', linked['link']['library'])
        model = load('management').HistoryRow.model_validate(linked)
        self.assertEqual(self.version, model.link.version_id)
        self.assertEqual(linked, self.migration.link_history(self.receipt, 0, rows[0]['digest'],
            self.task['id'], self.version, self.case.archive, 'admin'))
        later = self.migration.history(self.receipt, 100, 0)
        self.assertIsNone(later[1].get('link'))
        self.assertEqual(linked, later[0])
        self.assertEqual(before, self.repo.get_action(self.task['id']))
        with self.repo.connection() as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM tasks').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT count(*) FROM archive_versions WHERE active=1').fetchone()[0])

    def test_stale_or_mismatched_current_cannot_link(self):
        row = self.migration.history(self.receipt, 100, 0)[0]
        with self.assertRaisesRegex(ValueError, 'LEGACY_DIGEST_CHANGED'):
            self.migration.link_history(self.receipt, 0, '0' * 64,
                                        self.task['id'], self.version, self.case.archive, 'admin')
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE archive_versions SET active=0 WHERE id=?', (self.version,))
        with self.assertRaisesRegex(ValueError, 'CURRENT_LIBRARY_UNVERIFIED'):
            self.migration.link_history(self.receipt, 0, row['digest'],
                                        self.task['id'], self.version, self.case.archive, 'admin')

    def test_new_link_projection_cannot_expose_private_source_value(self):
        row = self.migration.history(self.receipt, 100, 0)[0]
        ref = self.migration.secrets.put('test')
        with self.repo.connection(write=True) as db:
            saved = db.execute('SELECT data FROM migration_receipts WHERE id=?', (self.receipt,)).fetchone()[0]
            receipt = json.loads(saved)
            receipt['private_context'] = [ref]
            db.execute('UPDATE migration_receipts SET data=? WHERE id=?',
                       (json.dumps(receipt), self.receipt))
        with self.assertRaisesRegex(ValueError, 'PRIVATE_VALUE_IN_MIGRATION'):
            self.migration.link_history(self.receipt, 0, row['digest'],
                                        self.task['id'], self.version, self.case.archive, 'admin')
        with self.repo.connection() as db:
            current = json.loads(db.execute('SELECT data FROM migration_history WHERE receipt_id=? AND ordinal=0',
                                            (self.receipt,)).fetchone()[0])
        self.assertNotIn('link', current)


if __name__ == '__main__':
    unittest.main()
