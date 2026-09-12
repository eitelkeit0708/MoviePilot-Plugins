"""Bounded, configuration-local TTL cache with single-flight and clear fencing."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import RLock
from time import monotonic


class TTLCache:
    """Loader returns (value, TTL seconds); None is a cacheable negative value."""

    def __init__(self, maxsize=1000, timer=monotonic):
        self._maxsize = max(1, int(maxsize))
        self._timer = timer
        self._lock = RLock()
        self._entries = OrderedDict()
        self._pending = {}
        self._generation = 0
        self._hits = self._misses = self._coalesced = 0

    def get_or_load(self, key, loader, timeout):
        """Never hold the cache lock during external I/O or while waiting."""
        with self._lock:
            now = self._timer()
            expired = [k for k, (expiry, _) in self._entries.items() if expiry <= now]
            for k in expired:
                self._entries.pop(k, None)
            if key in self._entries:
                self._hits += 1
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key][1])
            flight_key = (self._generation, key)
            future = self._pending.get(flight_key)
            leader = future is None
            if leader:
                future = self._pending[flight_key] = Future()
                self._misses += 1
            else:
                self._coalesced += 1
        if not leader:
            return deepcopy(future.result(timeout=max(0, timeout)))
        try:
            value, ttl = loader()
            with self._lock:
                if self._generation == flight_key[0] and ttl > 0:
                    self._entries[key] = (self._timer() + ttl, deepcopy(value))
                    self._entries.move_to_end(key)
                    while len(self._entries) > self._maxsize:
                        self._entries.popitem(last=False)
            future.set_result(value)
            return deepcopy(value)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(flight_key, None)

    def clear(self):
        """In-flight work may finish, but cannot repopulate a cleared generation."""
        with self._lock:
            self._generation += 1
            self._entries.clear()

    def stats(self):
        with self._lock:
            return {'hits': self._hits, 'misses': self._misses, 'coalesced': self._coalesced,
                    'size': len(self._entries), 'inflight': len(self._pending)}
