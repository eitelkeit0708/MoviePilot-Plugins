"""Durable receipts and an OS lock shared by reloads of the same instance."""

from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import time
import uuid

from .domain import batch_name
from .activity import changes, notice_text
from .media import notification_media


class Store:
    def __init__(self, directory: Path, event_sink=None):
        self.event_sink = event_sink
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "batches.sqlite3"
        self.lock_path = directory / "worker.lock"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    body TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS activity (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL,
                    at REAL NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS activity_batch ON activity(batch, seq);
                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL,
                    scope TEXT NOT NULL, at REAL NOT NULL, body TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                    submitted_at REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS notices_due ON notices(submitted_at, next_at);
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
                title: str, history_id: int, routing: dict, route_name: str = "默认路线", cleanup_local=False,
                inventory_files=None, media=None):
        # A retry/replayed event resolves to the same batch, including after handoff.
        # Routing changes must not make a replayed event look like a new download.
        source_key = json.dumps([downloader, download_hash or f"manual:{history_id}"])
        now = time.time()
        identifier = batch_name(instance, uuid.uuid4().hex)
        job = {"id": identifier, "download_hash": download_hash, "downloader": downloader,
               "title": title, "history_id": history_id, "routing": routing,
               "route_name": route_name, "media": media or {},
               "cleanup_local": bool(cleanup_local),
               "state": "waiting", "message": "等待视频与字幕整理完成", "files": [],
               "created": now, "updated": now, "attempts": 0, "next_check": 0}
        job["origin"] = "inventory" if inventory_files is not None else "transfer"
        if inventory_files is not None:
            job["inventory_files"] = inventory_files
            job["message"] = "已接管现存整理文件，等待核对并上传"
        with self.connect() as db:
            inserted = db.execute("INSERT OR IGNORE INTO batches(id, source_key, body, updated) VALUES (?, ?, ?, ?)",
                       (identifier, source_key, json.dumps(job, ensure_ascii=False), now))
            existing = json.loads(db.execute("SELECT body FROM batches WHERE source_key=?", (source_key,)).fetchone()[0])
            old = json.loads(json.dumps(existing))
            last_sealed_history = max(existing.get("history_ids") or [existing["history_id"]])
            if existing["state"] == "handed_off" and history_id > last_sealed_history:
                late_ids = existing.setdefault("late_history_ids", [])
                if history_id not in late_ids:
                    late_ids.append(history_id)
                    existing.update(message="移交后收到新的整理记录，请人工核对；不会单独补送字幕或重复移交", updated=now)
                    db.execute("UPDATE batches SET body=?, updated=? WHERE id=?",
                               (json.dumps(existing, ensure_ascii=False), now, existing["id"]))
            items = self._record(db, existing, changes(None if inserted.rowcount else old, existing))
        self._emit(existing, items)
        return existing

    def restore_origin(self, job):
        """Migrate pre-1.4.1 imports from durable events, without changing sealed jobs."""
        if job.get("origin"):
            return
        with self.connect() as db:
            imported = any(json.loads(row[0]).get("kind") == "imported" for row in
                           db.execute("SELECT body FROM activity WHERE batch=?", (job["id"],)))
        job["origin"] = "inventory" if imported and not job.get("files") and not job.get("move_requested") else "transfer"
        self.save(job)

    def save(self, job):
        job["updated"] = time.time()
        with self.connect() as db:
            row = db.execute("SELECT body FROM batches WHERE id=?", (job["id"],)).fetchone()
            if row is None:
                raise KeyError("Unknown batch")
            old = json.loads(row[0])
            db.execute("UPDATE batches SET body=?, updated=?, state=?, next_check=? WHERE id=?",
                       (json.dumps(job, ensure_ascii=False), job["updated"], job["state"],
                        job.get("next_check", 0), job["id"]))
            items = self._record(db, job, changes(old, job))
        self._emit(job, items)

    def _record(self, db, job, items):
        now = time.time()
        for item in items:
            item["at"] = now
            db.execute("INSERT INTO activity(batch,at,body) VALUES (?,?,?)",
                       (job["id"], now, json.dumps(item, ensure_ascii=False)))
            if not item.get("notice"):
                continue
            scope = item["scope"]
            # One fallback notice per file, one successful handoff per batch.
            # A persistent issue reminds daily, never once per scheduler tick.
            recent = db.execute("SELECT at FROM notices WHERE batch=? AND scope=? ORDER BY id DESC LIMIT 1",
                                (job["id"], scope)).fetchone()
            if recent and (scope != "issue" or now - recent[0] < 86400):
                continue
            payload = {"title": "115秒传助手 · " + item["notice"], "text": notice_text(job, item)}
            image = notification_media(job, item)[1]
            if image:
                payload["image"] = image
            db.execute("INSERT INTO notices(batch,scope,at,body) VALUES (?,?,?,?)",
                       (job["id"], scope, now, json.dumps(payload, ensure_ascii=False)))
        return items

    def record(self, job, item):
        with self.connect() as db:
            items = self._record(db, job, [item])
        self._emit(job, items)

    def _emit(self, job, items):
        if self.event_sink:
            for item in items:
                try:
                    self.event_sink(job, item)
                except Exception:
                    # Telemetry must not turn a committed upload into a failed upload.
                    pass

    def events(self, batch, page=0, limit=30):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM activity WHERE batch=? ORDER BY seq DESC LIMIT ? OFFSET ?",
                (batch, limit, page * limit))]

    def pending_notices(self, limit=20):
        with self.connect() as db:
            return [{"id": r[0], "batch": r[1], "attempts": r[2], **json.loads(r[3])} for r in db.execute(
                "SELECT id,batch,attempts,body FROM notices WHERE submitted_at=0 AND next_at<=? ORDER BY id LIMIT ?",
                (time.time(), limit))]

    def notice_result(self, item, success):
        now = time.time()
        with self.connect() as db:
            if success:
                db.execute("UPDATE notices SET submitted_at=? WHERE id=?", (now, item["id"]))
            else:
                delay = min(1800, 60 * 2 ** min(item["attempts"], 5))
                db.execute("UPDATE notices SET attempts=attempts+1,next_at=? WHERE id=?", (now + delay, item["id"]))
            message = "通知已提交 MP 通知队列" if success else "通知提交失败，将自动重试"
            db.execute("INSERT INTO activity(batch,at,body) VALUES (?,?,?)", (item["batch"], now, json.dumps(
                {"kind": "notification", "message": message, "level": "info" if success else "warning", "at": now}, ensure_ascii=False)))

    def page_jobs(self, page=0, limit=12):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM batches ORDER BY CASE WHEN state IN ('review','retrying') "
                "OR COALESCE(json_extract(body,'$.attempts'),0)>0 "
                "OR COALESCE(json_array_length(body,'$.late_history_ids'),0)>0 "
                "OR COALESCE(json_extract(body,'$.cleanup_error'),'')!='' "
                "OR EXISTS(SELECT 1 FROM json_each(body,'$.files') f WHERE json_extract(f.value,'$.instant_error_since') IS NOT NULL) "
                "OR (state='waiting' AND ?-json_extract(body,'$.created')>=86400) THEN 0 "
                "WHEN state!='handed_off' THEN 1 ELSE 2 END, updated DESC, id LIMIT ? OFFSET ?",
                (time.time(), limit, page * limit))]

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT state,COUNT(*) FROM batches GROUP BY state"))

    def source_keys(self):
        with self.connect() as db:
            return {tuple(json.loads(r[0])) for r in db.execute("SELECT source_key FROM batches")}

    def cleanup_jobs(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM batches WHERE state='handed_off' AND json_extract(body,'$.cleanup_local')=1 "
                "AND COALESCE(json_extract(body,'$.cleanup_done'),0)=0 AND COALESCE(json_extract(body,'$.cleanup_next'),0)<=? LIMIT 3", (time.time(),))]

    def notice_counts(self, batch):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*),COALESCE(SUM(submitted_at>0),0) FROM notices WHERE batch=?", (batch,)).fetchone()

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
