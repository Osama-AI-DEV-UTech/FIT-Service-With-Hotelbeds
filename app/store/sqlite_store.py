"""
SQLite persistence (aiosqlite, WAL mode) — packages + Hotelbeds content cache.

Kept behind two tiny interfaces (PackageStore / KeyValueCache) so a
Postgres/DynamoDB implementation can replace it without touching the
FIT engine.
"""
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite
import structlog

logger = structlog.get_logger("store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    package_id      TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    request_json    TEXT NOT NULL,
    package_json    TEXT NOT NULL,
    booking_json    TEXT,
    correlation_id  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packages_created ON packages(created_at);
CREATE INDEX IF NOT EXISTS idx_packages_status ON packages(status);

CREATE TABLE IF NOT EXISTS content_cache (
    cache_key   TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    def __init__(self, path: str):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("store_connected", path=self.path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store not connected")
        return self._db

    # ── packages ─────────────────────────────────────────────────────
    async def save_package(self, *, package_id: str, status: str, request: dict, package: dict,
                           correlation_id: Optional[str], expires_at: str) -> None:
        now = _now_iso()
        await self.db.execute(
            """INSERT INTO packages (package_id, status, request_json, package_json, booking_json,
                                     correlation_id, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)
               ON CONFLICT(package_id) DO UPDATE SET status=excluded.status, request_json=excluded.request_json,
                   package_json=excluded.package_json, updated_at=excluded.updated_at, expires_at=excluded.expires_at""",
            (package_id, status, json.dumps(request, default=str), json.dumps(package, default=str),
             correlation_id, now, now, expires_at),
        )
        await self.db.commit()

    async def get_package(self, package_id: str) -> Optional[dict]:
        async with self.db.execute("SELECT * FROM packages WHERE package_id = ?", (package_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "packageId": row["package_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "package": json.loads(row["package_json"]),
            "booking": json.loads(row["booking_json"]) if row["booking_json"] else None,
            "correlationId": row["correlation_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "expiresAt": row["expires_at"],
        }

    async def update_package(self, package_id: str, *, status: Optional[str] = None,
                             package: Optional[dict] = None, booking: Optional[dict] = None) -> None:
        sets, args = ["updated_at = ?"], [_now_iso()]
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if package is not None:
            sets.append("package_json = ?")
            args.append(json.dumps(package, default=str))
        if booking is not None:
            sets.append("booking_json = ?")
            args.append(json.dumps(booking, default=str))
        args.append(package_id)
        await self.db.execute(f"UPDATE packages SET {', '.join(sets)} WHERE package_id = ?", args)
        await self.db.commit()

    async def list_packages(self, *, limit: int = 20, offset: int = 0, status: Optional[str] = None) -> list[dict]:
        sql = "SELECT package_id, status, created_at, updated_at, expires_at, package_json FROM packages"
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        out = []
        async with self.db.execute(sql, args) as cur:
            async for row in cur:
                pkg = json.loads(row["package_json"])
                summary = (pkg.get("displayPackage") or {}).get("summary") or {}
                out.append({
                    "packageId": row["package_id"], "status": row["status"], "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"], "expiresAt": row["expires_at"],
                    "title": summary.get("title"), "startDate": summary.get("startDate"),
                    "endDate": summary.get("endDate"),
                    "grandTotal": ((pkg.get("displayPackage") or {}).get("pricing") or {}).get("grandTotal"),
                })
        return out

    # ── key/value cache ──────────────────────────────────────────────
    async def cache_get(self, key: str) -> Optional[Any]:
        async with self.db.execute("SELECT value_json, expires_at FROM content_cache WHERE cache_key = ?",
                                   (key,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] < time.time():
            return None
        return json.loads(row["value_json"])

    async def cache_set(self, key: str, value: Any, ttl_seconds: float) -> None:
        await self.db.execute(
            """INSERT INTO content_cache (cache_key, value_json, expires_at) VALUES (?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json, expires_at=excluded.expires_at""",
            (key, json.dumps(value, default=str), time.time() + ttl_seconds),
        )
        await self.db.commit()

    async def cache_purge(self, prefix: Optional[str] = None) -> int:
        if prefix:
            cur = await self.db.execute("DELETE FROM content_cache WHERE cache_key LIKE ?", (f"{prefix}%",))
        else:
            cur = await self.db.execute("DELETE FROM content_cache")
        await self.db.commit()
        return cur.rowcount or 0
