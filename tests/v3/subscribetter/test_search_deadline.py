"""A slow host search cannot consume the discovery deadline or save late rows."""
from pathlib import Path
from threading import Event, Thread
import tempfile
import time
import unittest

from test_planner import load


class SearchDeadlineTests(unittest.TestCase):
    def test_slow_site_enumeration_returns_at_deadline(self):
        candidates, repository = load("candidates"), load("repository")
        entered, release = Event(), Event()

        class Adapter:
            def sites(self):
                entered.set()
                release.wait(.25)
                return [dict(id=1)]
            def search(self, site, word, page):
                raise AssertionError("expired discovery must not search")

        with tempfile.TemporaryDirectory() as directory:
            service = candidates.CandidateService(repository.Repository(Path(directory) / "state.db"), Adapter())
            start = time.monotonic()
            try:
                rows = service.search([1], ["Late"], candidates.SearchBudget(1, 1, 1, 10, 1, 0), deadline=start + .04)
                elapsed = time.monotonic() - start
            finally:
                release.set()
            self.assertTrue(entered.wait(.1))
            self.assertLess(elapsed, .15)
            self.assertEqual([], rows)

    def test_sqlite_writer_cannot_extend_deadline_or_save_late_candidate(self):
        candidates, repository = load("candidates"), load("repository")
        entered, release, searched, finished = Event(), Event(), Event(), Event()
        raw = dict(site=1, torrent_id="locked", title="Locked", description="Known", labels=[])

        class Adapter:
            def sites(self): return [dict(id=1)]
            def search(self, site, word, page):
                searched.set()
                return [raw]

        with tempfile.TemporaryDirectory() as directory:
            repo = repository.Repository(Path(directory) / "state.db")
            service = candidates.CandidateService(repo, Adapter())
            observe = service.observe
            def track_observe(*args, **kwargs):
                try:
                    return observe(*args, **kwargs)
                finally:
                    finished.set()
            service.observe = track_observe
            def hold_writer():
                with repo.connection(write=True):
                    entered.set()
                    release.wait(.25)
            writer = Thread(target=hold_writer)
            writer.start()
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            try:
                rows = service.search([1], ["Locked"], candidates.SearchBudget(1, 1, 1, 10, 1, 0), deadline=start + .04)
                elapsed = time.monotonic() - start
            finally:
                release.set()
                writer.join(1)
                if searched.is_set():
                    self.assertTrue(finished.wait(1))
            self.assertLess(elapsed, .15)
            self.assertEqual([], rows)
            self.assertEqual([], service.records())

    def test_slow_site_search_returns_at_deadline_without_late_candidate(self):
        candidates, repository = load("candidates"), load("repository")
        entered, release, finished = Event(), Event(), Event()
        raw = dict(site=1, torrent_id="late", title="Late", description="Known", labels=[])

        class Adapter:
            def sites(self): return [dict(id=1)]
            def search(self, site, word, page):
                entered.set()
                release.wait(.25)
                finished.set()
                return [raw]

        with tempfile.TemporaryDirectory() as directory:
            service = candidates.CandidateService(repository.Repository(Path(directory) / "state.db"), Adapter())
            budget = candidates.SearchBudget(1, 1, 1, 10, 1, 0)
            start = time.monotonic()
            try:
                rows = service.search([1], ["Late"], budget, deadline=start + .04)
                elapsed = time.monotonic() - start
            finally:
                release.set()
            self.assertTrue(entered.wait(.1))
            self.assertLess(elapsed, .15)
            self.assertEqual([], rows)
            self.assertTrue(finished.wait(1))
            time.sleep(.03)
            self.assertEqual([], service.records())


if __name__ == "__main__":
    unittest.main()
