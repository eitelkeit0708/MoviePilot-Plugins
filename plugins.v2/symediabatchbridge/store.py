"""Durable receipts and an OS lock shared by reloads of the same instance."""

from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import time
import uuid

from .domain import batch_name


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "batches.sqlite3"
        self.lock_path = directory / "worker.lock"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    body TEXT NOT NULL, updated REAL NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(batches)")}
            if not {"state", "next_check"}.issubset(columns):
                # SQLite DDL otherwise starts outside Python's implicit DML
                # transaction. Migrate atomically and repair a partial old attempt.
                db.execute("BEGIN IMMEDIATE")
                if "state" not in columns:
                    db.execute("ALTER TABLE batches ADD COLUMN state TEXT NOT NULL DEFAULT 'waiting'")
                if "next_check" not in columns:
                    db.execute("ALTER TABLE batches ADD COLUMN next_check REAL NOT NULL DEFAULT 0")
                for identifier, body in db.execute("SELECT id, body FROM batches").fetchall():
                    job = json.loads(body)
                    db.execute("UPDATE batches SET state=?, next_check=? WHERE id=?",
                               (job["state"], job.get("next_check", 0), identifier))
            db.execute("CREATE INDEX IF NOT EXISTS batches_due ON batches(state, next_check, updated)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    def observe(self, *, instance: str, download_hash: str, downloader: str,
                title: str, history_id: int, routing: dict, route_name: str = "默认路线"):
        # A retry/replayed event resolves to the same batch, including after handoff.
        # Routing changes must not make a replayed event look like a new download.
        source_key = json.dumps([downloader, download_hash or f"manual:{history_id}"])
        now = time.time()
        identifier = batch_name(instance, uuid.uuid4().hex)
        job = {"id": identifier, "download_hash": download_hash, "downloader": downloader,
               "title": title, "history_id": history_id, "routing": routing,
               "route_name": route_name,
               "state": "waiting", "message": "等待视频与字幕整理完成", "files": [],
               "created": now, "updated": now, "attempts": 0, "next_check": 0}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO batches(id, source_key, body, updated) VALUES (?, ?, ?, ?)",
                       (identifier, source_key, json.dumps(job, ensure_ascii=False), now))
            existing = json.loads(db.execute("SELECT body FROM batches WHERE source_key=?", (source_key,)).fetchone()[0])
            last_sealed_history = max(existing.get("history_ids") or [existing["history_id"]])
            if existing["state"] == "handed_off" and history_id > last_sealed_history:
                late_ids = existing.setdefault("late_history_ids", [])
                if history_id not in late_ids:
                    late_ids.append(history_id)
                    existing.update(message="移交后收到新的整理记录，请人工核对；不会单独补送字幕或重复移交", updated=now)
                    db.execute("UPDATE batches SET body=?, updated=? WHERE id=?",
                               (json.dumps(existing, ensure_ascii=False), now, existing["id"]))
            return existing

    def save(self, job):
        job["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE batches SET body=?, updated=?, state=?, next_check=? WHERE id=?",
                       (json.dumps(job, ensure_ascii=False), job["updated"], job["state"],
                        job.get("next_check", 0), job["id"]))

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT body FROM batches WHERE id=?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def jobs(self, limit=None):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM batches ORDER BY updated DESC, id LIMIT ?", (limit or -1,))]

    def due(self, limit=3):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM batches WHERE state != 'handed_off' AND next_check <= ? "
                "ORDER BY updated, id LIMIT ?", (time.time(), limit))]

    def pending_routings(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT DISTINCT json_extract(body, '$.routing') FROM batches WHERE state != 'handed_off'")]

    @contextmanager
    def worker_lock(self):
        """No stale lease timeout: a still-running upload must keep its exclusive lock."""
        handle = self.lock_path.open("a+b")
        acquired = False
        try:
            if self.lock_path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                pass
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
