"""T086/T087 partial evidence: supported watcher-off scans, not inotify faults."""
from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_delivery as delivery_tests
import test_management as management_tests
import test_planner as planner


class WatchDisabledTests(unittest.TestCase):
    def setUp(self):
        delivery_tests.DeliveryTests.setUpClass()
        self.f = delivery_tests.DeliveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def observations(self):
        with self.f.repo.connection() as db:
            return {row['path']: json.loads(row['data'])['state']
                    for row in db.execute('SELECT path,data FROM local_observations')}

    def checkpoint(self):
        with self.f.repo.connection() as db:
            return json.loads(db.execute(
                "SELECT data FROM reconcile_checkpoints WHERE scope='local:r'").fetchone()[0])

    def test_watch_enable_rejected_at_both_typed_and_worker_boundaries(self):
        config = planner.load('configuration')
        # Typed native host configuration uses POSIX paths; actual scan fixtures
        # below retain this platform's real temporary-directory paths.
        native_rule = dict(self.f.rule, local_root='/fixture/local', read_roots=['/fixture/local'])
        self.assertIs(False, config.Recovery().watcher)
        self.assertIs(False, config.DeliveryRule.model_validate(native_rule).watcher)
        with self.assertRaises(ValueError):
            config.Recovery(watcher=True)
        with self.assertRaises(ValueError):
            config.DeliveryRule.model_validate(dict(native_rule, watcher=True))
        with self.assertRaisesRegex(ValueError, 'WATCHER_UNSUPPORTED_USE_PERIODIC_SCAN'):
            self.f.m.LocalReconciler(self.f.repo, [dict(self.f.rule, watcher=True)])
        with self.f.repo.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM reconcile_checkpoints').fetchone()[0])

    def test_no_notifications_due_full_scan_finds_unknown_deep_range_after_restart(self):
        f = self.f
        deep = f.local / 'deep'
        deep.mkdir()
        (deep / 'known.srt').write_text('known')
        scanner = f.m.LocalReconciler(f.repo, [f.rule])
        before = scanner.scan('r', now=planner.NOW)
        self.assertEqual('COMPLETE', before['state'])
        root_times = f.local.stat()
        original = f.local / 'movie.zh.srt'
        original.unlink()
        branch = deep / 'unseen' / 'nested'
        branch.mkdir(parents=True)
        added = branch / 'late.srt'
        added.write_text('late')
        os.utime(f.local, ns=(root_times.st_atime_ns, root_times.st_mtime_ns))
        self.assertEqual(before, scanner.scan('r', now=planner.NOW + timedelta(seconds=59)))
        self.assertNotIn(str(added), self.observations())
        at = planner.NOW + timedelta(seconds=61)
        first = scanner.scan('r', limits={'entries': 1}, now=at)
        self.assertEqual('INCOMPLETE', first['state'])
        self.assertTrue(first['stack'])
        self.assertNotEqual(before['epoch'], first['epoch'])
        self.assertEqual(first, self.checkpoint())
        self.assertEqual('PRESENT', self.observations()[str(original)])
        # Reopen the actual SQLite repository and reconstruct the scanner every batch.
        for _ in range(40):
            repo = f.r.Repository(f.repo.path)
            after = f.m.LocalReconciler(repo, [f.rule]).scan('r', limits={'entries': 1}, now=at)
            self.assertEqual(first['epoch'], after['epoch'])
            if after['state'] == 'COMPLETE':
                break
        self.assertEqual('COMPLETE', after['state'])
        self.assertEqual('PRESENT', self.observations()[str(added)])
        self.assertEqual('MISSING', self.observations()[str(original)])
        self.assertFalse(after['hint'])
        with f.repo.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM delivery_bundles').fetchone()[0])

    def test_scan_permission_failure_is_durable_visible_and_recovers_without_watcher(self):
        f = self.f
        deep = f.local / 'unreadable'
        deep.mkdir()
        (deep / 'present.srt').write_text('present')
        scanner = f.m.LocalReconciler(f.repo, [f.rule])
        self.assertEqual('COMPLETE', scanner.scan('r', now=planner.NOW)['state'])
        removed = f.local / 'movie.zh.srt'
        removed.unlink()
        scandir = os.scandir
        def fail_exact(path):
            if Path(path) == deep:
                raise PermissionError('fictional local range failure')
            return scandir(path)
        at = planner.NOW + timedelta(seconds=61)
        with patch.object(f.m.os, 'scandir', side_effect=fail_exact):
            failed = scanner.scan('r', now=at)
        self.assertEqual('INCOMPLETE', failed['state'])
        self.assertEqual([str(deep)], failed['failed_paths'])
        self.assertEqual(failed, self.checkpoint())
        self.assertEqual('PRESENT', self.observations()[str(removed)])
        management_tests.ManagementTests.setUpClass()
        view_fixture = management_tests.ManagementTests()
        view_fixture.setUp()
        self.addCleanup(view_fixture.doCleanups)
        view_fixture.plugin.repository = f.repo
        view = planner.load('ui').Views(view_fixture.plugin)
        with f.repo.connection() as db:
            before = '\n'.join(db.iterdump())
        health = view.health_rows('local_scans', limit=25, offset=0, user=None)
        self.assertEqual(1, health.total)
        self.assertEqual('INCOMPLETE', health.items[0].data['data']['state'])
        self.assertEqual(1, health.items[0].data['failed_count'])
        paths = view.local_scan('r', 'failed_paths', limit=25, offset=0, user=None)
        self.assertEqual(1, paths.total)
        self.assertEqual(['[PATH]'], [row.data['entry'] for row in paths.items])
        with f.repo.connection() as db:
            self.assertEqual(before, '\n'.join(db.iterdump()))
        restarted = f.m.LocalReconciler(f.r.Repository(f.repo.path), [f.rule])
        recovered = restarted.scan('r', now=at + timedelta(seconds=1))
        self.assertEqual('COMPLETE', recovered['state'])
        self.assertEqual([], recovered['failed_paths'])
        self.assertEqual('MISSING', self.observations()[str(removed)])


if __name__ == '__main__':
    unittest.main()
