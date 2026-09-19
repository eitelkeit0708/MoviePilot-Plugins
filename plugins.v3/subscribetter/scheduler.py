"""Persistent target clocks. The host scheduler owns invocation; no worker threads."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math


def instant(value=None):
    value = value if value is not None else datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


def stamp(value=None):
    return instant(value).isoformat(timespec='microseconds')


def parse(value):
    return instant(datetime.fromisoformat(value))


def duration(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(name + ' must be a finite nonnegative duration')
    return value


@dataclass(frozen=True)
class ScheduleConfig:
    observation_enabled: bool = False
    base_seconds: float | None = None
    quiet_seconds: float | None = None
    max_seconds: float | None = None
    goal_early_enabled: bool = False
    cooldown_enabled: bool = False
    cooldown_seconds: float | None = None
    supersession_limit: int = 0
    supersession_seconds: float | None = None
    failure_limit: int = 0
    waiting_transfer_preemption: bool = True
    normal_download_preemption: bool = False
    max_downloaded_bytes: int | None = None
    min_remaining_seconds: float | None = None

    def __post_init__(self):
        for name in ('observation_enabled', 'goal_early_enabled', 'cooldown_enabled',
                     'waiting_transfer_preemption', 'normal_download_preemption'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('invalid switch: ' + name)
        for name in ('supersession_limit', 'failure_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError('invalid budget: ' + name)
        if self.observation_enabled:
            for name in ('base_seconds', 'quiet_seconds', 'max_seconds'):
                duration(getattr(self, name), name)
            if not 0 <= self.base_seconds <= self.max_seconds or self.max_seconds == 0:
                raise ValueError('require 0 <= base <= max and max > 0')
        if self.cooldown_enabled:
            duration(self.cooldown_seconds, 'cooldown_seconds')
        if self.supersession_limit:
            if duration(self.supersession_seconds, 'supersession_seconds') == 0:
                raise ValueError('positive supersession horizon required')
        if self.normal_download_preemption:
            if type(self.max_downloaded_bytes) is not int or self.max_downloaded_bytes < 0:
                raise ValueError('explicit file byte cost limit required')
            duration(self.min_remaining_seconds, 'min_remaining_seconds')


UPGRADES = {'QUALITY_UPGRADE', 'EVIDENCE_UPGRADE'}
ACTIONS = UPGRADES | {'ACQUIRE', 'REPLACE_INVALID', 'SIDECAR_SUPPLEMENT', 'UNCHANGED'}


def readiness(db, opportunity, target_key, action, now, *, immediate=False, goal_reached=False, approved=False):
    if action not in ACTIONS or type(immediate) is not bool or type(goal_reached) is not bool:
        raise ValueError('invalid scheduling input')
    config = ScheduleConfig(**json.loads(opportunity['config']))
    target = db.execute('SELECT * FROM target_units WHERE target_key=?', (target_key,)).fetchone()
    task = db.execute('SELECT state FROM tasks WHERE id=?', (opportunity['task_id'],)).fetchone()
    if not target or not task or task['state'] not in ('ACTIVE', 'PASSIVE') or opportunity['state'] != 'ACTIVE':
        return {'ready': False, 'reason': 'TASK_NOT_ACTIVE', 'earliest': None}
    lifecycle = db.execute('SELECT state FROM task_lifecycle WHERE task_id=?', (opportunity['task_id'],)).fetchone()
    if lifecycle and lifecycle['state'] == 'EXPIRED' and opportunity['mode'] == 'CONTINUOUS' and action in UPGRADES and not approved:
        return {'ready': False, 'reason': 'ACTIVE_UPGRADE_EXPIRED', 'earliest': None}
    if immediate or (config.goal_early_enabled and goal_reached):
        return {'ready': True, 'reason': 'EXPLICIT_TIME_BYPASS', 'earliest': stamp(now)}
    times = []
    if config.observation_enabled and action != 'UNCHANGED':
        row = db.execute('SELECT deadline FROM observations WHERE opportunity_id=? AND target_key=?', (opportunity['id'], target_key)).fetchone()
        if not row:
            return {'ready': False, 'reason': 'NO_ELIGIBLE_OBSERVATION', 'earliest': None}
        times.append((parse(row['deadline']), 'OBSERVATION_PENDING'))
    if action in UPGRADES and target['cooldown_until'] and not approved:
        times.append((parse(target['cooldown_until']), 'UPGRADE_COOLDOWN'))
    deadline, reason = max(times, default=(instant(now), 'READY'))
    return {'ready': instant(now) >= deadline, 'reason': 'READY' if instant(now) >= deadline else reason, 'earliest': stamp(deadline)}


class Scheduler:
    def __init__(self, repository):
        self.repository = repository

    def open_opportunity(self, opportunity_id, task_id, units, *, mode, config, now=None):
        if not isinstance(opportunity_id, str) or not 1 <= len(opportunity_id) <= 256 or mode not in ('CONTINUOUS', 'ONESHOT'):
            raise ValueError('invalid opportunity')
        if not isinstance(config, ScheduleConfig) or not 1 <= len(units) <= 10000:
            raise ValueError('explicit schedule config and nonempty scope required')
        scope = sorted(unit.key for unit in units)
        if len(scope) != len(set(scope)):
            raise ValueError('duplicate target')
        encoded = json.dumps(asdict(config), sort_keys=True)
        with self.repository.connection(write=True) as db:
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task or task['state'] not in ('ACTIVE', 'PASSIVE'):
                raise ValueError('task is not active')
            if any(unit.target.key != task['target_key'] for unit in units):
                raise ValueError('scope does not belong to task')
            existing = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if existing:
                if (existing['task_id'], json.loads(existing['scope']), existing['mode'], existing['config']) != (task_id, scope, mode, encoded):
                    raise ValueError('opportunity id reused with different frozen scope/config')
                return dict(existing)
            # Same business scope merges repeated RSS/site arrivals without resetting budgets.
            for row in db.execute("SELECT * FROM opportunities WHERE task_id=? AND state='ACTIVE'", (task_id,)):
                old = json.loads(row['scope'])
                if set(old) & set(scope):
                    if old == scope:
                        return dict(row)
                    raise ValueError('overlapping active opportunity requires explicit merge/queue')
            at = stamp(now)
            db.execute("INSERT INTO opportunities(id,task_id,scope,mode,state,config,created_at,updated_at) VALUES(?,?,?,?,'ACTIVE',?,?,?)", (opportunity_id, task_id, json.dumps(scope), mode, encoded, at, at))
            for unit in units:
                db.execute('INSERT OR IGNORE INTO target_units(target_key,task_id,identity) VALUES(?,?,?)', (unit.key, task_id, unit.key))
                db.execute('INSERT INTO opportunity_targets(opportunity_id,target_key) VALUES(?,?)', (opportunity_id, unit.key))
            self.repository._audit(db, task_id, 'OPPORTUNITY_OPENED:' + opportunity_id, 'scheduler')
            return dict(db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone())

    def opportunity(self, opportunity_id):
        with self.repository.connection() as db:
            row = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            return dict(row) if row else None

    def target(self, key):
        with self.repository.connection() as db:
            row = db.execute('SELECT * FROM target_units WHERE target_key=?', (key,)).fetchone()
            return dict(row) if row else None

    def observe(self, opportunity_id, target_key, candidate_key, quality, *, eligible, now=None):
        if eligible is not True or not isinstance(candidate_key, str) or not 1 <= len(candidate_key) <= 256:
            raise ValueError('only an eligible valuable candidate starts observation')
        if not quality or len(quality) > 16 or any(type(v) not in (bool, int, float) or not math.isfinite(v) for v in quality):
            raise ValueError('known policy quality/evidence vector required')
        at = instant(now)
        with self.repository.connection(write=True) as db:
            opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if not opportunity or opportunity['state'] != 'ACTIVE' or not db.execute('SELECT 1 FROM opportunity_targets WHERE opportunity_id=? AND target_key=?', (opportunity_id, target_key)).fetchone():
                raise ValueError('target outside active frozen opportunity')
            config = ScheduleConfig(**json.loads(opportunity['config']))
            old = db.execute('SELECT * FROM observations WHERE opportunity_id=? AND target_key=?', (opportunity_id, target_key)).fetchone()
            first = parse(old['first_seen']) if old else at
            if at < first:
                raise ValueError('observation clock moved backwards')
            better = not old or tuple(quality) > tuple(json.loads(old['best_quality']))
            last = max(at, parse(old['last_better'])) if better and old else at if better else parse(old['last_better'])
            best = candidate_key if better else min(candidate_key, old['best_key']) if tuple(quality) == tuple(json.loads(old['best_quality'])) else old['best_key']
            quality = quality if better else json.loads(old['best_quality'])
            deadline = min(first + timedelta(seconds=config.max_seconds), max(first + timedelta(seconds=config.base_seconds), last + timedelta(seconds=config.quiet_seconds))) if config.observation_enabled else first
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(opportunity_id,target_key) DO UPDATE SET last_better=excluded.last_better,best_key=excluded.best_key,best_quality=excluded.best_quality,deadline=excluded.deadline', (opportunity_id, target_key, stamp(first), stamp(last), best, json.dumps(quality), stamp(deadline)))
            return dict(db.execute('SELECT * FROM observations WHERE opportunity_id=? AND target_key=?', (opportunity_id, target_key)).fetchone())

    def ready(self, opportunity_id, target_key, action, *, now=None, immediate=False, goal_reached=False):
        with self.repository.connection() as db:
            opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if not opportunity:
                raise ValueError('opportunity missing')
            return readiness(db, opportunity, target_key, action, instant(now), immediate=immediate, goal_reached=goal_reached)


    def record_failure(self, opportunity_id, failure_id, reason, *, now=None):
        if not isinstance(failure_id, str) or not 1 <= len(failure_id) <= 256 or reason not in ('CONFIRMED_BAD_RESOURCE', 'CONFIRMED_NO_PROGRESS', 'EXECUTION_FAILED'):
            raise ValueError('confirmed failure required; pause/queue/checking/disconnect is not failure')
        with self.repository.connection(write=True) as db:
            old = db.execute('SELECT * FROM opportunity_failures WHERE id=?', (failure_id,)).fetchone()
            if old:
                if (old['opportunity_id'], old['reason']) != (opportunity_id, reason):
                    raise ValueError('failure key reused')
                return dict(old)
            opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if not opportunity or opportunity['state'] != 'ACTIVE':
                raise ValueError('opportunity not active')
            config = ScheduleConfig(**json.loads(opportunity['config']))
            if opportunity['failures'] >= config.failure_limit:
                raise ValueError('failure recovery budget exhausted')
            db.execute('INSERT INTO opportunity_failures(id,opportunity_id,reason,at) VALUES(?,?,?,?)', (failure_id, opportunity_id, reason, stamp(now)))
            db.execute('UPDATE opportunities SET failures=failures+1,updated_at=? WHERE id=?', (stamp(now), opportunity_id))
            return dict(db.execute('SELECT * FROM opportunity_failures WHERE id=?', (failure_id,)).fetchone())

    def configure_lifecycle(self, task_id, scope, *, movie_days, tv_days, anchor, now=None):
        duration(movie_days, 'movie_days')
        duration(tv_days, 'tv_days')
        if anchor not in ('COMPLETE_COLLECTED', 'LAST_INGEST') or not scope or len(scope) != len(set(scope)):
            raise ValueError('explicit expiry anchor and scope required')
        config = json.dumps(dict(movie_days=movie_days, tv_days=tv_days, anchor=anchor), sort_keys=True)
        with self.repository.connection(write=True) as db:
            if not db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise ValueError('task missing')
            if any(not db.execute('SELECT 1 FROM target_units WHERE target_key=? AND task_id=?', (key, task_id)).fetchone() for key in scope):
                raise ValueError('lifecycle scope outside task')
            row = db.execute('SELECT * FROM task_lifecycle WHERE task_id=?', (task_id,)).fetchone()
            if row and json.loads(row['scope']) != sorted(scope):
                raise ValueError('scope change requires explicit new completion evidence')
            db.execute("INSERT INTO task_lifecycle(task_id,config,scope) VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET config=excluded.config", (task_id, config, json.dumps(sorted(scope))))
            self._refresh_lifecycle(db, task_id, now)

    @staticmethod
    def _refresh_lifecycle(db, task_id, now):
        row = db.execute('SELECT l.*,t.media_type FROM task_lifecycle l JOIN tasks t ON t.id=l.task_id WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            return
        config = json.loads(row['config'])
        days = config['movie_days' if row['media_type'] == '电影' else 'tv_days']
        anchor = row['complete_collected_at'] if config['anchor'] == 'COMPLETE_COLLECTED' else row['last_ingest_at']
        expires = parse(anchor) + timedelta(days=days) if days and anchor else None
        state = 'EXPIRED' if expires and instant(now) >= expires else 'ACTIVE'
        db.execute('UPDATE task_lifecycle SET expires_at=?,state=? WHERE task_id=?', (stamp(expires) if expires else None, state, task_id))

    def update_completion(self, task_id, scope, *, scope_closed, collected, now=None):
        if type(scope_closed) is not bool or type(collected) is not bool:
            raise ValueError('explicit verified completion facts required')
        with self.repository.connection(write=True) as db:
            row = db.execute('SELECT * FROM task_lifecycle WHERE task_id=?', (task_id,)).fetchone()
            if not row or json.loads(row['scope']) != sorted(scope):
                raise ValueError('completion must cover exact declared scope')
            # The first confirmed completed-and-collected anchor is immutable.
            db.execute('UPDATE task_lifecycle SET scope_closed=?,complete_collected_at=COALESCE(complete_collected_at,?) WHERE task_id=?', (int(scope_closed), stamp(now) if scope_closed and collected else None, task_id))
            self._refresh_lifecycle(db, task_id, now)

    def lifecycle(self, task_id):
        with self.repository.connection() as db:
            row = db.execute('SELECT * FROM task_lifecycle WHERE task_id=?', (task_id,)).fetchone()
            return dict(row) if row else None

    def tick(self, *, now=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('bounded lifecycle page required')
        with self.repository.connection(write=True) as db:
            cursor = db.execute("SELECT value FROM settings WHERE key='lifecycle_cursor'").fetchone()
            after = json.loads(cursor[0]) if cursor else 0
            rows = db.execute('SELECT task_id FROM task_lifecycle WHERE task_id>? ORDER BY task_id LIMIT ?', (after, limit)).fetchall()
            if not rows:
                rows = db.execute('SELECT task_id FROM task_lifecycle ORDER BY task_id LIMIT ?', (limit,)).fetchall()
            for row in rows:
                self._refresh_lifecycle(db, row['task_id'], now)
            db.execute("INSERT INTO settings VALUES('lifecycle_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(rows[-1]['task_id'] if rows else 0),))
            return {'checked': len(rows)}
