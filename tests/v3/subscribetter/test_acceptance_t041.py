"""T041: startup, notification and timer discovery share one durable scan queue."""
from datetime import timedelta
import json
import os
import struct
import threading
import time
import unittest

import test_delivery as delivery_tests
import test_management as management_tests
import test_planner as planner


class T041Tests(unittest.TestCase):
    def setUp(self):
        delivery_tests.DeliveryTests.setUpClass()
        self.f = delivery_tests.DeliveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_concurrent_discovery_coalesces_and_changed_file_gets_new_identity(self):
        rule = dict(self.f.rule, watcher=True)
        scanner = self.f.m.LocalReconciler(self.f.repo, [rule])
        barrier = threading.Barrier(3)
        errors = []

        def run(function):
            try:
                barrier.wait()
                function()
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=run, args=(lambda: scanner.scan('r', force=True, now=planner.NOW),)),
            threading.Thread(target=run, args=(lambda: scanner.notify('r', 'FILESYSTEM_EVENT'),)),
            threading.Thread(target=run, args=(lambda: scanner.scan('r', force=True, now=planner.NOW),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        scanner.scan('r', now=planner.NOW)

        path = self.f.local / 'movie.mkv'
        with self.f.repo.connection() as db:
            rows = db.execute('SELECT path,data FROM local_observations WHERE rule_id=?', ('r',)).fetchall()
            first = json.loads(db.execute('SELECT data FROM local_observations WHERE rule_id=? AND path=?', ('r', str(path))).fetchone()[0])
            checkpoint = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='local:r'").fetchone()[0])
        self.assertEqual(2, len(rows))
        self.assertEqual('COMPLETE', checkpoint['state'])
        self.assertFalse(checkpoint['hint'])
        with self.f.repo.connection() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM delivery_bundles').fetchone()[0])

        path.write_bytes(b'y' * 100)
        scanner.notify('r', 'FILESYSTEM_EVENT')
        scanner.scan('r', now=planner.NOW + timedelta(seconds=1))
        with self.f.repo.connection() as db:
            second = json.loads(db.execute('SELECT data FROM local_observations WHERE rule_id=? AND path=?', ('r', str(path))).fetchone()[0])
            count = db.execute('SELECT count(*) FROM local_observations WHERE rule_id=? AND path=?', ('r', str(path))).fetchone()[0]
        self.assertEqual(1, count)
        self.assertNotEqual(first['identity'], second['identity'])
        self.assertNotEqual(first['stable_since'], second['stable_since'])

    def test_registration_failure_persists_full_scan_duty_and_retry_recovers(self):
        rule = dict(self.f.rule, watcher=True)
        attempts = []

        def fail(*args):
            attempts.append(args)
            raise OSError('inotify watch limit reached')

        scanner = self.f.m.LocalReconciler(self.f.repo, [rule], watch_factory=fail)
        scanner.start_watchers()
        with self.f.repo.connection() as db:
            health = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='watcher:r'").fetchone()[0])
            queued = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='local:r'").fetchone()[0])
        self.assertEqual('DEGRADED', health['state'])
        self.assertEqual('WATCH_REGISTRATION_FAILED', health['reason'])
        self.assertEqual([str(self.f.local)], health['failed_paths'])
        self.assertEqual(['.'], health['unmonitored'])
        self.assertTrue(queued['hint'])
        management_tests.ManagementTests.setUpClass()
        fixture = management_tests.ManagementTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plugin.repository = self.f.repo
        view = planner.load('ui').Views(fixture.plugin)
        page = view.health_rows('local_scans', user=None)
        visible = next(row.data['data'] for row in page.items if row.id == 'watcher:r')
        self.assertEqual(['.'], visible['unmonitored'])
        self.assertNotIn('failed_paths', visible)

        class Watch:
            def __init__(self, root, callback):
                self.root, self.callback, self.closed = root, callback, False
            def start(self):
                self.callback('FILESYSTEM_EVENT')
            def close(self):
                self.closed = True

        scanner.watch_factory = Watch
        scanner.start_watchers()
        with self.f.repo.connection() as db:
            health = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='watcher:r'").fetchone()[0])
        self.assertEqual('HEALTHY', health['state'])
        self.assertEqual([], health['failed_paths'])
        self.assertEqual([], health['unmonitored'])
        watch = scanner.watchers['r']
        scanner.close()
        self.assertTrue(watch.closed)

    def test_inotify_parser_does_not_drop_queue_overflow(self):
        watcher = self.f.m.InotifyWatcher
        payload = struct.pack('iIII', -1, watcher.IN_Q_OVERFLOW, 0, 0)
        self.assertEqual([(-1, watcher.IN_Q_OVERFLOW, b'')], list(watcher.events(payload)))
        self.assertEqual('IN_Q_OVERFLOW', watcher.reason(watcher.IN_Q_OVERFLOW))
        self.assertEqual('FILESYSTEM_EVENT', watcher.reason(watcher.IN_CREATE))
        self.assertEqual(watcher.IN_ONLYDIR, watcher.MASK & watcher.IN_ONLYDIR)
        self.assertEqual(watcher.IN_DONT_FOLLOW, watcher.MASK & watcher.IN_DONT_FOLLOW)

    def test_inotify_thread_exit_closes_descriptor(self):
        watcher = self.f.m.InotifyWatcher(self.f.local, lambda *args: None)
        descriptor = os.open(os.devnull, os.O_RDONLY)
        watcher.fd = descriptor
        watcher.stop.set()
        watcher._run()
        self.assertIsNone(watcher.fd)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_delivery_owns_one_watcher_and_closes_it(self):
        instances = []
        class Watch:
            def __init__(self, root, callback):
                self.root, self.callback, self.started, self.closed = root, callback, False, False
                instances.append(self)
            def start(self):
                self.started = True
            def close(self):
                self.closed = True

        rule = dict(self.f.rule, watcher=True)
        worker = self.f.m.Delivery(self.f.repo, self.f.auth, None, self.f.cloud,
            rules=[rule], revalidate=lambda _: self.f.publication,
            watchers=True, watch_factory=Watch)
        worker.local_maintenance(entries=100, deadline=time.monotonic() + 5)
        worker.local_maintenance(entries=100, deadline=time.monotonic() + 5)
        self.assertEqual(1, len(instances))
        self.assertTrue(instances[0].started)
        instances[0].callback('FILESYSTEM_EVENT')
        with self.f.repo.connection() as db:
            queued = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='local:r'").fetchone()[0])
        self.assertTrue(queued['hint'])
        worker.close()
        self.assertTrue(instances[0].closed)
        with self.f.repo.connection() as db:
            before = '\n'.join(db.iterdump())
        instances[0].callback('WATCH_READ_FAILED', str(self.f.local))
        with self.f.repo.connection() as db:
            self.assertEqual(before, '\n'.join(db.iterdump()))

    def test_overflow_degrades_then_complete_full_scan_restores_health(self):
        class Watch:
            def __init__(self, root, callback):
                self.callback = callback
            def start(self):
                pass
            def close(self):
                pass

        scanner = self.f.m.LocalReconciler(self.f.repo, [dict(self.f.rule, watcher=True)], watch_factory=Watch)
        self.addCleanup(scanner.close)
        scanner.start_watchers()
        deep = self.f.local / 'deep'
        deep.mkdir()
        added = deep / 'missed.srt'
        added.write_text('subtitle')
        scanner.watchers['r'].callback('IN_Q_OVERFLOW', str(deep))
        with self.f.repo.connection() as db:
            degraded = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='watcher:r'").fetchone()[0])
        self.assertEqual('DEGRADED', degraded['state'])
        self.assertEqual('IN_Q_OVERFLOW', degraded['reason'])
        self.assertEqual(['.'], degraded['unmonitored'])
        result = scanner.scan('r', now=planner.NOW)
        self.assertEqual('COMPLETE', result['state'])
        with self.f.repo.connection() as db:
            health = json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='watcher:r'").fetchone()[0])
            observed = db.execute('SELECT count(*) FROM local_observations WHERE rule_id=? AND path=?', ('r', str(added))).fetchone()[0]
        self.assertEqual('HEALTHY', health['state'])
        self.assertEqual([], health['unmonitored'])
        self.assertEqual(1, observed)


if __name__ == '__main__':
    unittest.main()
