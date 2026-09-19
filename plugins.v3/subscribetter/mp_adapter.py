"""Narrow V3 public Chain/Oper/SDK adapter. No stored host ORM objects."""
from __future__ import annotations

import inspect
import sys
from app.sdk.media import normalize_media_source, resolve_media_identity
from app.chain.subscribe import SubscribeChain
from app.db.oper.subscribe import SubscribeOper
from app.schemas.types import MediaType
from .repository import Target, SNAPSHOT_FIELDS


def make_target(media_type: str, media_source: str, media_id: str, season: int | None = None,
                episode_group: str = "") -> Target:
    source, mid = resolve_media_identity(media_source=normalize_media_source(media_source), media_id=media_id)
    if source is None or mid is None:
        raise ValueError("invalid media identity")
    return Target(media_type, source.value, mid, season, episode_group)


def target_from_native(native: dict) -> Target:
    return make_target(native["type"], native["media_source"], str(native["media_id"]),
                       native.get("season"), native.get("episode_group") or "")


class NativeAdapter:
    @staticmethod
    def classify(media) -> dict:
        """Refresh via public SDK, outside Meta hooks and synchronous veto events."""
        from copy import deepcopy
        from app.sdk.classification import classify_media

        subject = deepcopy(media)
        previous = getattr(subject, "classification", None)
        if previous is not None:
            # The SDK returns a deepcopy without evaluating if not assembled. Mark
            # only evaluation stale; preserve explicit manual/subscription selection.
            subject.classification = previous.model_copy(
                deep=True, update={"state": "not_evaluated", "policy_revision": 0})
        result = getattr(classify_media(subject), "classification", None)
        return result.model_dump(mode="json") if result is not None else {
            "state": "not_evaluated", "policy_revision": 0, "effective": None}

    @staticmethod
    def capabilities() -> list[str]:
        failures = []
        if sys.version_info < (3, 14):
            failures.append("HOST_PYTHON_REQUIRES_3_14")
        required = ((SubscribeChain.add, {"title", "year", "media_source", "media_id", "season", "episode_group"}),
                    (SubscribeOper.update, {"sid", "payload"}), (SubscribeOper.get, {"sid"}),
                    (SubscribeOper.list_by_media_identity, {"media_source", "media_id"}))
        for method, parameters in required:
            if not parameters <= set(inspect.signature(method).parameters):
                failures.append("HOST_CONTRACT_MISMATCH")
        return sorted(set(failures))

    @staticmethod
    def snapshot(row) -> dict | None:
        if row is None:
            return None
        values = {key: getattr(row, key, None) for key in SNAPSHOT_FIELDS}
        source, mid = resolve_media_identity(media=row)
        values.update(id=row.id, media_source=source.value if source else None, media_id=mid)
        values["type"] = getattr(values["type"], "value", values["type"])
        return values

    def get(self, sid: int) -> dict | None:
        return self.snapshot(SubscribeOper().get(sid))

    def list(self) -> list[dict]:
        return [self.snapshot(row) for row in SubscribeOper().list()]

    def find(self, target: Target) -> list[dict]:
        return [self.snapshot(row) for row in SubscribeOper().list_by_media_identity(
            media_source=normalize_media_source(target.media_source), media_id=target.media_id)]

    def create(self, target: Target, snapshot: dict) -> int:
        sid, message = SubscribeChain().add(
            title=snapshot.get("name") or target.media_id, year=snapshot.get("year") or "",
            mtype=MediaType(target.media_type), media_source=normalize_media_source(target.media_source),
            media_id=target.media_id, season=target.season, episode_group=target.episode_group or None,
            username=snapshot.get("username"), state="S", exist_ok=False, message=False,
        )
        # Fixed V3 returns a description on success AND a positive ID for duplicates.
        # Only confirmed new creation grants ownership; other outcomes need recovery.
        if type(sid) is not int or sid <= 0 or message != "新增订阅成功":
            raise RuntimeError("native subscription creation not acknowledged")
        return sid

    @staticmethod
    def pause(sid: int):
        SubscribeOper().update(sid, {"state": "S"})

    @staticmethod
    def set_state(sid: int, state: str):
        if state not in {"S", "R", "N", "P"}:
            raise ValueError("invalid native state")
        SubscribeOper().update(sid, {"state": state})
