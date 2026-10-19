"""Optional Redis coordination for the durable database-backed job queue.

The database remains the source of truth. Redis only wakes workers quickly and
provides a short lease so multiple worker processes do not perform the same
queue sweep at once.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator


logger = logging.getLogger(__name__)
WAKE_KEY = "understandvideo:jobs:wake"
LEASE_KEY = "understandvideo:jobs:sweep"


@lru_cache(maxsize=1)
def redis_client():
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=5)
        client.ping()
        return client
    except Exception:
        logger.warning("Redis is unavailable; durable job polling remains active", exc_info=True)
        return None


def notify_jobs() -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.lpush(WAKE_KEY, "1")
        client.ltrim(WAKE_KEY, 0, 99)
    except Exception:
        logger.warning("Could not notify Redis worker; database polling will recover", exc_info=True)


def wait_for_jobs(timeout_seconds: int) -> None:
    client = redis_client()
    if client is None:
        import time

        time.sleep(timeout_seconds)
        return
    try:
        client.brpop(WAKE_KEY, timeout=max(1, timeout_seconds))
    except Exception:
        logger.warning("Redis wait failed; reverting to timed polling", exc_info=True)
        redis_client.cache_clear()


@contextmanager
def worker_sweep_lease(ttl_seconds: int = 60) -> Iterator[bool]:
    client = redis_client()
    if client is None:
        yield True
        return
    token = secrets.token_hex(16)
    try:
        acquired = bool(client.set(LEASE_KEY, token, nx=True, ex=max(10, ttl_seconds)))
        yield acquired
    finally:
        if client is not None:
            try:
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    LEASE_KEY,
                    token,
                )
            except Exception:
                logger.warning("Could not release Redis worker lease", exc_info=True)
