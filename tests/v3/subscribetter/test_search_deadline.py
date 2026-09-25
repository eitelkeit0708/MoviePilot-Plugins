"""A slow host search cannot consume the discovery deadline or save late rows."""
from pathlib import Path
from threading import Event
import tempfile
import time
import unittest

from test_planner import load


class SearchDeadlineTests(unittest.TestCase):
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
