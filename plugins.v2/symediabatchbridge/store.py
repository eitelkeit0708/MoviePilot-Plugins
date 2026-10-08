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
                title: str, history_id: int, routing: dict):
        # A retry/replayed event resolves to the same batch, including after handoff.
        # Routing changes must not make a replayed event look like a new download.
        source_key = json.dumps([downloader, download_hash or f"manual:{history_id}"])
        now = time.time()
        identifier = batch_name(instance, uuid.uuid4().hex)
        job = {"id": identifier, "download_hash": download_hash, "downloader": downloader,
               "title": title, "history_id": history_id, "routing": routing,
               "state": "waiting", "message": "等待视频与字幕整理完成", "files": [],
               "created": now, "updated": now, "attempts": 0, "next_check": 0}
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO batches VALUES (?, ?, ?, ?)",
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

    def save(self, job):
        job["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE batches SET body=?, updated=? WHERE id=?",
                       (json.dumps(job, ensure_ascii=False), job["updated"], job["id"]))

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT body FROM batches WHERE id=?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def jobs(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM batches ORDER BY updated, id")]

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
