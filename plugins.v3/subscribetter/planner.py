"""Frozen file plans and the single SQLite target authority used by all executors.

An authorized attempt is a durable intent, not a remote fencing token. Callers
must dispatch only when dispatch=True, then query uncertain outcomes; never
repeat an already authorized external call merely because a worker lease died.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from hashlib import sha256
import json
import math
from pathlib import PurePosixPath
import re

from .repository import Target
from .scheduler import ACTIONS, ScheduleConfig, instant, parse, readiness, stamp

BARRIERS = {'PUBLISHING', 'PUBLISH_OUTCOME_UNKNOWN', 'HANDED_OFF'}
HISTORY_REPAIR_LIMIT = 3
TRANSFER_PHASES = {'PENDING', 'QUEUED', 'DOWNLOADING', 'WAITING_ASSETS', 'RAPID_WAIT',
                   'RAPID_IN_FLIGHT', 'CD2_UPLOADING', 'REMOTE_VERIFIED', 'READY_TO_PUBLISH', 'FAILED'}
ATTEMPT_KINDS = {'ADD', 'SET_WANTED', 'RESUME', 'RAPID', 'CD2_UPLOAD', 'ORGANIZE', 'REFRESH', 'PUBLISH'}
VIDEO_SUFFIXES = frozenset({'.mkv', '.mp4', '.avi', '.ts', '.m2ts', '.mov', '.wmv'})
TEXT_SUBTITLE_SUFFIXES = frozenset({'.ass', '.srt', '.ssa', '.vtt', '.sup'})


def encoded(value):
    result = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(result) > 8_000_000:
        raise ValueError('snapshot too large')
    return result


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or '\n' in value or '\r' in value:
        raise ValueError('bounded identifier required')
    return value


@dataclass(frozen=True)
class TargetUnit:
    target: Target
    episode: int | None = None

    def __post_init__(self):
        if self.target.media_type == '电影':
            if self.episode is not None:
                raise ValueError('movie must not invent an episode')
        elif type(self.episode) is not int or self.episode < 0:
            raise ValueError('TV target needs a nonnegative episode')

    @property
    def key(self):
        return encoded([*json.loads(self.target.key), self.episode])


def validate_files(files, selected):
    if not isinstance(files, list) or not 1 <= len(files) <= 10000 or not isinstance(selected, list) or not selected:
        raise ValueError('nonempty complete file table and exact selection required')
    by_index, paths = {}, set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {'index', 'path', 'size', 'role', 'targets', 'requires'}:
            raise ValueError('file index/path/bytes/role/targets/dependencies required')
        index, path, size = item['index'], item['path'], item['size']
        if type(index) is not int or index < 0 or index in by_index or type(size) is not int or size < 0:
            raise ValueError('invalid file index or size')
        if not isinstance(path, str) or not path or len(path) > 4096 or '\\' in path or '\x00' in path or ':' in path:
            raise ValueError('unsafe relative torrent path')
        parts = path.split('/')
        if PurePosixPath(path).is_absolute() or any(p in ('', '.', '..') for p in parts) or path in paths:
            raise ValueError('unsafe or duplicated torrent path')
        if item['role'] not in ('video', 'subtitle', 'attachment', 'other') or not isinstance(item['targets'], list) or len(item['targets']) != len(set(item['targets'])):
            raise ValueError('invalid file mapping')
        if not isinstance(item['requires'], list) or any(type(i) is not int for i in item['requires']):
            raise ValueError('invalid file dependency')
        paths.add(path)
        by_index[index] = item
    if any(type(i) is not int for i in selected) or len(selected) != len(set(selected)) or not set(selected) <= set(by_index):
        raise ValueError('invalid exact selection')
    for item in files:
        if not set(item['requires']) <= set(by_index):
            raise ValueError('unknown file dependency')
    for i in selected:
        if not set(by_index[i]['requires']) <= set(selected) or not by_index[i]['targets']:
            raise ValueError('selected file lacks dependency or target binding')
    return by_index


def validate_new_asset_scope(files):
    """Require canonical video/supported-subtitle bindings for new plans."""
    by_index = {item['index']: item for item in files}
    for item in files:
        suffix = PurePosixPath(item['path']).suffix.casefold()
        expected = 'video' if suffix in VIDEO_SUFFIXES else 'subtitle' if suffix in TEXT_SUBTITLE_SUFFIXES else 'other'
        if item['role'] != expected or (expected == 'other' and (item['targets'] or item['requires'])):
            raise ValueError('ASSET_SCOPE_REBIND_REQUIRED')
        if item['index'] in item['requires'] or any(by_index[index]['role'] == 'other' for index in item['requires']):
            raise ValueError('ASSET_SCOPE_REBIND_REQUIRED')
    return by_index


def asset_table(snapshot):
    """Execution assets include verified local sidecars; torrent metadata does not."""
    return snapshot['torrent_files']+[a['file'] for a in snapshot.get('local_assets',[])]


class Authority:
    def __init__(self, repository):
        self.repository = repository

    def set_revisions(self, policy_revision, parse_revision):
        """Publish the currently configured revisions before evaluating/dispatching work."""
        self.repository.setting('planner_revisions', [identifier(policy_revision), identifier(parse_revision)])

    @staticmethod
    def _revisions(db, snapshot):
        row = db.execute("SELECT value FROM settings WHERE key='planner_revisions'").fetchone()
        if not row or json.loads(row[0]) != [snapshot['policy_revision'], snapshot['parse_revision']]:
            raise ValueError('policy/parse revision changed; re-evaluate')

    def prepare(self, plan_id, opportunity_id, snapshot, *, now=None):
        identifier(plan_id)
        snapshot = json.loads(encoded(snapshot))
        required = {'candidate_key', 'infohash', 'downloader', 'save_path', 'policy_revision', 'parse_revision',
                    'current', 'targets', 'torrent_files', 'selected_indices', 'verified'}
        if set(snapshot) not in (required,required|{'local_assets','source_plan'}):
            raise ValueError('complete frozen execution snapshot required')
        if 'local_assets' in snapshot:
            identifier(snapshot['source_plan'])
            for n,asset in enumerate(snapshot['local_assets'],len(snapshot['torrent_files'])):
                if set(asset)!={'file','sha256','sha1','mtime_ns','stable_since','observed_at'} or asset['file']['index']!=n or asset['file']['role']!='subtitle' or not re.fullmatch('[a-f0-9]{64}',asset['sha256']) or not re.fullmatch('[a-f0-9]{40}',asset['sha1']):raise ValueError('LOCAL_ASSET_PROOF_REQUIRED')
                if parse(asset['observed_at'])<=parse(asset['stable_since']):raise ValueError('LOCAL_ASSET_STABILITY_REQUIRED')
        for name in ('candidate_key', 'downloader', 'policy_revision', 'parse_revision'):
            identifier(snapshot[name])
        if not isinstance(snapshot['infohash'], str) or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', snapshot['infohash']):
            raise ValueError('verified infohash required')
        snapshot['infohash'] = snapshot['infohash'].lower()
        if not isinstance(snapshot['save_path'], str) or not snapshot['save_path'].startswith('/') or '\x00' in snapshot['save_path'] or '..' in snapshot['save_path'].split('/'):
            raise ValueError('explicit absolute downloader layout required')
        if set(snapshot['verified']) != {'identity', 'scope', 'admission', 'files', 'configuration'} or any(v is not True for v in snapshot['verified'].values()):
            raise ValueError('candidate has not passed all preflight gates')
        files = validate_files(asset_table(snapshot), snapshot['selected_indices'])
        targets = snapshot['targets']
        covered = {key for i in snapshot['selected_indices'] for key in files[i]['targets']}
        if not isinstance(targets, dict) or not targets or set(targets) != covered or set(snapshot['current']) != covered:
            raise ValueError('plan must claim every selected physical file target')
        for key, item in targets.items():
            if item.get('action') not in ACTIONS or not item.get('reason') or not isinstance(item.get('evidence_keys'), list):
                raise ValueError('explicit action/reason/evidence required')
            quality = item.get('quality')
            if not isinstance(quality, list) or not quality or len(quality) > 16 or any(type(v) not in (int, float, bool) or not math.isfinite(v) for v in quality) or item.get('evidence_source') not in ('none', 'unknown', 'explicit', 'inferred_pgs'):
                raise ValueError('known policy quality and evidence source required')
            if snapshot['current'][key].get('state') not in ('PRESENT', 'MISSING', 'INVALID'):
                raise ValueError('unknown/error is not confirmed missing')
            if item['action'] == 'ACQUIRE' and snapshot['current'][key]['state'] != 'MISSING':
                raise ValueError('ACQUIRE requires confirmed missing fact')
            if item['action'] == 'REPLACE_INVALID' and snapshot['current'][key]['state'] != 'INVALID':
                raise ValueError('repair requires confirmed invalid fact')
        if all(t['action'] == 'UNCHANGED' for t in targets.values()):
            raise ValueError('plan has no improvement')
        with self.repository.connection(write=True) as db:
            if snapshot.get('local_assets'):
                source=db.execute('SELECT * FROM plans WHERE id=?',(snapshot['source_plan'],)).fetchone()
                if not source:raise ValueError('LOCAL_ASSET_SOURCE_PLAN_REQUIRED')
                before=json.loads(source['snapshot'])
                if any(before[k]!=snapshot[k] for k in ('candidate_key','infohash','torrent_files','downloader','save_path')) or not set(snapshot['targets'])<=set(before['targets']):raise ValueError('LOCAL_ASSET_SOURCE_CONFLICT')
            opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if not opportunity or opportunity['state'] != 'ACTIVE' or not covered <= set(json.loads(opportunity['scope'])):
                raise ValueError('plan outside active frozen opportunity')
            self._revisions(db, snapshot)
            text = encoded(snapshot)
            old = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if old:
                if old['snapshot'] != text or old['opportunity_id'] != opportunity_id:
                    raise ValueError('immutable plan id reused')
                return self._plan(old)
            validate_new_asset_scope(asset_table(snapshot))
            task_generation = db.execute('SELECT generation FROM tasks WHERE id=?', (opportunity['task_id'],)).fetchone()[0]
            db.execute("INSERT INTO plans(id,opportunity_id,task_id,snapshot,authorization,transfer_phase,created_at,task_generation) VALUES(?,?,?,?,'PREPARED','PENDING',?,?)", (plan_id, opportunity_id, opportunity['task_id'], text, stamp(now), task_generation))
            for key, action in targets.items():
                db.execute("INSERT INTO plan_targets(plan_id,target_key,state,action) VALUES(?,?,'PREPARED',?)", (plan_id, key, action['action']))
            self.repository._audit(db, opportunity['task_id'], 'PLAN_PREPARED:' + plan_id, 'planner')
            return self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())

    @staticmethod
    def _plan(row):
        if row is None:
            raise ValueError('plan missing')
        value = dict(row)
        value['snapshot'] = json.loads(value['snapshot'])
        return value

    def plan(self, plan_id):
        with self.repository.connection() as db:
            result = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
            result['targets'] = [dict(r) for r in db.execute('SELECT * FROM plan_targets WHERE plan_id=? ORDER BY target_key', (plan_id,))]
            return result

    @staticmethod
    def _vector(db, keys):
        result = {}
        for key in sorted(keys):
            row = db.execute('SELECT owner_plan_id,generation,publish_phase,current_revision FROM target_units WHERE target_key=?', (key,)).fetchone()
            if row is None:
                raise ValueError('unknown target')
            result[key] = dict(row)
        return result

    def vector(self, keys):
        if not keys or len(keys) != len(set(keys)) or len(keys) > 10000:
            raise ValueError('exact nonempty target set required')
        with self.repository.connection() as db:
            return self._vector(db, keys)

    @staticmethod
    def _match(db, vector, *, owner=None, allow_barrier=False):
        if not vector:
            raise ValueError('nonempty target vector required')
        current = Authority._vector(db, vector)
        for key, expected in vector.items():
            actual = current[key]
            if type(expected.get('generation')) is not int or (actual['owner_plan_id'], actual['generation']) != (expected.get('owner_plan_id'), expected['generation']):
                raise ValueError('stale all-target authority vector')
            if owner is not None:
                row = db.execute('SELECT state FROM plan_targets WHERE plan_id=? AND target_key=? AND generation=?', (owner, key, actual['generation'])).fetchone()
                if actual['owner_plan_id'] != owner or not row or row['state'] != 'ACTIVE':
                    raise ValueError('plan no longer owns this target')
            if not allow_barrier and actual['publish_phase'] in BARRIERS:
                raise ValueError('unresolved publish barrier')
        return current

    @staticmethod
    def _task_active(db, plan):
        task = db.execute('SELECT state,generation FROM tasks WHERE id=?', (plan['task_id'],)).fetchone()
        if not task or task['state'] not in ('ACTIVE', 'PASSIVE'):
            raise ValueError('task stopped/paused/released')
        if plan['task_generation'] != task['generation']:
            raise ValueError('task execution generation changed; explicit new plan required')

    def _acquire(self, db, plan_id, expected, *, replacement, reason, safe_isolation, now, immediate, progress, failure_id=None):
        plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
        self._download_not_cleaning(db,plan['snapshot'])
        if plan['authorization'] != 'PREPARED' or set(expected) != set(plan['snapshot']['targets']):
            raise ValueError('new frozen plan and complete expected vector required')
        self._task_active(db, plan)
        self._revisions(db, plan['snapshot'])
        actual = self._match(db, expected)
        opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (plan['opportunity_id'],)).fetchone()
        config = ScheduleConfig(**json.loads(opportunity['config']))
        old_plans = {x['owner_plan_id'] for x in actual.values() if x['owner_plan_id'] is not None}
        if bool(old_plans) != replacement:
            raise ValueError('use explicit supersession for occupied targets')
        for key, item in plan['snapshot']['targets'].items():
            current = plan['snapshot']['current'][key]
            if type(current.get('revision')) is not int or current['revision'] != actual[key]['current_revision']:
                raise ValueError('current archive changed; re-evaluate')
            ready = readiness(db, opportunity, key, item['action'], now, immediate=immediate, approved=replacement)
            if not ready['ready']:
                raise ValueError(ready['reason'])
        if replacement:
            if not reason or safe_isolation is not True:
                raise ValueError('explicit replacement reason and safe shared-resource isolation required')
            if failure_id is not None:
                failure = db.execute('SELECT * FROM opportunity_failures WHERE id=? AND opportunity_id=?', (failure_id, opportunity['id'])).fetchone()
                if not failure or failure['consumed']:
                    raise ValueError('fresh bounded failure recovery authorization required')
            elif config.supersession_limit <= opportunity['supersessions'] or (instant(now) - parse(opportunity['created_at'])).total_seconds() > config.supersession_seconds:
                raise ValueError('supersession budget exhausted')
            for old_id in old_plans:
                old = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (old_id,)).fetchone())
                if old['opportunity_id'] != plan['opportunity_id']:
                    raise ValueError('replacement must preserve opportunity lineage')
                for key, value in expected.items():
                    if value['owner_plan_id'] != old_id or failure_id is not None:
                        continue
                    before = old['snapshot']['targets'][key]
                    after = plan['snapshot']['targets'][key]
                    evidence_better = after['action'] == 'EVIDENCE_UPGRADE' and before['evidence_source'] == 'inferred_pgs' and after['evidence_source'] == 'explicit'
                    if tuple(after['quality']) < tuple(before['quality']) or (tuple(after['quality']) == tuple(before['quality']) and not evidence_better):
                        raise ValueError('replacement is not a verified improvement over active plan')
                phases = {db.execute('SELECT transfer_phase FROM plan_targets WHERE plan_id=? AND target_key=?', (old_id, key)).fetchone()[0] for key, value in expected.items() if value['owner_plan_id'] == old_id}
                if 'DOWNLOADING' in phases and failure_id is None:
                    costs = (progress or {}).get(old_id, {})
                    if not config.normal_download_preemption:
                        raise ValueError('healthy download continues')
                    if type(costs.get('downloaded_bytes')) is not int or costs['downloaded_bytes'] < 0 or type(costs.get('remaining_seconds')) not in (int, float) or not math.isfinite(costs['remaining_seconds']) or costs['remaining_seconds'] < 0 or costs.get('status') != 'DOWNLOADING':
                        raise ValueError('unknown file-level progress cannot be treated as zero')
                    if costs.get('scope') != sorted(k for k, v in expected.items() if v['owner_plan_id'] == old_id) or costs.get('sampled_at') != stamp(now):
                        raise ValueError('fresh selected-file progress required')
                    if costs['downloaded_bytes'] > config.max_downloaded_bytes or costs['remaining_seconds'] < config.min_remaining_seconds:
                        raise ValueError('preemption exceeds configured file cost')
                elif phases & {'RAPID_WAIT', 'RAPID_IN_FLIGHT', 'CD2_UPLOADING', 'REMOTE_VERIFIED', 'READY_TO_PUBLISH', 'WAITING_ASSETS'} and not config.waiting_transfer_preemption and failure_id is None:
                    raise ValueError('waiting-transfer supersession disabled')
            if failure_id is not None:
                db.execute('UPDATE opportunity_failures SET consumed=1 WHERE id=?', (failure_id,))
            else:
                db.execute('UPDATE opportunities SET supersessions=supersessions+1,updated_at=? WHERE id=?', (stamp(now), opportunity['id']))
        for key, current in actual.items():
            generation = current['generation'] + 1
            if current['owner_plan_id']:
                old_id = current['owner_plan_id']
                db.execute("UPDATE plan_targets SET state='SUPERSEDED',superseded_by=?,reason=? WHERE plan_id=? AND target_key=?", (plan_id, reason, old_id, key))
                # Queued work is revoked durably; issued operations keep their old receipts.
                for row in db.execute("SELECT id,targets FROM plan_actions WHERE plan_id=? AND state='PENDING'", (old_id,)).fetchall():
                    if key in json.loads(row['targets']):
                        db.execute("UPDATE plan_actions SET state='CANCELLED' WHERE id=?", (row['id'],))
            db.execute("UPDATE target_units SET owner_plan_id=?,generation=?,publish_phase='NOT_SENT',publish_action_id=NULL WHERE target_key=?", (plan_id, generation, key))
            db.execute("UPDATE plan_targets SET generation=?,state='ACTIVE' WHERE plan_id=? AND target_key=?", (generation, plan_id, key))
        for old_id in old_plans:
            if not db.execute("SELECT 1 FROM plan_targets WHERE plan_id=? AND state='ACTIVE'", (old_id,)).fetchone():
                db.execute("UPDATE plans SET authorization='SUPERSEDED' WHERE id=?", (old_id,))
        db.execute("UPDATE plans SET authorization='ACTIVE' WHERE id=?", (plan_id,))
        self.repository._audit(db, plan['task_id'], ('PLAN_SUPERSEDED_BY:' if replacement else 'PLAN_CLAIMED:') + plan_id, 'planner')
        return self._vector(db, expected)

    def claim(self, plan_id, expected, *, now=None, immediate=False):
        with self.repository.connection(write=True) as db:
            return self._acquire(db, plan_id, expected, replacement=False, reason='', safe_isolation=False, now=instant(now), immediate=immediate, progress=None)

    def supersede(self, plan_id, expected, *, reason, safe_isolation, now=None, immediate=False, progress=None):
        with self.repository.connection(write=True) as db:
            return self._acquire(db, plan_id, expected, replacement=True, reason=reason, safe_isolation=safe_isolation, now=instant(now), immediate=immediate, progress=progress)

    def recover(self, plan_id, expected, *, failure_id, safe_isolation, now=None):
        with self.repository.connection(write=True) as db:
            return self._acquire(db, plan_id, expected, replacement=True, reason='FAILURE_RECOVERY', safe_isolation=safe_isolation, now=instant(now), immediate=False, progress=None, failure_id=failure_id)

    def extend_assets(self,old_id,new_id,snapshot,expected):
        """Same resource, unchanged target decision; no upgrade clock or budget."""
        old=self.plan(old_id);before=old['snapshot']
        if snapshot.get('source_plan')!=old_id or 'local_assets' in before or not snapshot.get('local_assets') or any(snapshot[k]!=v for k,v in before.items() if k!='selected_indices') or snapshot['selected_indices']!=before['selected_indices']+[a['file']['index'] for a in snapshot['local_assets']]:raise ValueError('ASSET_EXTENSION_LINEAGE_REQUIRED')
        self.prepare(new_id,old['opportunity_id'],snapshot)
        with self.repository.connection(write=True) as db:
            self._task_active(db,old);self._revisions(db,before);self._match(db,expected,owner=old_id)
            if db.execute("SELECT 1 FROM plan_actions WHERE plan_id=? AND state IN ('UNKNOWN','IN_FLIGHT','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF')",(old_id,)).fetchone() or db.execute('SELECT 1 FROM delivery_bundles WHERE plan_id=?',(old_id,)).fetchone():raise ValueError('SOURCE_PLAN_UNSETTLED')
            for key,v in expected.items():
                if v['current_revision']!=before['current'][key]['revision']:raise ValueError('STALE_CURRENT')
                db.execute("UPDATE target_units SET owner_plan_id=?,generation=generation+1 WHERE target_key=?",(new_id,key))
                db.execute("UPDATE plan_targets SET state='SUPERSEDED',superseded_by=?,reason='VERIFIED_LOCAL_ASSETS' WHERE plan_id=? AND target_key=?",(new_id,old_id,key))
                db.execute("UPDATE plan_targets SET state='ACTIVE',generation=?,transfer_phase='WAITING_ASSETS' WHERE plan_id=? AND target_key=?",(v['generation']+1,new_id,key))
            db.execute("UPDATE plans SET authorization='SUPERSEDED' WHERE id=?",(old_id,))
            db.execute("UPDATE plans SET authorization='ACTIVE',transfer_phase='WAITING_ASSETS' WHERE id=?",(new_id,))
        return self.plan(new_id)

    def update_current(self, target_key, facts, *, expected_revision, db=None):
        """W06 refreshes the target CAS token with its archive writes, never its clock."""
        if db is None:
            with self.repository.connection(write=True) as connection:
                return self.update_current(target_key, facts, expected_revision=expected_revision, db=connection)
        if facts.get('state') not in ('PRESENT', 'MISSING', 'INVALID', 'UNKNOWN', 'ERROR') or not facts.get('evidence_ref') or type(expected_revision) is not int:
            raise ValueError('typed current evidence required')
        row = db.execute('SELECT * FROM target_units WHERE target_key=?', (target_key,)).fetchone()
        if not row or row['current_revision'] != expected_revision:
            raise ValueError('stale current archive revision')
        text = encoded(facts)
        if row['current_facts'] == text:
            return expected_revision
        db.execute('UPDATE target_units SET current_facts=?,current_revision=current_revision+1 WHERE target_key=?', (text, target_key))
        return expected_revision + 1

    @staticmethod
    def _batch(plan, vector, indices):
        files = validate_files(asset_table(plan['snapshot']), indices)
        if not set(indices) <= set(plan['snapshot']['selected_indices']):
            raise ValueError('file not in frozen plan')
        coverage = {k for i in indices for k in files[i]['targets']}
        if coverage != set(vector):
            raise ValueError('complete physical-file coverage must match authority vector')

    def set_transfer_phase(self, plan_id, vector, phase):
        if phase not in TRANSFER_PHASES:
            raise ValueError('invalid transfer phase')
        with self.repository.connection(write=True) as db:
            self._match(db, vector, owner=plan_id)
            db.execute('UPDATE plans SET transfer_phase=? WHERE id=?', (phase, plan_id))
            for key in vector:
                db.execute('UPDATE plan_targets SET transfer_phase=? WHERE plan_id=? AND target_key=?', (phase, plan_id, key))

    @staticmethod
    def _exclusion_revision(db, token):
        if token is not None and sha256(encoded([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]).encode()).hexdigest() != token:
            raise ValueError('EXCLUSIONS_CHANGED')

    def begin_attempt(self, action_id, plan_id, vector, kind, indices, payload, *, now=None, exclusion_token=None, db=None):
        if kind not in ATTEMPT_KINDS - {'PUBLISH'}:
            raise ValueError('use begin_publish for publication')
        with (self.repository.connection(write=True) if db is None else nullcontext(db)) as db:
            self._exclusion_revision(db, exclusion_token)
            return self._begin(db, action_id, plan_id, vector, kind, indices, payload, now)

    def download_references(self, downloader, infohash, save_path, *, db=None):
        """One coherent physical download cohort, including indivisible references."""
        if db is None:
            with self.repository.connection() as connection:
                return self.download_references(downloader, infohash, save_path, db=connection)
        result, table = [], {}
        for row in db.execute("SELECT * FROM plans WHERE authorization='ACTIVE' ORDER BY id"):
            plan = self._plan(row); snapshot = plan['snapshot']
            if (snapshot['downloader'], snapshot['infohash'], snapshot['save_path']) != (downloader, infohash.lower(), save_path):
                continue
            owned = {r[0] for r in db.execute("SELECT p.target_key FROM plan_targets p JOIN target_units t ON t.target_key=p.target_key WHERE p.plan_id=? AND p.state='ACTIVE' AND t.owner_plan_id=p.plan_id AND t.generation=p.generation", (plan['id'],))}
            indices = []
            for item in snapshot['torrent_files']:
                identity = (item['path'], item['size'])
                if item['index'] in table and table[item['index']] != identity:
                    raise ValueError('shared torrent table conflicts')
                table[item['index']] = identity
                if item['index'] in snapshot['selected_indices'] and owned.intersection(item['targets']):
                    indices.append(item['index'])
            if indices:
                keys = sorted({key for i in indices for key in snapshot['torrent_files'][i]['targets']})
                result.append(dict(plan_id=plan['id'], indices=indices, vector=self._vector(db, keys)))
        return result

    def begin_shared_attempt(self, action_id, family, references, kind, payload, *, exclusion_token, now=None):
        """Authorize one client RPC against every participant in one write transaction.

        Actions retain the exact cohort, whole wanted set and operation cycle. A
        later cohort cannot turn an uncertain older physical RPC into a retry.
        """
        if kind not in ('ADD', 'SET_WANTED', 'RESUME') or not references:
            raise ValueError('complete download cohort required')
        with self.repository.connection(write=True) as db:
            if self.download_references(*family, db=db) != references:
                raise ValueError('SHARED_AUTHORITY_CHANGED')
            token = sha256(encoded([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]).encode()).hexdigest()
            if token != exclusion_token:
                raise ValueError('EXCLUSIONS_CHANGED')
            managed=db.execute('SELECT save_path,evidence FROM managed_downloads WHERE downloader=? AND infohash=?',tuple(family[:2])).fetchone()
            if not managed or managed['save_path']!=family[2] or json.loads(managed['evidence']).get('cycle',0)!=payload.get('cycle'):
                raise ValueError('DOWNLOAD_CYCLE_CHANGED')
            shared = dict(action_id=action_id, family=list(family), references=references,
                          wanted=sorted({i for ref in references for i in ref['indices']}))
            if payload.get('wanted_indices')!=shared['wanted']:
                raise ValueError('SHARED_SELECTION_CHANGED')
            data = dict(payload, shared=shared)
            for row in db.execute("SELECT a.payload,p.snapshot FROM plan_actions a JOIN plans p ON p.id=a.plan_id WHERE a.kind IN ('ADD','SET_WANTED','RESUME') AND a.state IN ('IN_FLIGHT','UNKNOWN')"):
                old, previous = json.loads(row['snapshot']), json.loads(row['payload'])
                if [old['downloader'], old['infohash'], old['save_path']] == list(family) and previous.get('shared', {}).get('action_id') != action_id:
                    raise ValueError('SHARED_OUTCOME_UNRESOLVED')
            execution=payload.get('execution',references)
            if [r['plan_id'] for r in execution]!=[r['plan_id'] for r in references]:raise ValueError('SHARED_AUTHORITY_CHANGED')
            for complete,physical in zip(execution,references):
                plan=self._plan(db.execute('SELECT * FROM plans WHERE id=?',(complete['plan_id'],)).fetchone())
                files=plan['snapshot']['torrent_files'];indices=[i for i in complete['indices'] if i<len(files)]
                keys={k for i in indices for k in files[i]['targets']}
                if indices!=physical['indices'] or {k:complete['vector'][k] for k in keys}!=physical['vector']:raise ValueError('SHARED_AUTHORITY_CHANGED')
            # A physical RPC belongs to the full execution decision, including
            # local-only subtitle targets. Validate that vector atomically too.
            actions = [self._begin(db, action_id + ':' + ref['plan_id'], ref['plan_id'], ref['vector'], kind, ref['indices'], data, now) for ref in execution]
            if len({a['dispatch'] for a in actions}) != 1:
                raise ValueError('SHARED_DISPATCH_CONFLICT')
            return actions

    def begin_history_repair(self, original_id, vector, missing_paths, *, exclusion_token, now=None):
        """A separate bounded compensation for a returned partial local DB call.

        An in-flight or ambiguous call is not replayable. A returned local call
        plus fresh complete readback permits at most three durable compensations.
        """
        with self.repository.connection(write=True) as db:
            original=db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='ORGANIZE' AND state='UNKNOWN'",(original_id,)).fetchone()
            if not original or original['targets']!=encoded(vector):raise ValueError('HISTORY_REPAIR_AUTHORITY_CHANGED')
            payload=json.loads(original['payload'])
            if payload.get('verb')!='history' or not missing_paths or len(set(missing_paths))!=len(missing_paths) or not set(missing_paths)<=set(payload['paths']):
                raise ValueError('EXACT_HISTORY_COMPENSATION_REQUIRED')
            token=sha256(encoded([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]).encode()).hexdigest()
            if token!=exclusion_token:raise ValueError('EXCLUSIONS_CHANGED')
            reconciles={original_id}
            repairs=[]
            for row in db.execute("SELECT id,payload,state FROM plan_actions WHERE plan_id=? AND kind='ORGANIZE' ORDER BY created_at,id",(original['plan_id'],)):
                previous=json.loads(row['payload'])
                if previous.get('original_action')!=original_id:continue
                if row['state'] in ('IN_FLIGHT','PENDING'):raise ValueError('HISTORY_REPAIR_IN_FLIGHT')
                receipts=db.execute("SELECT evidence FROM action_receipts WHERE action_id=? AND outcome='UNKNOWN'",(row['id'],)).fetchall()
                if row['state']!='UNKNOWN' or not any(json.loads(r[0]).get('code')=='LOCAL_HISTORY_REPAIR_RETURNED' for r in receipts):
                    raise ValueError('HISTORY_REPAIR_RETURN_UNPROVEN')
                if not set(missing_paths)<=set(previous['missing_paths']):raise ValueError('HISTORY_ROWS_CHANGED')
                repairs.append(row['id']);reconciles.add(row['id'])
            if len(repairs)>=HISTORY_REPAIR_LIMIT:raise ValueError('HISTORY_REPAIR_EXHAUSTED')
            data=dict(payload,verb='history-repair',original_action=original_id,missing_paths=sorted(missing_paths),repair_sequence=len(repairs)+1,repair_limit=HISTORY_REPAIR_LIMIT)
            action_id='history-repair:'+sha256(encoded(data).encode()).hexdigest()
            return self._begin(db,action_id,original['plan_id'],vector,'ORGANIZE',json.loads(original['files']),data,now,reconciles=reconciles)

    def settle_legacy_preflight(self,action_id,vector,proof,*,exclusion_token,now=None):
        """Settle the old destination-NULL, returned pre-copy collision only.

        Legacy HostOrganization persisted destination before calling transfer.
        No newer naming operation or possibly sent copy can use this settlement.
        """
        with self.repository.connection(write=True) as db:
            action=db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='ORGANIZE' AND state='UNKNOWN'",(action_id,)).fetchone()
            if not action or action['targets']!=encoded(vector):raise ValueError('PREFLIGHT_SETTLEMENT_UNPROVEN')
            payload=json.loads(action['payload'])
            if payload.get('verb')!='organize:'+str(payload.get('file_index')) or 'naming_revision' in payload or proof.get('code')!='LEGACY_PREFLIGHT_COLLISION_V1' or proof.get('native_history_count')!=0 or proof.get('target_root')!=payload.get('target_root'):
                raise ValueError('PREFLIGHT_SETTLEMENT_UNPROVEN')
            asset=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(action['plan_id'],payload['file_index'])).fetchone()
            if not asset or asset['state']!='AUTHORIZED' or asset['destination'] is not None or asset['source']!=payload.get('source') or asset['sha256']!=payload.get('sha256') or asset['sha256']!=proof.get('source_sha256'):
                raise ValueError('PREFLIGHT_SETTLEMENT_UNPROVEN')
            collision=db.execute("SELECT * FROM organized_assets WHERE plan_id=? AND file_index!=? AND state='COMPLETE' AND destination=? AND sha256=?",(action['plan_id'],asset['file_index'],proof.get('collision_destination'),proof.get('collision_sha256'))).fetchone()
            returned=any(json.loads(r[0]).get('code')=='CLIENT_RESPONSE_UNKNOWN' for r in db.execute("SELECT evidence FROM action_receipts WHERE action_id=? AND outcome='UNKNOWN'",(action_id,)))
            if not collision or not returned:raise ValueError('PREFLIGHT_SETTLEMENT_UNPROVEN')
            token=sha256(encoded([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]).encode()).hexdigest()
            if token!=exclusion_token:raise ValueError('EXCLUSIONS_CHANGED')
            self._begin(db,action_id,action['plan_id'],vector,'ORGANIZE',json.loads(action['files']),payload,now)
            db.execute('INSERT OR IGNORE INTO action_receipts(action_id,outcome,evidence,at) VALUES(?,?,?,?)',(action_id,'FAILED',encoded(proof),stamp(now)))
            db.execute("UPDATE plan_actions SET state='FAILED',updated_at=? WHERE id=?",(stamp(now),action_id))
            return {'state':'FAILED','not_sent':True}

    def _begin(self, db, action_id, plan_id, vector, kind, indices, payload, now, queued=False, reconciles=()):
        identifier(action_id)
        plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
        if kind in ('ADD','SET_WANTED','RESUME','ORGANIZE'):self._download_not_cleaning(db,plan['snapshot'])
        self._task_active(db, plan)
        self._revisions(db, plan['snapshot'])
        self._batch(plan, vector, indices)
        actual = self._match(db, vector, owner=plan_id)
        if kind != 'PUBLISH' and any(plan['snapshot']['current'][k]['revision'] != actual[k]['current_revision'] for k in vector):
            raise ValueError('current archive changed; execution must be re-evaluated')
        old = db.execute('SELECT * FROM plan_actions WHERE id=?', (action_id,)).fetchone()
        fields = (plan_id, kind, encoded(vector), encoded(sorted(indices)), encoded(payload), plan['task_generation'])
        if old:
            if tuple(old[k] for k in ('plan_id', 'kind', 'targets', 'files', 'payload', 'task_generation')) != fields:
                raise ValueError('attempt id reused with different operation')
            if old['state'] != 'PENDING' or queued:
                return {**dict(old), 'dispatch': False}
        for row in db.execute("SELECT id,files FROM plan_actions WHERE plan_id=? AND kind=? AND state IN ('IN_FLIGHT','UNKNOWN') AND id!=?", (plan_id, kind, action_id)):
            if row['id'] in reconciles:continue
            if set(indices) & set(json.loads(row['files'])):
                raise ValueError('overlapping external attempt outcome unresolved')
        at = stamp(now)
        if old:
            db.execute("UPDATE plan_actions SET state='IN_FLIGHT',updated_at=? WHERE id=?", (at, action_id))
        else:
            db.execute('INSERT INTO plan_actions(id,plan_id,kind,targets,files,payload,task_generation,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)', (action_id, *fields, 'PENDING' if queued else 'IN_FLIGHT', at, at))
        self.repository._audit(db, plan['task_id'], 'ATTEMPT_AUTHORIZED:' + action_id, 'executor')
        return {**dict(db.execute('SELECT * FROM plan_actions WHERE id=?', (action_id,)).fetchone()), 'dispatch': not queued}

    @staticmethod
    def _download_not_cleaning(db,snapshot):
        row=db.execute('SELECT state FROM managed_downloads WHERE downloader=? AND infohash=?',(snapshot['downloader'],snapshot['infohash'])).fetchone()
        if row and row[0]=='DELIVERY_CLEANUP':raise ValueError('DOWNLOAD_CLEANUP_UNSETTLED')

    def queue_attempt(self, action_id, plan_id, vector, kind, indices, payload, *, now=None):
        if kind not in ATTEMPT_KINDS - {'PUBLISH'}:
            raise ValueError('invalid queued operation')
        with self.repository.connection(write=True) as db:
            return self._begin(db, action_id, plan_id, vector, kind, indices, payload, now, queued=True)

    def action(self, action_id):
        with self.repository.connection() as db:
            row = db.execute('SELECT * FROM plan_actions WHERE id=?', (action_id,)).fetchone()
            return dict(row) if row else None

    def pending_actions(self, *, after_id='', limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('bounded action page required')
        with self.repository.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM plan_actions WHERE state='PENDING' AND id>? ORDER BY id LIMIT ?", (after_id, limit))]

    def begin_publish(self, action_id, plan_id, vector, indices, *, validation, now=None, exclusion_token=None, db=None):
        required_checks = {'identity', 'admission', 'scope', 'not_excluded', 'current_allows', 'assets_complete', 'remote_verified'}
        if set(validation) != {'policy_revision', 'parse_revision', 'current_revisions', 'checks'} or set(validation['checks']) != set(vector) or set(validation['current_revisions']) != set(vector):
            raise ValueError('complete fresh publication re-evaluation required')
        for check in validation['checks'].values():
            if set(check) != required_checks or any(v is not True for v in check.values()):
                raise ValueError('publication re-evaluation denied or incomplete')
        with (self.repository.connection(write=True) if db is None else nullcontext(db)) as db:
            self._exclusion_revision(db, exclusion_token)
            old = db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='PUBLISH'", (action_id,)).fetchone()
            if old:
                if old['plan_id'] != plan_id or old['targets'] != encoded(vector) or old['files'] != encoded(sorted(indices)) or old['payload'] != encoded(validation):
                    raise ValueError('publication id reused')
                return {**dict(old), 'dispatch': False}
            plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
            if any(db.execute('SELECT transfer_phase FROM plan_targets WHERE plan_id=? AND target_key=?', (plan_id, key)).fetchone()[0] != 'READY_TO_PUBLISH' for key in vector):
                raise ValueError('bundle is not ready to publish')
            self._revisions(db, validation)
            actual = self._match(db, vector, owner=plan_id)
            if any(type(validation['current_revisions'][k]) is not int or validation['current_revisions'][k] != actual[k]['current_revision'] for k in vector):
                raise ValueError('archive changed after publication re-evaluation')
            action = self._begin(db, action_id, plan_id, vector, 'PUBLISH', indices, validation, now)
            for key in vector:
                db.execute("UPDATE target_units SET publish_phase='PUBLISHING',publish_action_id=? WHERE target_key=?", (action_id, key))
            return action

    def record_result(self, action_id, outcome, evidence, *, now=None, db=None):
        if outcome not in ('SUCCEEDED', 'FAILED', 'UNKNOWN', 'HANDED_OFF'):
            raise ValueError('invalid external outcome')
        with (self.repository.connection(write=True) if db is None else nullcontext(db)) as db:
            row = db.execute('SELECT * FROM plan_actions WHERE id=?', (action_id,)).fetchone()
            if not row:
                raise ValueError('unknown attempt')
            data = encoded(evidence)
            db.execute('INSERT OR IGNORE INTO action_receipts(action_id,outcome,evidence,at) VALUES(?,?,?,?)', (action_id, outcome, data, stamp(now)))
            # A transport receipt cannot confirm ingest or grant authority. Failed
            # publication is also uncertain until the old sender/consumer is settled.
            state = ('HANDED_OFF' if outcome in ('SUCCEEDED', 'HANDED_OFF') else 'PUBLISH_OUTCOME_UNKNOWN') if row['kind'] == 'PUBLISH' else outcome
            if row['state'] not in ('INGEST_CONFIRMED', 'RESOLVED_NOT_SENT', 'RESOLVED_SETTLED'):
                if row['state'] != 'HANDED_OFF' or state == 'HANDED_OFF':
                    db.execute('UPDATE plan_actions SET state=?,updated_at=? WHERE id=?', (state, stamp(now), action_id))
                    if row['kind'] == 'PUBLISH':
                        db.execute('UPDATE target_units SET publish_phase=? WHERE publish_action_id=?', (state, action_id))
            return {'state': state, 'receipt_only': True}

    def cancel(self, plan_id, vector, *, reason, db=None):
        identifier(reason)
        with (self.repository.connection(write=True) if db is None else nullcontext(db)) as db:
            current = self._match(db, vector, owner=plan_id, allow_barrier=True)
            plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
            for key, row in current.items():
                db.execute("UPDATE plan_targets SET state='CANCELLED',reason=? WHERE plan_id=? AND target_key=?", (reason, plan_id, key))
                if row['publish_phase'] not in BARRIERS:
                    db.execute('UPDATE target_units SET owner_plan_id=NULL,generation=generation+1 WHERE target_key=?', (key,))
            for row in db.execute("SELECT id,targets FROM plan_actions WHERE plan_id=? AND state='PENDING'", (plan_id,)).fetchall():
                if set(vector) & set(json.loads(row['targets'])):
                    db.execute("UPDATE plan_actions SET state='CANCELLED' WHERE id=?", (row['id'],))
            if not db.execute("SELECT 1 FROM plan_targets WHERE plan_id=? AND state='ACTIVE'", (plan_id,)).fetchone():
                db.execute("UPDATE plans SET authorization='CANCELLED' WHERE id=?", (plan_id,))
            self.repository._audit(db, plan['task_id'], 'PLAN_CANCELLED:' + plan_id, reason)

    def resolve_publish(self, action_id, resolution, proof, *, now=None):
        # Neither a lease timeout, an absent source path nor another higher version
        # is evidence that an old sender/consumer can no longer act.
        if resolution not in ('NOT_SENT', 'SETTLED') or proof.get('sender_stopped') is not True or proof.get('remote_operation_settled') is not True or proof.get('consumer_cannot_apply') is not True or not proof.get('evidence_ref'):
            raise ValueError('explicit sender/remote/consumer settlement evidence required')
        with self.repository.connection(write=True) as db:
            row = db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='PUBLISH'", (action_id,)).fetchone()
            if not row or row['state'] == 'INGEST_CONFIRMED':
                raise ValueError('no unresolved publication')
            db.execute('INSERT OR IGNORE INTO action_receipts(action_id,outcome,evidence,at) VALUES(?,?,?,?)', (action_id, 'RESOLVED_' + resolution, encoded(proof), stamp(now)))
            db.execute('UPDATE plan_actions SET state=?,updated_at=? WHERE id=?', ('RESOLVED_' + resolution, stamp(now), action_id))
            # Cancellation intentionally held ownership while a remote sender could
            # still act. Release only those cancelled targets this attempt resolved;
            # active siblings and other outstanding publication batches stay owned.
            db.execute("UPDATE target_units SET owner_plan_id=NULL,generation=generation+1 WHERE publish_action_id=? AND owner_plan_id=? AND EXISTS (SELECT 1 FROM plan_targets p WHERE p.plan_id=target_units.owner_plan_id AND p.target_key=target_units.target_key AND p.generation=target_units.generation AND p.state='CANCELLED')", (action_id, row['plan_id']))
            db.execute('UPDATE target_units SET publish_phase=?,publish_action_id=NULL WHERE publish_action_id=?', (resolution, action_id))

    def record_progress(self, plan_id, indices, file_stats, *, torrent, status, now=None):
        if status not in ('DOWNLOADING', 'QUEUED', 'PAUSED', 'CHECKING', 'LIMITED', 'DISCONNECTED', 'COMPLETED', 'FAILED'):
            raise ValueError('explicit downloader status required')
        with self.repository.connection(write=True) as db:
            plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())
            files = validate_files(asset_table(plan['snapshot']), indices)
            if not set(indices) <= set(plan['snapshot']['selected_indices']) or set(file_stats) != set(indices):
                raise ValueError('exact selected-file statistics required')
            for index, values in file_stats.items():
                for field in ('downloaded_bytes', 'speed'):
                    value = values.get(field)
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                        raise ValueError('unknown progress must remain None')
                if values.get('downloaded_bytes') is not None and values['downloaded_bytes'] > files[index]['size']:
                    raise ValueError('downloaded bytes exceed file size')
            total = sum(files[i]['size'] for i in indices)
            downloaded = sum(file_stats[i]['downloaded_bytes'] for i in indices) if all(file_stats[i].get('downloaded_bytes') is not None for i in indices) else None
            speed = sum(file_stats[i]['speed'] for i in indices) if all(file_stats[i].get('speed') is not None for i in indices) else None
            remaining = (total - downloaded) / speed if downloaded is not None and speed else None
            old = db.execute('SELECT sample FROM plan_progress WHERE plan_id=? AND files=?', (plan_id, encoded(sorted(indices)))).fetchone()
            old = json.loads(old[0]) if old else None
            made_progress = downloaded is not None and old and old['downloaded_bytes'] is not None and downloaded > old['downloaded_bytes']
            last_progress = stamp(now) if made_progress else old['last_progress_at'] if old else None
            sample = dict(scope=sorted({key for i in indices for key in files[i]['targets']}), files={str(k): v for k,v in file_stats.items()},
                          torrent=torrent, total_bytes=total, downloaded_bytes=downloaded, speed=speed,
                          remaining_seconds=remaining, status=status, sampled_at=stamp(now), last_progress_at=last_progress)
            db.execute('INSERT INTO plan_progress VALUES(?,?,?) ON CONFLICT(plan_id,files) DO UPDATE SET sample=excluded.sample', (plan_id, encoded(sorted(indices)), encoded(sample)))
            return sample

    def progress(self, plan_id, indices):
        with self.repository.connection() as db:
            row = db.execute('SELECT sample FROM plan_progress WHERE plan_id=? AND files=?', (plan_id, encoded(sorted(indices)))).fetchone()
            return json.loads(row[0]) if row else None

    def active_files(self, downloader, infohash, save_path):
        """Wanted union for owned plans. A shared indivisible file stays referenced."""
        with self.repository.connection() as db:
            indices, table = set(), {}
            for row in db.execute("SELECT * FROM plans WHERE authorization='ACTIVE'"):
                plan = self._plan(row)
                snapshot = plan['snapshot']
                if (snapshot['downloader'], snapshot['infohash'], snapshot['save_path']) != (downloader, infohash.lower(), save_path):
                    continue
                for item in snapshot['torrent_files']:
                    if item['index'] in table and table[item['index']] != (item['path'], item['size']):
                        raise ValueError('shared torrent table conflicts')
                    table[item['index']] = (item['path'], item['size'])
                    if item['index'] in snapshot['selected_indices'] and any(db.execute("SELECT 1 FROM plan_targets p JOIN target_units t ON t.target_key=p.target_key WHERE p.plan_id=? AND p.target_key=? AND p.state='ACTIVE' AND t.owner_plan_id=p.plan_id AND t.generation=p.generation", (plan['id'], key)).fetchone() for key in item['targets']):
                        indices.add(item['index'])
            return sorted(indices)

    def confirm_evidence(self, opportunity_id, confirmations, *, expected, now=None, db=None):
        """W06 joins verified no-transfer evidence to the same CAS/receipt ledger."""
        if db is None:
            with self.repository.connection(write=True) as connection:
                return self.confirm_evidence(opportunity_id, confirmations, expected=expected, now=now, db=connection)
        if not confirmations or set(confirmations) != set(expected['targets']):
            raise ValueError('exact evidence target vector required')
        duplicates = []
        for key, proof in confirmations.items():
            for name in ('association_verified', 'all_assets_verified', 'improvement_verified'):
                if proof.get(name) is not True:
                    raise ValueError('final evidence association incomplete')
            if proof.get('opportunity_id') != opportunity_id or not proof.get('evidence_keys'):
                raise ValueError('opportunity evidence required')
            for name in ('receipt_id', 'version_id', 'evidence_ref'):
                identifier(proof.get(name))
            prior = db.execute('SELECT * FROM ingest_receipts WHERE id=?', (proof['receipt_id'],)).fetchone()
            if prior and (prior['plan_id'] is not None or prior['target_key'] != key or prior['evidence'] != encoded(proof)):
                raise ValueError('evidence receipt id reused')
            duplicates.append(prior is not None)
        if all(duplicates):
            return {'accepted': False, 'duplicate': True, 'reason': 'ALREADY_CONFIRMED'}
        if any(duplicates):
            raise ValueError('partial evidence receipt batch')
        opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
        if not opportunity or opportunity['state'] != 'ACTIVE' or not set(confirmations) <= set(json.loads(opportunity['scope'])):
            raise ValueError('evidence outside active opportunity')
        self._task_active(db, {'task_id': opportunity['task_id'], 'task_generation': expected['task_generation']})
        self._revisions(db, expected)
        actual = self._match(db, expected['targets'])
        if any(actual[k]['owner_plan_id'] is not None or actual[k]['current_revision'] != expected['targets'][k]['current_revision'] for k in actual):
            raise ValueError('stale or owned evidence target')
        consumed = set()
        for key, proof in confirmations.items():
            if db.execute('SELECT fulfilled FROM opportunity_targets WHERE opportunity_id=? AND target_key=?', (opportunity_id, key)).fetchone()[0]:
                raise ValueError('opportunity target already fulfilled')
            db.execute('INSERT INTO ingest_receipts VALUES(?,NULL,?,?,?,?,?)', (proof['receipt_id'], key, actual[key]['generation'], proof['version_id'], encoded(proof), stamp(now)))
            for evidence_key in proof['evidence_keys']:
                identifier(evidence_key)
                if evidence_key not in consumed:
                    db.execute('INSERT INTO evidence_consumption VALUES(?,?)', (evidence_key, proof['receipt_id']))
                    consumed.add(evidence_key)
            db.execute('UPDATE opportunity_targets SET fulfilled=1 WHERE opportunity_id=? AND target_key=?', (opportunity_id, key))
        if not db.execute('SELECT 1 FROM opportunity_targets WHERE opportunity_id=? AND fulfilled=0', (opportunity_id,)).fetchone():
            db.execute('UPDATE opportunities SET state=?,updated_at=? WHERE id=?', ('ARCHIVED' if opportunity['mode'] == 'ONESHOT' else 'COMPLETED', stamp(now), opportunity_id))
            if opportunity['mode']=='ONESHOT':
                db.execute("UPDATE tasks SET state='PASSIVE',updated_at=? WHERE id=? AND state='ACTIVE' AND NOT EXISTS(SELECT 1 FROM opportunities WHERE task_id=? AND state='ACTIVE')",(stamp(now),opportunity['task_id'],opportunity['task_id']))
        self.repository._audit(db, opportunity['task_id'], 'EVIDENCE_CONFIRMED:' + opportunity_id, 'archive')
        return {'accepted': True, 'duplicate': False}

    def confirm_ingest(self, action_id, confirmations, *, now=None, db=None):
        """Commit final facts/clocks/evidence with W06 archive writes in one transaction.

        W06 may pass its existing Repository write connection, call this first,
        and write version/assets only when accepted=True, before that same commit.
        A stale receipt is preserved but cannot update current archive state.
        """
        if db is None:
            with self.repository.connection(write=True) as connection:
                return self.confirm_ingest(action_id, confirmations, now=now, db=connection)
        from datetime import timedelta
        from .scheduler import Scheduler
        row = db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='PUBLISH'", (action_id,)).fetchone()
        if not row:
            raise ValueError('publication missing')
        vector = json.loads(row['targets'])
        if set(confirmations) != set(vector):
            raise ValueError('final confirmation must cover the exact publication batch')
        for proof in confirmations.values():
            for name in ('association_verified', 'all_assets_verified', 'consumer_settled'):
                if proof.get(name) is not True:
                    raise ValueError('final association/asset/consumer proof incomplete')
            identifier(proof.get('version_id'))
            identifier(proof.get('receipt_id'))
            identifier(proof.get('evidence_ref'))
        if row['state'] == 'INGEST_CONFIRMED':
            if any(proof.get('improvement_verified') is not True for proof in confirmations.values()):
                raise ValueError('final improvement proof incomplete')
            for key, proof in confirmations.items():
                prior = db.execute('SELECT * FROM ingest_receipts WHERE id=?', (proof['receipt_id'],)).fetchone()
                if not prior or (prior['target_key'], prior['version_id'], prior['plan_id']) != (key, proof['version_id'], row['plan_id']):
                    raise ValueError('ingest receipt id changed')
            return {'accepted': False, 'duplicate': True, 'reason': 'ALREADY_CONFIRMED'}
        if row['state'] != 'HANDED_OFF':
            raise ValueError('handoff must first be observed/reconciled')
        plan = self._plan(db.execute('SELECT * FROM plans WHERE id=?', (row['plan_id'],)).fetchone())
        try:
            self._match(db, vector, owner=plan['id'], allow_barrier=True)
            self._task_active(db, plan)
        except ValueError:
            db.execute('INSERT OR IGNORE INTO action_receipts(action_id,outcome,evidence,at) VALUES(?,?,?,?)', (action_id, 'STALE_INGEST', encoded(confirmations), stamp(now)))
            return {'accepted': False, 'reason': 'STALE_AUTHORITY', 'receipt_only': True}
        if any(proof.get('improvement_verified') is not True for proof in confirmations.values()):
            raise ValueError('final improvement proof incomplete')
        if any(db.execute('SELECT publish_action_id FROM target_units WHERE target_key=?', (key,)).fetchone()[0] != action_id for key in vector):
            raise ValueError('different publication owns target barrier')
        opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (plan['opportunity_id'],)).fetchone()
        config = ScheduleConfig(**json.loads(opportunity['config']))
        at = stamp(now)
        consumed_in_batch = set()
        for key, proof in confirmations.items():
            target = plan['snapshot']['targets'][key]
            receipt_id = proof['receipt_id']
            old = db.execute('SELECT * FROM ingest_receipts WHERE id=?', (receipt_id,)).fetchone()
            if old:
                raise ValueError('ingest receipt already used')
            db.execute('INSERT INTO ingest_receipts VALUES(?,?,?,?,?,?,?)', (receipt_id, plan['id'], key, vector[key]['generation'], proof['version_id'], encoded(proof), at))
            for evidence_key in target['evidence_keys']:
                if evidence_key not in consumed_in_batch:
                    db.execute('INSERT INTO evidence_consumption VALUES(?,?)', (evidence_key, receipt_id))
                    consumed_in_batch.add(evidence_key)
            resets_clock = target['action'] not in ('SIDECAR_SUPPLEMENT', 'UNCHANGED') and target['reason'] != 'ENRICH_EVIDENCE'
            if resets_clock:
                cooldown = stamp(instant(now) + timedelta(seconds=config.cooldown_seconds)) if config.cooldown_enabled else None
                db.execute('UPDATE target_units SET last_ingest_confirmed_at=?,cooldown_until=? WHERE target_key=?', (at, cooldown, key))
                db.execute('UPDATE task_lifecycle SET last_ingest_at=? WHERE task_id=?', (at, plan['task_id']))
            db.execute("UPDATE target_units SET current_revision=current_revision+1,current_facts=?,publish_phase='INGEST_CONFIRMED',owner_plan_id=NULL WHERE target_key=?", (encoded({'version_id': proof['version_id'], 'evidence_ref': proof['evidence_ref']}), key))
            db.execute("UPDATE plan_targets SET state='COMPLETED' WHERE plan_id=? AND target_key=?", (plan['id'], key))
            db.execute('UPDATE opportunity_targets SET fulfilled=1 WHERE opportunity_id=? AND target_key=?', (opportunity['id'], key))
        db.execute("UPDATE plan_actions SET state='INGEST_CONFIRMED',updated_at=? WHERE id=?", (at, action_id))
        if not db.execute("SELECT 1 FROM plan_targets WHERE plan_id=? AND state='ACTIVE'", (plan['id'],)).fetchone():
            db.execute("UPDATE plans SET authorization='COMPLETED' WHERE id=?", (plan['id'],))
        if not db.execute('SELECT 1 FROM opportunity_targets WHERE opportunity_id=? AND fulfilled=0', (opportunity['id'],)).fetchone():
            db.execute('UPDATE opportunities SET state=?,updated_at=? WHERE id=?', ('ARCHIVED' if opportunity['mode'] == 'ONESHOT' else 'COMPLETED', at, opportunity['id']))
            if opportunity['mode']=='ONESHOT':
                db.execute("UPDATE tasks SET state='PASSIVE',updated_at=? WHERE id=? AND state='ACTIVE' AND NOT EXISTS(SELECT 1 FROM opportunities WHERE task_id=? AND state='ACTIVE')",(at,plan['task_id'],plan['task_id']))
        Scheduler._refresh_lifecycle(db, plan['task_id'], now)
        self.repository._audit(db, plan['task_id'], 'INGEST_CONFIRMED:' + action_id, 'archive')
        return {'accepted': True, 'duplicate': False}


class Planner:
    """Policy comparison plus deterministic conservative physical-file cover."""
    def __init__(self, policy):
        self.policy = policy

    def evaluate(self, candidate, current, scope, *, mode='episode', excluded=frozenset(), locked=None,
                 consumed=frozenset(), same_assets_verified=frozenset()):
        if mode not in ('episode', 'season') or not scope or len(scope) > 10000 or len(scope) != len(set(scope)):
            raise ValueError('explicit bounded episode/season scope required')
        result = {'plans': [], 'decisions': {}, 'enrichments': [], 'reason': 'NO_SAFE_IMPROVEMENT'}
        if any(candidate.get(k) is not True for k in ('available', 'identity_ok', 'scope_ok', 'files_verified', 'configuration_verified')) or candidate.get('parse_status') != 'OK':
            result['reason'] = 'CANDIDATE_UNVERIFIED_OR_UNAVAILABLE'
            return result
        try:
            table = asset_table(candidate)
            bound = [f['index'] for f in table if f['targets']]
            files = validate_files(table, bound)
        except (ValueError, KeyError, TypeError):
            result['reason'] = 'INVALID_COMPLETE_FILE_TABLE'
            return result
        try:
            validate_new_asset_scope(table)
        except ValueError:
            result['reason'] = 'ASSET_SCOPE_REBIND_REQUIRED'
            return result
        for key in scope:
            baseline = current.get(key, {})
            facts = candidate.get('facts', {}).get(key)
            versions = baseline.get('versions', [])
            state = baseline.get('state')
            if state not in ('PRESENT', 'MISSING', 'INVALID') or (state == 'PRESENT' and not any(v.active for v in versions)) or facts is None:
                result['decisions'][key] = {'status': 'DEFER', 'action': 'NONE', 'reason': 'CURRENT_OR_CANDIDATE_UNKNOWN'}
                continue
            if state == 'MISSING' and any(v.active for v in versions):
                result['decisions'][key] = {'status': 'ERROR', 'action': 'NONE', 'reason': 'CONTRADICTORY_MISSING_FACT'}
                continue
            decision = self.policy.compare(facts, versions, candidate['classification'], consumed=consumed,
                                           same_assets_verified=same_assets_verified, identity_ok=True, scope_ok=True,
                                           excluded=key in excluded, locked=locked)
            action = 'NONE'
            status = decision.status
            if decision.status == 'ALLOW':
                action = {'MISSING': 'ACQUIRE' if state == 'MISSING' else 'REPLACE_INVALID',
                          'QUALITY_UPGRADE': 'QUALITY_UPGRADE', 'EVIDENCE_UPGRADE': 'EVIDENCE_UPGRADE'}.get(decision.reason, 'NONE')
                if decision.action == 'ENRICH_EVIDENCE':
                    result['enrichments'].append({'target_key': key, 'current_revision': baseline['revision'],
                                                  'evidence_keys': list(decision.evidence_keys), 'policy_revision': self.policy.semantic_hash})
                    action = 'NONE'
            elif decision.reason == 'EQUIVALENT':
                action, status = 'UNCHANGED', 'ALLOW'
                if baseline.get('sidecar_missing') is True and candidate.get('same_video_verified', {}).get(key) is True:
                    action = 'SIDECAR_SUPPLEMENT'
            result['decisions'][key] = {'status': status, 'action': action, 'reason': decision.reason,
                                         'evidence_keys': list(decision.evidence_keys), 'rank': list(decision.rank), 'evidence_source': facts.evidence}
        decisions = result['decisions']
        selected, covered = set(), set()
        video_coverage = {key for item in table if item['role'] == 'video' for key in item['targets']}
        if mode == 'season' and not set(scope) <= video_coverage:
            result['reason'] = 'DECLARED_SEASON_NOT_COVERED'
            return result
        for item in table:
            targets = set(item['targets'])
            if item['role'] != 'video' or not targets or not targets <= set(scope):
                continue
            if any(decisions[k]['status'] != 'ALLOW' or decisions[k]['action'] == 'NONE' for k in targets):
                continue
            valuable = any(decisions[k]['action'] not in ('UNCHANGED', 'SIDECAR_SUPPLEMENT') for k in targets)
            if valuable or mode == 'season':
                selected.add(item['index'])
                covered.update(targets)
        # A supplement downloads bound sidecars only after the existing video is
        # positively associated. An equal-quality filename is not that proof.
        for item in table:
            if item['role'] == 'subtitle' and item['targets'] and set(item['targets']) <= set(scope) and all(decisions[k]['action'] == 'SIDECAR_SUPPLEMENT' for k in item['targets']):
                selected.add(item['index'])
                covered.update(item['targets'])
        if mode == 'season' and covered != set(scope):
            return result
        if not covered or not any(decisions[k]['action'] not in ('UNCHANGED', 'NONE') for k in covered):
            return result
        for item in table:
            if item['role'] == 'subtitle' and item['targets'] and set(item['targets']) <= covered:
                selected.add(item['index'])
        pending = list(selected)
        while pending:
            for index in files[pending.pop()]['requires']:
                if index not in selected:
                    selected.add(index)
                    pending.append(index)
        # Dependencies in a frozen table cannot quietly bring a forbidden
        # episode or another season into the selected batch.
        if any(not set(files[i]['targets']) <= covered for i in selected):
            result['reason'] = 'DEPENDENCY_OUTSIDE_AUTHORIZED_SCOPE'
            return result
        counts = {k: sum(f['role'] == 'video' and k in f['targets'] for i, f in files.items() if i in selected) for k in covered}
        if any(n > 1 for n in counts.values()):
            result['reason'] = 'AMBIGUOUS_MULTIPLE_VIDEOS_PER_TARGET'
            return result
        frozen_current = {}
        for key in sorted(covered):
            baseline = current[key]
            frozen_current[key] = {'revision': baseline['revision'], 'state': baseline['state'],
                                   'versions': [{'version_id': v.version_id, 'active': v.active, 'reliable': v.reliable,
                                                 'facts': {k: dict(value) if hasattr(value, 'items') else value for k, value in vars(v.facts).items()}}
                                                for v in baseline.get('versions', [])]}
        snapshot = {key: candidate[key] for key in ('candidate_key', 'infohash', 'downloader', 'save_path', 'parse_revision')}
        snapshot.update(policy_revision=self.policy.semantic_hash, current=frozen_current,
                        targets={k: {**{name: decisions[k][name] for name in ('action', 'reason', 'evidence_keys', 'evidence_source')}, 'quality': decisions[k]['rank']} for k in sorted(covered)},
                        torrent_files=candidate['torrent_files'], selected_indices=sorted(selected),
                        verified=dict(identity=True, scope=True, admission=True, files=True, configuration=True))
        if candidate.get('local_assets'):snapshot.update(local_assets=candidate['local_assets'],source_plan=candidate['source_plan'])
        result['plans'] = [json.loads(encoded(snapshot))]
        result['reason'] = 'READY_FOR_OBSERVATION_AND_CLAIM'
        return result

    def select(self, candidates, current, scope, **kwargs):
        if len(candidates) > 1000:
            raise ValueError('bounded candidate round required')
        # ponytail: bounded deterministic greedy cover; no global optimizer needed.
        ranked = []
        for candidate in candidates:
            decision = self.evaluate(candidate, current, scope, **kwargs)
            ranks = [tuple(v['rank']) for v in decision['decisions'].values() if v.get('rank') and v['status'] == 'ALLOW']
            if decision['plans'] and ranks:
                ranked.append((max(ranks), candidate['candidate_key'], candidate))
        ranked.sort(key=lambda v: v[1])
        ranked.sort(key=lambda v: v[0], reverse=True)
        remaining, plans = set(scope), []
        for _, _, candidate in ranked:
            if not remaining:
                break
            result = self.evaluate(candidate, current, sorted(remaining), **kwargs)
            for plan in result['plans']:
                plans.append(plan)
                remaining.difference_update(plan['targets'])
        return plans
