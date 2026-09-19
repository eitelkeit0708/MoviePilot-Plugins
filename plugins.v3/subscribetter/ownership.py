"""Durable native-shell handoff; host calls deliberately occur outside SQLite transactions."""
from __future__ import annotations

import hashlib
import json
from threading import RLock
from .repository import Repository, Target, snapshot_config


class Ownership:
    def __init__(self, repository: Repository, adapter):
        self.repository = repository
        self.adapter = adapter
        # ponytail: serialize shell mutations; per-task locks if management throughput matters.
        self.lock = RLock()

    @staticmethod
    def matches(target: Target, native: dict) -> bool:
        return (native.get("type") == target.media_type
                and native.get("media_source") == target.media_source
                and str(native.get("media_id")) == target.media_id
                and native.get("season") == target.season
                and (native.get("episode_group") or "") == target.episode_group)

    def submit(self, intent_key: str, target: Target, snapshot: dict, actor: str,
               native_id: int | None = None, adopt: bool = False) -> dict:
        with self.lock:
            if native_id is not None:
                if not adopt:
                    raise ValueError("explicit adoption required")
                existing = self.repository.by_native_id(native_id)
                if existing:
                    # Use the original snapshot for idempotent intent fingerprints.
                    snapshot = existing["snapshot"]
                else:
                    native = self.adapter.get(native_id)
                    if not native or not self.matches(target, native):
                        raise ValueError("native identity/scope mismatch")
                    snapshot = snapshot_config(native)
            row = self.repository.submit(intent_key, target, snapshot, actor, native_id, adopt)
            if row["state"] == "PENDING":
                self._handoff(row)
            return self.repository.get_task(row["id"])

    def _handoff(self, task: dict):
        task_id = task["id"]
        target = Target.from_task(task)
        try:
            if task["native_id"] is None:
                action = self.repository.get_action(task_id)
                if not action or action["state"] != "PENDING":
                    return
                if any(self.matches(target, row) for row in self.adapter.find(target)):
                    self.repository.action_state(task_id, "PENDING", "NATIVE_CONFLICT", expected_state="PENDING")
                    return
                if not self.repository.start_create(task_id):
                    return
                sid = self.adapter.create(target, task["snapshot"])
                self.repository.bind_native(task_id, sid)
                task = self.repository.get_task(task_id)
            sid = task["native_id"]
            native = self.adapter.get(sid)
            if not native:
                self.repository.set_state(task_id, "STOPPED", "native-deleted")
                return
            if not self.matches(target, native):
                self.repository.action_state(task_id, "PENDING", "NATIVE_IDENTITY_MISMATCH")
                return
            if native["state"] != "S":
                self.adapter.pause(sid)
            verified = self.adapter.get(sid)
            if not verified or verified["state"] != "S" or not self.matches(target, verified):
                self.repository.action_state(task_id, "PENDING", "HANDOFF_READBACK_FAILED")
                return
            self.repository.complete_handoff(task_id, task["generation"])
        except Exception:
            # Error bodies can contain host URLs/credentials. Persist a bounded code only.
            current = self.repository.get_task(task_id)
            # UNKNOWN belongs to the durable dispatch record, not this invocation.
            # Without a bound ID only still-PENDING actions may remain retryable.
            self.repository.action_state(task_id, "PENDING", "HOST_UNAVAILABLE",
                                         expected_state="PENDING" if current["native_id"] is None else None)

    def reconcile(self):
        with self.lock:
            progress = self.repository.setting("ownership_outbox_progress")
            if progress is None:
                progress = {"after_id": 0, "through_id": self.repository.action_high_watermark()}
            actions = self.repository.pending_actions(**progress)
            if not actions:
                # Freeze the cycle tail so continuous arrivals cannot starve retries.
                progress = {"after_id": 0, "through_id": self.repository.action_high_watermark()}
                actions = self.repository.pending_actions(**progress)
            for action in actions:
                task = self.repository.get_task(action["task_id"])
                if task["state"] == "PENDING":
                    self._handoff(task)
                elif task["state"] == "RELEASING":
                    self._release(task)
                progress["after_id"] = action["id"]
                self.repository.setting("ownership_outbox_progress", progress)

    def recover_native(self, task_id: int, native_id: int, actor: str) -> dict:
        """Explicit administrator adoption resolves a lost create response without guessing."""
        with self.lock:
            task = self.repository.get_task(task_id)
            if not task or task["state"] != "PENDING" or task["native_id"] is not None:
                raise ValueError("task does not have an unresolved create")
            native = self.adapter.get(native_id)
            if not native or not self.matches(Target.from_task(task), native):
                raise ValueError("native identity/scope mismatch")
            self.repository.recover_native(task_id, native_id, actor, snapshot_config(native))
            self._handoff(self.repository.get_task(task_id))
            return self.repository.get_task(task_id)

    def ensure_paused(self) -> list[int]:
        """Existing ownership is independent of the ordinary-work enable switch."""
        failed = []
        with self.lock:
            offset = 0
            while tasks := self.repository.list_tasks(100, offset):
                offset += len(tasks)
                for task in tasks:
                    if task["state"] == "RELEASED_NATIVE" or task["native_id"] is None:
                        continue
                    try:
                        native = self.adapter.get(task["native_id"])
                        if not native:
                            self.repository.set_state(task["id"], "STOPPED", "native-deleted")
                        elif not self.matches(Target.from_task(task), native):
                            failed.append(task["id"])
                        elif native["state"] != "S":
                            self.adapter.pause(task["native_id"])
                            verified = self.adapter.get(task["native_id"])
                            if not verified or verified["state"] != "S":
                                failed.append(task["id"])
                    except Exception:
                        failed.append(task["id"])
        return failed

    def release_preview(self, task_id: int) -> dict:
        task = self.repository.get_task(task_id)
        if not task or not task["native_id"] or task["state"] in {"RELEASED_NATIVE", "RELEASING"}:
            raise ValueError("no releasable native shell")
        current = self.adapter.get(task["native_id"])
        if not current or not self.matches(Target.from_task(task), current):
            raise ValueError("native identity/scope changed")
        current = snapshot_config(current)
        revision = hashlib.sha256(json.dumps([task["generation"], current], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # Never expose snapshot values: native keywords/paths may contain private data.
        changed = sorted(key for key in set(current) | set(task["snapshot"]) if current.get(key) != task["snapshot"].get(key))
        return {"task_id": task_id, "revision": revision, "changed_fields": changed,
                "resume_state": self._resume_state(task), "restores_old_filters": False}

    @staticmethod
    def _resume_state(task: dict) -> str:
        state = task["snapshot"].get("state")
        return state if state in {"N", "R", "P", "S"} else "R"

    def release(self, task_id: int, revision: str, actor: str) -> dict:
        with self.lock:
            preview = self.release_preview(task_id)
            if preview["revision"] != revision:
                raise ValueError("release preview is stale")
            task = self.repository.get_task(task_id)
            self.repository.begin_release(task_id, task["generation"], actor)
            self._release(self.repository.get_task(task_id))
            return self.repository.get_task(task_id)

    def _release(self, task: dict):
        try:
            native = self.adapter.get(task["native_id"])
            if not native or not self.matches(Target.from_task(task), native):
                self.repository.action_state(task["id"], "PENDING", "NATIVE_IDENTITY_MISMATCH")
                return
            state = self._resume_state(task)
            self.adapter.set_state(task["native_id"], state)
            verified = self.adapter.get(task["native_id"])
            if not verified or verified["state"] != state or not self.matches(Target.from_task(task), verified):
                self.repository.action_state(task["id"], "UNKNOWN", "RELEASE_READBACK_FAILED")
                return
            self.repository.complete_release(task["id"], task["generation"])
        except Exception:
            self.repository.action_state(task["id"], "UNKNOWN", "HOST_UNAVAILABLE")


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def write_output(data, **values):
    for name, value in values.items():
        if isinstance(data, dict):
            data[name] = value
        else:
            setattr(data, name, value)


class Guard:
    """Origin identifies a host subscription route; it never grants execution permission."""
    def __init__(self, repository: Repository, adapter, auto_scope):
        self.repository = repository
        self.adapter = adapter
        self.auto_scope = auto_scope
        self.known_ids = set()
        self.unhealthy = False
        self.refresh_cache()

    def refresh_cache(self):
        ids = set()
        offset = 0
        while rows := self.repository.list_tasks(1000, offset):
            offset += len(rows)
            ids.update(row["native_id"] for row in rows if row["native_id"] is not None and row["state"] != "RELEASED_NATIVE")
        self.known_ids = ids

    def _owned_native(self, native: dict) -> bool:
        sid = native.get("id")
        if type(sid) is not int or sid <= 0:
            raise ValueError("invalid native subscription id")
        task = self.repository.by_native_id(sid)
        if task:
            # An identity mismatch is a reason to refuse, never a reason to release ownership.
            if task["state"] != "RELEASED_NATIVE":
                self.known_ids.add(sid)
                return True
            self.known_ids.discard(sid)
            return False
        current = self.adapter.get(sid)
        return bool(current and self.auto_scope(current))

    def _safe_owned(self, native: dict) -> bool:
        try:
            return self._owned_native(native)
        except Exception:
            self.unhealthy = True
            return native.get("id") in self.known_ids or self.auto_scope(native)

    @staticmethod
    def _source(event) -> dict | None:
        origin = field(event.event_data, "origin")
        if not isinstance(origin, str) or not origin.startswith("Subscribe|"):
            return None
        if len(origin) > 16384:
            raise ValueError("invalid subscription origin")
        native = json.loads(origin.split("|", 1)[1])
        if not isinstance(native, dict):
            raise ValueError("invalid subscription origin")
        return native

    def _route(self, event) -> tuple[bool, dict | None]:
        try:
            native = self._source(event)
            if native is None:
                return False, None
            return self._safe_owned(native), native
        except Exception:
            self.unhealthy = True
            return False, None

    @staticmethod
    def _other_identity(context, native: dict) -> bool:
        media = field(context, "media_info")
        # Only positively identified unrelated contexts are retained on an owned route.
        source, mid, mtype = field(media, "media_source"), field(media, "media_id"), field(media, "type")
        source, mtype = field(source, "value", source), field(mtype, "value", mtype)
        return bool(source and mid and mtype and (source != native.get("media_source") or str(mid) != str(native.get("media_id")) or mtype != native.get("type")))

    def selection(self, event):
        block, native = self._route(event)
        if not block:
            return
        data = event.event_data
        # Deny first: host dispatcher swallows listener exceptions.
        prior = field(data, "updated_contexts") if field(data, "updated") else field(data, "contexts", [])
        write_output(data, updated=True, updated_contexts=[], source="SubscriBetter")
        try:
            snapshot = event.snapshot()
            if not snapshot.valid or native is None:
                return
            originals = field(data, "contexts", [])
            snapshots = snapshot.input.contexts
            if len(originals) != len(snapshots):
                return
            allowed_ids = {id(original) for original, checked in zip(originals, snapshots) if self._other_identity(checked, native)}
            write_output(data, updated_contexts=[context for context in (prior or []) if id(context) in allowed_ids])
        except Exception:
            return

    def download(self, event):
        block, _ = self._route(event)
        if block:
            write_output(event.event_data, cancel=True, source="SubscriBetter", reason="Managed subscription requires a persisted execution plan")
            # Validate input independently; a malformed snapshot never undoes the veto.
            try:
                event.snapshot()
            except Exception:
                pass

    def completion(self, event):
        data = event.event_data
        native = field(data, "subscribe")
        if native is None:
            return
        try:
            sid = field(native, "id")
            blocked = self._safe_owned({"id": sid, "type": field(native, "type")})
        except Exception:
            self.unhealthy = True
            blocked = False
        if blocked:
            write_output(data, cancel=True, source="SubscriBetter", reason="Managed subscription completion is controlled by SubscriBetter")
            try:
                event.snapshot()
            except Exception:
                pass
