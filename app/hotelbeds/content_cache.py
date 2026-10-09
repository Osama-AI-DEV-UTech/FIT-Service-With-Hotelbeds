"""
Two-level cache for Hotelbeds CONTENT data (destinations, terminals,
hotel descriptions/images, master data).

Hotelbeds explicitly says content must be stored locally and refreshed
(at least weekly) rather than called in real time — and evaluation keys
only get 50 calls/day. Level 1 is an in-process dict, level 2 is the
SQLite `content_cache` table so the cache survives restarts. A per-key
lock gives single-flight behaviour: 10 concurrent FIT requests for Dubai
trigger ONE destinations download, not ten.

Never use this for availability/prices/rateKeys — those must be live.
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

import structlog

from app.core.context import current_ledger

logger = structlog.get_logger("content_cache")


class ContentCache:
    def __init__(self, store, default_ttl_seconds: float):
        self._store = store
        self._ttl = default_ttl_seconds
        self._mem: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[Any]],
                          ttl_seconds: Optional[float] = None, product: str = "content") -> Any:
        ttl = ttl_seconds or self._ttl
        hit = self._mem_get(key)
        if hit is not None:
            self._note_hit(product, key)
            return hit

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._mem_get(key)
            if hit is not None:
                self._note_hit(product, key)
                return hit
            if self._store is not None:
                stored = await self._store.cache_get(key)
                if stored is not None:
                    self._mem[key] = (time.time() + ttl, stored)
                    self._note_hit(product, key)
                    return stored
            value = await loader()
            if value is not None:
                self._mem[key] = (time.time() + ttl, value)
                if self._store is not None:
                    await self._store.cache_set(key, value, ttl)
            return value

    async def get(self, key: str, product: str = "content") -> Any:
        hit = self._mem_get(key)
        if hit is None and self._store is not None:
            hit = await self._store.cache_get(key)
            if hit is not None:
                self._mem[key] = (time.time() + self._ttl, hit)
        if hit is not None:
            self._note_hit(product, key)
        return hit

    async def put(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        ttl = ttl_seconds or self._ttl
        self._mem[key] = (time.time() + ttl, value)
        if self._store is not None:
            await self._store.cache_set(key, value, ttl)

    async def purge(self, prefix: Optional[str] = None) -> int:
        if prefix:
            for k in [k for k in self._mem if k.startswith(prefix)]:
                self._mem.pop(k, None)
        else:
            self._mem.clear()
        return await self._store.cache_purge(prefix) if self._store is not None else 0

    def _mem_get(self, key: str) -> Any:
        entry = self._mem.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        if entry:
            self._mem.pop(key, None)
        return None

    @staticmethod
    def _note_hit(product: str, key: str) -> None:
        ledger = current_ledger()
        if ledger is not None:
            ledger.record(product, "CACHE", key, 200, 0.0, cached=True)
