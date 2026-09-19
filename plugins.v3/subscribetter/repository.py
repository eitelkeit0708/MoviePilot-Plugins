"""Plugin-owned SQLite state. No host database sessions or long transactions."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Target:
    media_type: str
    media_source: str
    media_id: str
    season: int | None = None
    episode_group: str = ""

    def __post_init__(self):
        if self.media_type not in ("电影", "电视剧"):
            raise ValueError("unsupported media type")
        if not isinstance(self.media_source, str) or not self.media_source.strip():
            raise ValueError("media source required")
        if not isinstance(self.media_id, str) or not self.media_id.strip() or self.media_id.strip() == "0":
            raise ValueError("nonzero string media ID required")
        if self.media_type == "电影" and (self.season is not None or self.episode_group):
            raise ValueError("movie has no season or episode group")
        if self.media_type == "电视剧" and (type(self.season) is not int or self.season < 0):
            raise ValueError("TV requires a nonnegative season")
        if not isinstance(self.episode_group, str) or len(self.episode_group) > 256:
            raise ValueError("invalid episode group")
        if len(self.media_id) > 256 or len(self.media_source) > 128:
            raise ValueError("identity too long")
        object.__setattr__(self, "media_source", self.media_source.strip().casefold())
        object.__setattr__(self, "media_id", self.media_id.strip())

    @property
    def key(self) -> str:
        return json.dumps([self.media_type, self.media_source, self.media_id, self.season, self.episode_group], ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_task(cls, task: dict) -> Target:
        return cls(task["media_type"], task["media_source"], task["media_id"], task["season"], task["episode_group"])


# Host fields only; never serialize a host service, ORM __dict__, or config object.
SNAPSHOT_FIELDS = frozenset({
    "name", "year", "type", "media_source", "media_id", "season", "episode_group",
    "keyword", "username", "sites", "downloader", "save_path", "custom_words",
    "total_episode", "start_episode", "best_version", "best_version_full",
    "state", "filter", "include", "exclude", "quality", "resolution", "effect",
    "filter_groups", "media_category_id", "media_category",
})


def snapshot_config(value: dict) -> dict:
    """Private restore snapshot; API projections deliberately exclude these fields."""
    result = {key: value[key] for key in SNAPSHOT_FIELDS if key in value}
    # Round-trip freezes mutable inputs and refuses arbitrary runtime objects.
    return json.loads(json.dumps(result, ensure_ascii=False))


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            revision = db.execute("PRAGMA user_version").fetchone()[0]
            if revision > 1:
                raise RuntimeError("unsupported future database revision")
            if revision == 0:
                statements = (
                    "CREATE TABLE tasks (id INTEGER PRIMARY KEY, target_key TEXT UNIQUE NOT NULL, media_type TEXT NOT NULL, media_source TEXT NOT NULL, media_id TEXT NOT NULL, season INTEGER, episode_group TEXT NOT NULL, state TEXT NOT NULL, native_id INTEGER UNIQUE, snapshot TEXT NOT NULL, actor TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE intents (intent_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, task_id INTEGER NOT NULL REFERENCES tasks(id), created_at TEXT NOT NULL)",
                    "CREATE TABLE outbox (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id), operation TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT, updated_at TEXT NOT NULL)",
                    "CREATE TABLE audit (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL)",
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                    "CREATE INDEX tasks_identity ON tasks(media_type,media_source,media_id)",
                    "PRAGMA user_version=1",
                )
                for statement in statements:
                    db.execute(statement)

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _task(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = json.loads(result["snapshot"])
        return result

    @staticmethod
    def _audit(db, task_id, action, actor):
        db.execute("INSERT INTO audit(task_id,action,actor,at) VALUES(?,?,?,?)", (task_id, action, actor, utcnow()))

    def submit(self, intent_key: str, target: Target, snapshot: dict, actor: str,
               native_id: int | None = None, adopt: bool = False) -> dict:
        if not isinstance(intent_key, str) or not 1 <= len(intent_key) <= 256:
            raise ValueError("intent key must contain 1..256 characters")
        if native_id is not None and (type(native_id) is not int or native_id <= 0 or not adopt):
            raise ValueError("explicit adoption of a positive native ID required")
        snapshot = snapshot_config(snapshot)
        fingerprint = hashlib.sha256(json.dumps([target.key, native_id, snapshot], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.connection(write=True) as db:
            intent = db.execute("SELECT * FROM intents WHERE intent_key=?", (intent_key,)).fetchone()
            if intent:
                if intent["fingerprint"] != fingerprint:
                    raise ValueError("idempotency key reused with different input")
                return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (intent["task_id"],)).fetchone())
            row = db.execute("SELECT * FROM tasks WHERE target_key=?", (target.key,)).fetchone()
            if row and native_id is not None and row["native_id"] != native_id:
                raise ValueError("target already belongs to another native subscription")
            now = utcnow()
            if row is None:
                cursor = db.execute("INSERT INTO tasks(target_key,media_type,media_source,media_id,season,episode_group,state,native_id,snapshot,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,'PENDING',?,?,?,?,?)",
                                    (target.key, target.media_type, target.media_source, target.media_id, target.season, target.episode_group, native_id, json.dumps(snapshot, ensure_ascii=False), actor, now, now))
                task_id = cursor.lastrowid
                db.execute("INSERT INTO outbox(task_id,operation,state,updated_at) VALUES(?,'HANDOFF','PENDING',?)", (task_id, now))
                self._audit(db, task_id, "INTENT_REGISTERED", actor)
            else:
                task_id = row["id"]
            db.execute("INSERT INTO intents VALUES(?,?,?,?)", (intent_key, fingerprint, task_id, now))
            return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def get_task(self, task_id: int) -> dict | None:
        with self.connection() as db:
            return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def list_tasks(self, limit: int = 100, offset: int = 0) -> list[dict]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("invalid pagination")
        with self.connection() as db:
            return [self._task(row) for row in db.execute("SELECT * FROM tasks ORDER BY id LIMIT ? OFFSET ?", (limit, offset))]

    def owned(self, target: Target) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT state FROM tasks WHERE target_key=?", (target.key,)).fetchone()
            return bool(row and row["state"] != "RELEASED_NATIVE")

    def by_native_id(self, native_id: int) -> dict | None:
        with self.connection() as db:
            return self._task(db.execute("SELECT * FROM tasks WHERE native_id=?", (native_id,)).fetchone())

    def set_state(self, task_id: int, state: str, actor: str) -> dict:
        if state not in {"PAUSED", "PASSIVE", "STOPPED"}:
            raise ValueError("use verified handoff/release for active states")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["state"] == "RELEASED_NATIVE":
                raise ValueError("task not managed")
            if row["state"] == state:
                return self._task(row)
            if row["state"] == "STOPPED" and state != "STOPPED":
                raise ValueError("explicit stopped task cannot be reactivated")
            db.execute("UPDATE tasks SET state=?,generation=generation+1,updated_at=? WHERE id=?", (state, utcnow(), task_id))
            db.execute("UPDATE outbox SET state='CANCELLED',updated_at=? WHERE task_id=? AND state!='DONE'", (utcnow(), task_id))
            self._audit(db, task_id, state, actor)
            return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def pending_actions(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM outbox WHERE state IN ('PENDING','UNKNOWN') ORDER BY id LIMIT ?", (limit,))]

    def setting(self, key: str, value: Any = None) -> Any:
        with self.connection(write=value is not None) as db:
            if value is not None:
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def action_state(self, task_id: int, state: str, error_code: str | None = None):
        with self.connection(write=True) as db:
            db.execute("UPDATE outbox SET state=?,error_code=?,updated_at=? WHERE task_id=? AND state!='CANCELLED'", (state, error_code, utcnow(), task_id))

    def start_create(self, task_id: int) -> bool:
        with self.connection(write=True) as db:
            return db.execute("UPDATE outbox SET state='UNKNOWN',error_code='CREATE_OUTCOME_UNKNOWN',updated_at=? WHERE task_id=? AND state='PENDING' AND task_id IN (SELECT id FROM tasks WHERE native_id IS NULL AND state='PENDING')", (utcnow(), task_id)).rowcount == 1

    def bind_native(self, task_id: int, native_id: int):
        with self.connection(write=True) as db:
            db.execute("UPDATE tasks SET native_id=?,updated_at=? WHERE id=? AND native_id IS NULL", (native_id, utcnow(), task_id))
            self._audit(db, task_id, "NATIVE_ID_RETURNED", "host")

    def complete_handoff(self, task_id: int, generation: int):
        with self.connection(write=True) as db:
            changed = db.execute("UPDATE tasks SET state='ACTIVE',updated_at=? WHERE id=? AND state='PENDING' AND generation=?", (utcnow(), task_id, generation)).rowcount
            if changed:
                db.execute("UPDATE outbox SET state='DONE',error_code=NULL,updated_at=? WHERE task_id=?", (utcnow(), task_id))
                self._audit(db, task_id, "HANDOFF_VERIFIED", "host")

    def begin_release(self, task_id: int, generation: int, actor: str):
        with self.connection(write=True) as db:
            changed = db.execute("UPDATE tasks SET state='RELEASING',generation=generation+1,updated_at=? WHERE id=? AND generation=? AND native_id IS NOT NULL AND state NOT IN ('RELEASED_NATIVE','RELEASING')", (utcnow(), task_id, generation)).rowcount
            if not changed:
                raise ValueError("release preview is stale")
            db.execute("INSERT INTO outbox(task_id,operation,state,updated_at) VALUES(?,'RELEASE','PENDING',?) ON CONFLICT(task_id) DO UPDATE SET operation='RELEASE',state='PENDING',error_code=NULL,updated_at=excluded.updated_at", (task_id, utcnow()))
            self._audit(db, task_id, "RELEASE_REQUESTED", actor)

    def complete_release(self, task_id: int, generation: int):
        with self.connection(write=True) as db:
            changed = db.execute("UPDATE tasks SET state='RELEASED_NATIVE',updated_at=? WHERE id=? AND state='RELEASING' AND generation=?", (utcnow(), task_id, generation)).rowcount
            if changed:
                db.execute("UPDATE outbox SET state='DONE',error_code=NULL,updated_at=? WHERE task_id=?", (utcnow(), task_id))
                self._audit(db, task_id, "RELEASE_VERIFIED", "host")

    def recover_native(self, task_id: int, native_id: int, actor: str, snapshot: dict):
        with self.connection(write=True) as db:
            action = db.execute("SELECT * FROM outbox WHERE task_id=?", (task_id,)).fetchone()
            if not action or action["state"] != "UNKNOWN" or action["operation"] != "HANDOFF":
                raise ValueError("no unresolved native creation")
            if db.execute("SELECT id FROM tasks WHERE native_id=?", (native_id,)).fetchone():
                raise ValueError("native subscription already owned")
            changed = db.execute("UPDATE tasks SET native_id=?,snapshot=?,updated_at=? WHERE id=? AND native_id IS NULL AND state='PENDING'",
                                 (native_id, json.dumps(snapshot_config(snapshot), ensure_ascii=False), utcnow(), task_id)).rowcount
            if not changed:
                raise ValueError("recovery state changed")
            db.execute("UPDATE outbox SET state='PENDING',error_code=NULL,updated_at=? WHERE task_id=?", (utcnow(), task_id))
            self._audit(db, task_id, "OPERATOR_ADOPTED_UNKNOWN_CREATE", actor)
