"""W01 unit evidence only: durable intents and target ownership (T148/T188)."""
import concurrent.futures
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / "plugins.v3/subscribetter/repository.py"


def load_repository():
    spec = importlib.util.spec_from_file_location("subscribetter_repository", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PATH.exists():
            cls.mod = load_repository()

    def setUp(self):
        self.assertTrue(PATH.exists(), "W01 durable repository is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.repo = self.mod.Repository(self.path)
        self.target = self.mod.Target("电视剧", "themoviedb", "00042", 0, "specials")

    def submit(self, key="fictional-intent", **kwargs):
        return self.repo.submit(key, self.target, {"name": "Fictional", "cookie": "NEVER_STORE"}, "admin", **kwargs)

    def test_T188_parallel_intents_one_target_and_stopped_never_revives(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda i: self.submit(f"intent-{i}"), range(20)))
        self.assertEqual(1, len({row["id"] for row in rows}))
        self.repo.set_state(rows[0]["id"], "STOPPED", "admin")
        self.assertEqual("STOPPED", self.submit("again")["state"])
        self.assertEqual([], self.repo.pending_actions())
        self.assertEqual("STOPPED", self.mod.Repository(self.path).get_task(rows[0]["id"])["state"])

    def test_T148_intent_conflicts_and_separate_source_group_season(self):
        first = self.submit()
        self.assertEqual(first["id"], self.submit()["id"])
        for target in (self.mod.Target("电视剧", "douban", "00042", 0, "specials"),
                       self.mod.Target("电视剧", "themoviedb", "00042", 1, "specials"),
                       self.mod.Target("电视剧", "themoviedb", "00042", 0, "other")):
            with self.assertRaises(ValueError):
                self.repo.submit("fictional-intent", target, {}, "admin")
            row = self.repo.submit(target.key, target, {}, "admin")
            self.assertNotEqual(first["id"], row["id"])
        self.assertEqual(0, first["season"])
        self.assertEqual("00042", first["media_id"])
        self.assertEqual(4, len(self.repo.list_tasks()))

    def test_T148_persist_before_host_and_public_snapshot_sanitized(self):
        row = self.submit()
        actions = self.repo.pending_actions()
        self.assertEqual(row["id"], actions[0]["task_id"])
        self.assertEqual("PENDING", actions[0]["state"])
        self.assertNotIn("cookie", row["snapshot"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(12, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertGreater(db.execute("SELECT count(*) FROM audit").fetchone()[0], 0)
        self.assertNotIn(b"NEVER_STORE", self.path.read_bytes())

    def test_T010_released_paused_passive_stopped_are_distinct(self):
        row = self.submit()
        for state in ("PAUSED", "PASSIVE", "STOPPED"):
            self.repo.set_state(row["id"], state, "admin")
            self.assertEqual(state, self.repo.get_task(row["id"])["state"])
        with self.assertRaises(ValueError):
            self.repo.set_state(row["id"], "ACTIVE", "admin")
        with self.assertRaises(ValueError):
            self.mod.Target("电影", "themoviedb", "42", 0)

    def test_future_schema_is_not_downgraded(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=999")
            db.commit()
        with self.assertRaises(RuntimeError):
            self.mod.Repository(self.path)

    def test_explicit_conflict_adoption_is_atomic_and_cannot_steal_or_revive(self):
        task = self.submit()
        self.repo.action_state(task["id"], "PENDING", "NATIVE_CONFLICT")
        snapshot = {"name": "Native", "state": "R", "keyword": "preserved"}
        adopted = self.repo.submit("adopt", self.target, snapshot, "admin", 42, True)
        self.assertEqual(task["id"], adopted["id"])
        self.assertEqual(42, adopted["native_id"])
        self.assertEqual(snapshot, adopted["snapshot"])
        with self.repo.connection() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM audit WHERE action='EXPLICIT_NATIVE_ADOPTED' AND task_id=?", (task["id"],)).fetchone()[0])
        with self.assertRaises(ValueError):
            self.repo.submit("steal", self.target, {}, "admin", 43, True)
        other = self.mod.Target("电视剧", "themoviedb", "other", 0, "specials")
        with self.assertRaises(ValueError):
            self.repo.submit("steal-other", other, {}, "admin", 42, True)
        stopped = self.repo.submit("stopped", other, {}, "admin")
        self.repo.set_state(stopped["id"], "STOPPED", "admin")
        with self.assertRaises(ValueError):
            self.repo.submit("revive", other, {}, "admin", 99, True)
        self.assertEqual("STOPPED", self.repo.get_task(stopped["id"])["state"])
        self.assertIsNone(self.repo.get_task(stopped["id"])["native_id"])

    def test_competing_explicit_adoptions_bind_one_native_snapshot(self):
        from threading import Barrier
        task = self.submit()
        self.repo.action_state(task["id"], "PENDING", "NATIVE_CONFLICT")
        gate = Barrier(2)
        def adopt(sid):
            gate.wait(timeout=3)
            try:
                return self.repo.submit(f"adopt-{sid}", self.target, {"name": str(sid)}, "admin", sid, True)
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(adopt, (42, 43)))
        self.assertEqual(1, sum(row is not None for row in results))
        current = self.repo.get_task(task["id"])
        self.assertEqual(str(current["native_id"]), current["snapshot"]["name"])


if __name__ == "__main__":
    unittest.main()

