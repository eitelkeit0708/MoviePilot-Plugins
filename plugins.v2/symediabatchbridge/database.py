"""Retry whole, database-only operations after their connections have closed."""
from functools import wraps
import sqlite3
import time


BUSY_TIMEOUT = 10.0
CONNECT_TIMEOUT = 0.25


class StoreBusy(sqlite3.OperationalError):
    """The bounded attempt ended; durable work remains queued for a later tick."""


def is_busy(error):
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return str(error).lower() in ("database is locked", "database table is locked", "database schema is locked")


def database_operation(method):
    # These methods contain SQLite work only. Never decorate an upload, move,
    # post_message or other external side effect: only rolled-back SQL is replayed.
    @wraps(method)
    def run(*args, **kwargs):
        until = time.monotonic() + BUSY_TIMEOUT
        while True:
            try:
                return method(*args, **kwargs)
            except sqlite3.OperationalError as error:
                if isinstance(error, StoreBusy) or not is_busy(error):
                    raise
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise StoreBusy("插件数据库暂时忙，已保留队列，稍后自动重试") from None
                # The failed method has exited its transaction and closed its
                # connection before waiting. Do not retain a blocking read lock.
                time.sleep(min(0.05, remaining))
    return run
