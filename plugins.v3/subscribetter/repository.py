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
            if revision > 6:
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
            if revision < 2:
                db.execute("CREATE TABLE parse_revisions (revision TEXT PRIMARY KEY, rules TEXT NOT NULL, created_at TEXT NOT NULL)")
                db.execute("CREATE TABLE parse_samples (sample_key TEXT PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), inputs TEXT NOT NULL, native TEXT NOT NULL, result TEXT NOT NULL, revision TEXT NOT NULL REFERENCES parse_revisions(revision), replay_allowed INTEGER NOT NULL, updated_at TEXT NOT NULL)")
                db.execute("CREATE TABLE parse_history (sample_key TEXT NOT NULL REFERENCES parse_samples(sample_key), digest TEXT NOT NULL, revision TEXT NOT NULL REFERENCES parse_revisions(revision), record TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(sample_key,digest))")
                db.execute("PRAGMA user_version=2")
            if revision < 3:
                for statement in (
                    "CREATE TABLE target_units (target_key TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), identity TEXT NOT NULL, owner_plan_id TEXT, generation INTEGER NOT NULL DEFAULT 0, publish_phase TEXT NOT NULL DEFAULT 'NOT_SENT', publish_action_id TEXT, current_revision INTEGER NOT NULL DEFAULT 0, current_facts TEXT, last_ingest_confirmed_at TEXT, cooldown_until TEXT)",
                    "CREATE TABLE opportunities (id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), scope TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, config TEXT NOT NULL, supersessions INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE opportunity_targets (opportunity_id TEXT NOT NULL REFERENCES opportunities(id), target_key TEXT NOT NULL REFERENCES target_units(target_key), fulfilled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(opportunity_id,target_key))",
                    "CREATE TABLE observations (opportunity_id TEXT NOT NULL REFERENCES opportunities(id), target_key TEXT NOT NULL REFERENCES target_units(target_key), first_seen TEXT NOT NULL, last_better TEXT NOT NULL, best_key TEXT NOT NULL, best_quality TEXT NOT NULL, deadline TEXT NOT NULL, PRIMARY KEY(opportunity_id,target_key))",
                    "CREATE TABLE plans (id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunities(id), task_id INTEGER NOT NULL REFERENCES tasks(id), snapshot TEXT NOT NULL, authorization TEXT NOT NULL, transfer_phase TEXT NOT NULL, created_at TEXT NOT NULL)",
                    "CREATE TABLE plan_targets (plan_id TEXT NOT NULL REFERENCES plans(id), target_key TEXT NOT NULL REFERENCES target_units(target_key), generation INTEGER, state TEXT NOT NULL, action TEXT NOT NULL, superseded_by TEXT, reason TEXT, transfer_phase TEXT NOT NULL DEFAULT 'PENDING', PRIMARY KEY(plan_id,target_key))",
                    "CREATE TABLE plan_actions (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id), kind TEXT NOT NULL, targets TEXT NOT NULL, files TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE action_receipts (id INTEGER PRIMARY KEY, action_id TEXT NOT NULL REFERENCES plan_actions(id), outcome TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(action_id,outcome,evidence))",
                    "CREATE TABLE ingest_receipts (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id), target_key TEXT NOT NULL REFERENCES target_units(target_key), generation INTEGER NOT NULL, version_id TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL)",
                    "CREATE TABLE evidence_consumption (evidence_key TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES ingest_receipts(id))",
                    "CREATE TABLE task_lifecycle (task_id INTEGER PRIMARY KEY REFERENCES tasks(id), config TEXT NOT NULL, scope TEXT NOT NULL, scope_closed INTEGER NOT NULL DEFAULT 0, complete_collected_at TEXT, last_ingest_at TEXT, expires_at TEXT, state TEXT NOT NULL DEFAULT 'ACTIVE')",
                    "CREATE TABLE plan_progress (plan_id TEXT NOT NULL REFERENCES plans(id), files TEXT NOT NULL, sample TEXT NOT NULL, PRIMARY KEY(plan_id,files))",
                    "CREATE TABLE opportunity_failures (id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL REFERENCES opportunities(id), reason TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL)",
                    "CREATE INDEX active_plan_targets ON plan_targets(target_key,state)",
                    "CREATE INDEX plan_actions_pending ON plan_actions(state,id)",
                    "CREATE INDEX opportunity_task_state ON opportunities(task_id,state)",
                    "PRAGMA user_version=3",
                ):
                    db.execute(statement)
            if revision < 4:
                # Legacy plans lack proof of the task generation at authorization.
                # Preserve their receipts/barriers; require explicit new authorization.
                db.execute("ALTER TABLE plans ADD COLUMN task_generation INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE plan_actions ADD COLUMN task_generation INTEGER NOT NULL DEFAULT 0")
                db.execute("PRAGMA user_version=4")
            if revision < 5:
                db.execute("CREATE TABLE candidates (candidate_key TEXT PRIMARY KEY, data TEXT NOT NULL, first_seen TEXT NOT NULL, updated_at TEXT NOT NULL)")
                db.execute("CREATE TABLE managed_downloads (downloader TEXT NOT NULL, infohash TEXT NOT NULL, save_path TEXT NOT NULL, file_table TEXT NOT NULL, marker TEXT NOT NULL, add_action TEXT NOT NULL, client_id TEXT, state TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL, PRIMARY KEY(downloader,infohash))")
                db.execute("CREATE TABLE exclusions (id TEXT PRIMARY KEY, criteria TEXT NOT NULL, reason TEXT NOT NULL, expires_at TEXT, active INTEGER NOT NULL DEFAULT 1)")
                db.execute("CREATE TABLE organized_assets (plan_id TEXT NOT NULL REFERENCES plans(id), file_index INTEGER NOT NULL, source TEXT NOT NULL, destination TEXT, size INTEGER NOT NULL, sha256 TEXT, state TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(plan_id,file_index))")
                db.execute("PRAGMA user_version=5")
            if revision < 6:
                # Evidence enrichment has no transfer plan. Keep the existing receipt
                # and consumption domain; only the plan reference becomes optional.
                db.execute('CREATE TEMP TABLE kept_evidence AS SELECT * FROM evidence_consumption')
                db.execute('DROP TABLE evidence_consumption')
                db.execute('CREATE TABLE ingest_receipts_v6 (id TEXT PRIMARY KEY, plan_id TEXT REFERENCES plans(id), target_key TEXT NOT NULL REFERENCES target_units(target_key), generation INTEGER NOT NULL, version_id TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL)')
                db.execute('INSERT INTO ingest_receipts_v6 SELECT * FROM ingest_receipts')
                db.execute('DROP TABLE ingest_receipts')
                db.execute('ALTER TABLE ingest_receipts_v6 RENAME TO ingest_receipts')
                db.execute('CREATE TABLE evidence_consumption (evidence_key TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES ingest_receipts(id))')
                db.execute('INSERT INTO evidence_consumption SELECT * FROM kept_evidence')
                db.execute('DROP TABLE kept_evidence')
                for statement in (
                    "CREATE TABLE archive_targets (target_key TEXT PRIMARY KEY, state TEXT NOT NULL, revision TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL)",
                    "CREATE TABLE archive_versions (id TEXT PRIMARY KEY, target_key TEXT NOT NULL REFERENCES archive_targets(target_key), service TEXT NOT NULL, library TEXT NOT NULL, active INTEGER NOT NULL, data TEXT NOT NULL)",
                    "CREATE INDEX archive_version_target ON archive_versions(target_key,active)",
                    "CREATE TABLE archive_contents (id TEXT PRIMARY KEY, sha1 TEXT NOT NULL, size INTEGER NOT NULL, UNIQUE(sha1,size))",
                    "CREATE TABLE archive_locations (id TEXT PRIMARY KEY, content_id TEXT NOT NULL REFERENCES archive_contents(id), scope TEXT NOT NULL, path TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)",
                    "CREATE INDEX archive_location_path ON archive_locations(scope,path)",
                    "CREATE TABLE archive_assets (version_id TEXT NOT NULL REFERENCES archive_versions(id), file_index INTEGER NOT NULL, location_id TEXT NOT NULL REFERENCES archive_locations(id), data TEXT NOT NULL, PRIMARY KEY(version_id,file_index,location_id))",
                    "CREATE TABLE archive_sources (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES archive_versions(id), data TEXT NOT NULL, at TEXT NOT NULL)",
                    "CREATE TABLE archive_scans (id TEXT PRIMARY KEY, service TEXT NOT NULL, library TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)",
                    "CREATE TABLE archive_scan_items (scan_id TEXT NOT NULL REFERENCES archive_scans(id), item_id TEXT NOT NULL, data TEXT NOT NULL, resolved TEXT, PRIMARY KEY(scan_id,item_id))",
                    "PRAGMA user_version=6",
                ):
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
            if native_id is not None:
                owner = db.execute("SELECT id FROM tasks WHERE native_id=?", (native_id,)).fetchone()
                if owner and (row is None or owner["id"] != row["id"]):
                    raise ValueError("native subscription already owned")
            if row and native_id is not None and row["native_id"] != native_id:
                action = db.execute("SELECT * FROM outbox WHERE task_id=?", (row["id"],)).fetchone()
                if (row["native_id"] is not None or row["state"] != "PENDING" or not action
                        or action["operation"] != "HANDOFF" or action["state"] != "PENDING"):
                    raise ValueError("target cannot be adopted in its current state")
                db.execute("UPDATE tasks SET native_id=?,snapshot=?,generation=generation+1,updated_at=? WHERE id=?",
                           (native_id, json.dumps(snapshot, ensure_ascii=False), utcnow(), row["id"]))
                db.execute("UPDATE outbox SET error_code=NULL,updated_at=? WHERE task_id=?", (utcnow(), row["id"]))
                self._audit(db, row["id"], "EXPLICIT_NATIVE_ADOPTED", actor)
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
            if state == "STOPPED":
                self._cancel_plans(db, task_id, "USER_STOPPED")
            db.execute("UPDATE tasks SET state=?,generation=generation+1,updated_at=? WHERE id=?", (state, utcnow(), task_id))
            db.execute("UPDATE outbox SET state='CANCELLED',updated_at=? WHERE task_id=? AND state!='DONE'", (utcnow(), task_id))
            self._audit(db, task_id, state, actor)
            return self._task(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def pending_actions(self, limit: int = 100, after_id: int = 0, through_id: int | None = None) -> list[dict]:
        if not 1 <= limit <= 1000 or after_id < 0:
            raise ValueError("invalid action pagination")
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM outbox WHERE state IN ('PENDING','UNKNOWN') AND id>? AND (? IS NULL OR id<=?) ORDER BY id LIMIT ?", (after_id, through_id, through_id, limit))]

    def action_high_watermark(self) -> int:
        with self.connection() as db:
            return db.execute("SELECT COALESCE(MAX(id),0) FROM outbox").fetchone()[0]

    def get_action(self, task_id: int) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM outbox WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def setting(self, key: str, value: Any = None) -> Any:
        with self.connection(write=value is not None) as db:
            if value is not None:
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_parse_sample(self, key: str, data: dict, rules: dict, task_id: int | None, replay_allowed: bool):
        revision = data["result"]["revision"]
        with self.connection(write=True) as db:
            existing = db.execute("SELECT task_id FROM parse_samples WHERE sample_key=?", (key,)).fetchone()
            if existing and existing[0] != task_id:
                raise ValueError("sample belongs to another task")
            db.execute("INSERT OR IGNORE INTO parse_revisions VALUES(?,?,?)", (revision, json.dumps(rules, ensure_ascii=False), utcnow()))
            db.execute("INSERT INTO parse_samples VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(sample_key) DO UPDATE SET inputs=excluded.inputs,native=excluded.native,result=excluded.result,revision=excluded.revision,replay_allowed=excluded.replay_allowed,updated_at=excluded.updated_at",
                       (key, task_id, json.dumps(data["inputs"], ensure_ascii=False), json.dumps(data["native"], ensure_ascii=False),
                        json.dumps(data["result"], ensure_ascii=False), revision, int(replay_allowed), utcnow()))
            record = json.dumps(data, ensure_ascii=False, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO parse_history VALUES(?,?,?,?,?)", (key, hashlib.sha256(record.encode()).hexdigest(), revision, record, utcnow()))

    def parse_history(self, key: str, limit: int = 100, offset: int = 0) -> list[dict]:
        if not isinstance(key, str) or not 1 <= len(key) <= 256 or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid parse history selection")
        with self.connection() as db:
            return [dict(revision=row[0], record=json.loads(row[1]), created_at=row[2]) for row in db.execute(
                "SELECT revision,record,created_at FROM parse_history WHERE sample_key=? ORDER BY created_at,digest LIMIT ? OFFSET ?", (key, limit, offset))]

    def parse_samples(self, keys: list[str] | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0 or (keys is not None and (not 1 <= len(keys) <= 100 or any(not isinstance(k, str) or not 1 <= len(k) <= 256 for k in keys))):
            raise ValueError("invalid parse sample selection")
        query = "SELECT p.*,t.state AS task_state FROM parse_samples p LEFT JOIN tasks t ON t.id=p.task_id"
        args = []
        if keys is not None:
            query += " WHERE p.sample_key IN (" + ",".join("?" for _ in keys) + ")"
            args.extend(keys)
        query += " ORDER BY p.sample_key LIMIT ? OFFSET ?"
        with self.connection() as db:
            rows = [dict(row) for row in db.execute(query, (*args, limit, offset))]
        for row in rows:
            for field in ("inputs", "native", "result"):
                row[field] = json.loads(row[field])
        return rows

    def action_state(self, task_id: int, state: str, error_code: str | None = None,
                     expected_state: str | None = None):
        with self.connection(write=True) as db:
            db.execute("UPDATE outbox SET state=?,error_code=?,updated_at=? WHERE task_id=? AND state!='CANCELLED' AND (? IS NULL OR state=?)",
                       (state, error_code, utcnow(), task_id, expected_state, expected_state))

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

    @staticmethod
    def _cancel_plans(db, task_id, reason):
        # Unresolved external publication remains a barrier even after user stop.
        db.execute("UPDATE plan_targets SET state='CANCELLED',reason=? WHERE state='ACTIVE' AND plan_id IN (SELECT id FROM plans WHERE task_id=?)", (reason, task_id))
        db.execute("UPDATE plans SET authorization='CANCELLED' WHERE task_id=? AND authorization IN ('PREPARED','ACTIVE')", (task_id,))
        db.execute("UPDATE plan_actions SET state='CANCELLED' WHERE state='PENDING' AND plan_id IN (SELECT id FROM plans WHERE task_id=?)", (task_id,))
        db.execute("UPDATE target_units SET owner_plan_id=NULL,generation=generation+1 WHERE task_id=? AND publish_phase NOT IN ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') AND owner_plan_id IS NOT NULL", (task_id,))
        db.execute("UPDATE opportunities SET state='CANCELLED' WHERE task_id=? AND state='ACTIVE'", (task_id,))

    def begin_release(self, task_id: int, generation: int, actor: str):
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM target_units WHERE task_id=? AND publish_phase IN ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF') LIMIT 1", (task_id,)).fetchone():
                raise ValueError("unresolved publication blocks native release")
            changed = db.execute("UPDATE tasks SET state='RELEASING',generation=generation+1,updated_at=? WHERE id=? AND generation=? AND native_id IS NOT NULL AND state NOT IN ('RELEASED_NATIVE','RELEASING')", (utcnow(), task_id, generation)).rowcount
            if not changed:
                raise ValueError("release preview is stale")
            db.execute("INSERT INTO outbox(task_id,operation,state,updated_at) VALUES(?,'RELEASE','PENDING',?) ON CONFLICT(task_id) DO UPDATE SET operation='RELEASE',state='PENDING',error_code=NULL,updated_at=excluded.updated_at", (task_id, utcnow()))
            self._cancel_plans(db, task_id, "NATIVE_RELEASE")
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
