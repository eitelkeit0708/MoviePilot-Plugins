"""One durable cooldown for this plugin's use of MP's native 115 account."""

import math
import time

from .domain import Awaiting, BridgeError


COOLDOWN_KEY = "u115_cloud_cooldown"
TRANSIENT_KEY = "u115_transient_pause"


class CloudRequestError(BridgeError):
    def __init__(self, message, *, kind="unknown", next_at=0, review=False):
        super().__init__(message, review=review)
        self.kind, self.next_at = kind, next_at


class CloudCooldown(Awaiting):
    def __init__(self, next_at, message="115 接口正在冷却，稍后自动继续"):
        super().__init__(message)
        self.kind, self.next_at = "rate_limit", next_at


class TransientPause(CloudCooldown):
    def __init__(self, next_at):
        super().__init__(next_at, "115/CD2 连接短暂抖动，已暂停云端访问，稍后自动核对")
        self.kind = "transient"


def deadline(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (ValueError, TypeError):
        return 0


class Cooldown:
    """Conservative u115 scope: all routes share one native host account.

    No token/account fingerprint is persisted. A credential change does not
    silently clear an active cooldown. Only an atomic max extends its deadline.
    """

    def __init__(self, store):
        self.store = store

    def defer(self, until, reason="115 接口限流"):
        return self.store.extend_cloud_cooldown(deadline(until), reason)

    def transient(self):
        return self.store.extend_transient_pause()

    def status(self, provider=None):
        state = self.store.meta(COOLDOWN_KEY, {})
        state = state if isinstance(state, dict) else {}
        if provider is not None:
            host_until = deadline(getattr(provider, "_limit_until", 0))
            if host_until > max(time.time(), deadline(state.get("until"))):
                state = self.defer(host_until, "MP 的 115 接口正在冷却")
        return state

    def before(self, provider=None):
        until = deadline(self.status(provider).get("until"))
        if until > time.time():
            raise CloudCooldown(until)
        until = deadline(self.store.meta(TRANSIENT_KEY, {}).get('until'))
        if until > time.time():
            raise TransientPause(until)
