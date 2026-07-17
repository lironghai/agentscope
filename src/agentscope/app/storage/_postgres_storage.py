# -*- coding: utf-8 -*-
"""PostgreSQL storage implementation for the Agent Service.

The service storage contract is already expressed by
:class:`RedisStorage`: records are persisted as JSON strings, with
secondary indexes represented as sets and message histories represented
as ordered lists.  This module provides the same data model on
PostgreSQL so deployments can persist service state in PG without
rewriting the higher-level Agent Service code.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Self

from ._redis_storage import RedisStorage


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(value: str, *, label: str) -> str:
    """Validate a SQL identifier fragment used for generated table names."""
    if not _IDENT_RE.fullmatch(value):
        raise ValueError(f"{label} must be a valid SQL identifier.")
    return value


def _parse_status_count(status: str) -> int:
    """Return the row count from an asyncpg command status string."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (IndexError, ValueError):
        return 0


class _PostgresKVAdapter:
    """Small Redis-like adapter backed by PostgreSQL tables."""

    def __init__(self, pool: Any, *, table_prefix: str) -> None:
        self._pool = pool
        prefix = _validate_ident(table_prefix, label="table_prefix")
        self._kv_table = f"{prefix}_kv"
        self._set_table = f"{prefix}_sets"
        self._list_table = f"{prefix}_lists"

    async def ensure_schema(self) -> None:
        """Create the backing tables if they are missing."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._kv_table} (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NULL
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._set_table} (
                    key TEXT NOT NULL,
                    member TEXT NOT NULL,
                    PRIMARY KEY (key, member)
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._list_table} (
                    key TEXT NOT NULL,
                    idx BIGINT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NULL,
                    PRIMARY KEY (key, idx)
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._list_table}_key_idx
                ON {self._list_table} (key, idx)
                """,
            )

    async def get(self, key: str) -> str | None:
        """Return a string value, ignoring expired keys."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"""
                SELECT value
                FROM {self._kv_table}
                WHERE key = $1
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                key,
            )

    async def set(self, key: str, value: str) -> None:
        """Set a string value and clear any previous TTL."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._kv_table} (key, value, expires_at)
                VALUES ($1, $2, NULL)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    expires_at = NULL
                """,
                key,
                value,
            )

    async def delete(self, key: str) -> int:
        """Delete a key from all PG-backed Redis-like structures."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                count = 0
                count += _parse_status_count(
                    await conn.execute(
                        f"DELETE FROM {self._kv_table} WHERE key = $1",
                        key,
                    ),
                )
                count += _parse_status_count(
                    await conn.execute(
                        f"DELETE FROM {self._set_table} WHERE key = $1",
                        key,
                    ),
                )
                count += _parse_status_count(
                    await conn.execute(
                        f"DELETE FROM {self._list_table} WHERE key = $1",
                        key,
                    ),
                )
                return count

    async def expire(self, key: str, seconds: int) -> bool:
        """Set a sliding TTL on string/list keys."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                kv = _parse_status_count(
                    await conn.execute(
                        f"""
                        UPDATE {self._kv_table}
                        SET expires_at = $2
                        WHERE key = $1
                        """,
                        key,
                        expires_at,
                    ),
                )
                rows = _parse_status_count(
                    await conn.execute(
                        f"""
                        UPDATE {self._list_table}
                        SET expires_at = $2
                        WHERE key = $1
                        """,
                        key,
                        expires_at,
                    ),
                )
                return kv + rows > 0

    async def ttl(self, key: str) -> int:
        """Return an approximate TTL in seconds, Redis-style."""
        async with self._pool.acquire() as conn:
            expires_at = await conn.fetchval(
                f"""
                SELECT expires_at
                FROM {self._kv_table}
                WHERE key = $1
                UNION ALL
                SELECT max(expires_at)
                FROM {self._list_table}
                WHERE key = $1
                LIMIT 1
                """,
                key,
            )
        if expires_at is None:
            return -1
        remaining = expires_at - datetime.now(timezone.utc)
        return max(0, int(remaining.total_seconds()))

    async def sadd(self, key: str, *members: str) -> int:
        """Add members to a set."""
        if not members:
            return 0
        async with self._pool.acquire() as conn:
            before = await conn.fetchval(
                f"SELECT count(*) FROM {self._set_table} WHERE key = $1",
                key,
            )
            await conn.executemany(
                f"""
                INSERT INTO {self._set_table} (key, member)
                VALUES ($1, $2)
                ON CONFLICT (key, member) DO NOTHING
                """,
                [(key, member) for member in members],
            )
            after = await conn.fetchval(
                f"SELECT count(*) FROM {self._set_table} WHERE key = $1",
                key,
            )
        return int(after or 0) - int(before or 0)

    async def smembers(self, key: str) -> set[str]:
        """Return all members from a set."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT member FROM {self._set_table} WHERE key = $1",
                key,
            )
        return {row["member"] for row in rows}

    async def srem(self, key: str, *members: str) -> int:
        """Remove members from a set."""
        if not members:
            return 0
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                f"""
                DELETE FROM {self._set_table}
                WHERE key = $1 AND member = ANY($2::text[])
                """,
                key,
                list(members),
            )
        return _parse_status_count(status)

    async def rpush(self, key: str, *values: str) -> int:
        """Append values to a list."""
        if not values:
            return await self.llen(key)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._delete_expired_list_rows(conn, key)
                start = await conn.fetchval(
                    f"""
                    SELECT COALESCE(max(idx), -1) + 1
                    FROM {self._list_table}
                    WHERE key = $1
                    """,
                    key,
                )
                await conn.executemany(
                    f"""
                    INSERT INTO {self._list_table} (key, idx, value)
                    VALUES ($1, $2, $3)
                    """,
                    [
                        (key, int(start) + offset, value)
                        for offset, value in enumerate(values)
                    ],
                )
                return await conn.fetchval(
                    f"SELECT count(*) FROM {self._list_table} WHERE key = $1",
                    key,
                )

    async def llen(self, key: str) -> int:
        """Return list length."""
        async with self._pool.acquire() as conn:
            await self._delete_expired_list_rows(conn, key)
            value = await conn.fetchval(
                f"""
                SELECT count(*)
                FROM {self._list_table}
                WHERE key = $1
                """,
                key,
            )
        return int(value or 0)

    async def lindex(self, key: str, index: int) -> str | None:
        """Return one list entry by index."""
        async with self._pool.acquire() as conn:
            await self._delete_expired_list_rows(conn, key)
            if index < 0:
                length = await conn.fetchval(
                    f"SELECT count(*) FROM {self._list_table} WHERE key = $1",
                    key,
                )
                index = int(length or 0) + index
            return await conn.fetchval(
                f"""
                SELECT value
                FROM {self._list_table}
                WHERE key = $1
                  AND idx = $2
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                key,
                index,
            )

    async def lset(self, key: str, index: int, value: str) -> None:
        """Replace one list entry by index."""
        async with self._pool.acquire() as conn:
            await self._delete_expired_list_rows(conn, key)
            if index < 0:
                length = await conn.fetchval(
                    f"SELECT count(*) FROM {self._list_table} WHERE key = $1",
                    key,
                )
                index = int(length or 0) + index
            await conn.execute(
                f"""
                UPDATE {self._list_table}
                SET value = $3
                WHERE key = $1 AND idx = $2
                """,
                key,
                index,
                value,
            )

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        """Return an inclusive list slice."""
        async with self._pool.acquire() as conn:
            await self._delete_expired_list_rows(conn, key)
            rows = await conn.fetch(
                f"""
                SELECT value
                FROM {self._list_table}
                WHERE key = $1
                  AND idx >= $2
                  AND idx <= $3
                ORDER BY idx ASC
                """,
                key,
                start,
                stop,
            )
        return [row["value"] for row in rows]

    async def _delete_expired_list_rows(self, conn: Any, key: str) -> None:
        """Remove expired rows for a list before Redis-like list operations."""
        await conn.execute(
            f"""
            DELETE FROM {self._list_table}
            WHERE key = $1
              AND expires_at IS NOT NULL
              AND expires_at <= now()
            """,
            key,
        )

    def pipeline(self, transaction: bool = True) -> "_PostgresPipeline":
        """Return a minimal pipeline object used by RedisStorage leases."""
        return _PostgresPipeline(self, transaction=transaction)


class _PostgresPipeline:
    """Minimal Redis pipeline compatibility layer for lease CAS methods."""

    def __init__(
        self,
        adapter: _PostgresKVAdapter,
        *,
        transaction: bool,
    ) -> None:
        self._adapter = adapter
        self._transaction_enabled = transaction
        self._conn: Any | None = None
        self._transaction: Any | None = None
        self._queued: list[Callable[[], Awaitable[Any]]] = []
        self._queue_mode = False

    async def __aenter__(self) -> Self:
        self._conn = await self._adapter._pool.acquire()
        if self._transaction_enabled:
            self._transaction = self._conn.transaction()
            await self._transaction.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        try:
            if self._transaction is not None:
                if exc_type is None:
                    await self._transaction.commit()
                else:
                    await self._transaction.rollback()
        finally:
            await self._adapter._pool.release(self._conn)
            self._conn = None

    async def watch(self, key: str) -> None:
        """Lock an existing row for the rest of the transaction if present."""
        await self._conn.fetchrow(
            f"""
            SELECT key
            FROM {self._adapter._kv_table}
            WHERE key = $1
            FOR UPDATE
            """,
            key,
        )

    async def unwatch(self) -> None:
        """Compatibility no-op."""

    async def get(self, key: str) -> str | None:
        """Read one string value using the pipeline connection."""
        return await self._conn.fetchval(
            f"""
            SELECT value
            FROM {self._adapter._kv_table}
            WHERE key = $1
              AND (expires_at IS NULL OR expires_at > now())
            """,
            key,
        )

    def multi(self) -> None:
        """Switch subsequent writes into queued mode."""
        self._queue_mode = True

    def set(self, key: str, value: str) -> None:
        """Queue or execute a string set."""
        async def _op() -> None:
            await self._conn.execute(
                f"""
                INSERT INTO {self._adapter._kv_table}
                    (key, value, expires_at)
                VALUES ($1, $2, NULL)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    expires_at = NULL
                """,
                key,
                value,
            )

        if self._queue_mode:
            self._queued.append(_op)
        else:
            raise RuntimeError("pipeline.set() must be called after multi().")

    def expire(self, key: str, seconds: int) -> None:
        """Queue or execute a TTL update."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        async def _op() -> None:
            await self._conn.execute(
                f"""
                UPDATE {self._adapter._kv_table}
                SET expires_at = $2
                WHERE key = $1
                """,
                key,
                expires_at,
            )

        if self._queue_mode:
            self._queued.append(_op)
        else:
            raise RuntimeError(
                "pipeline.expire() must be called after multi().",
            )

    async def execute(self) -> list[Any]:
        """Execute queued operations."""
        results: list[Any] = []
        for op in self._queued:
            results.append(await op())
        self._queued.clear()
        self._queue_mode = False
        return results


class PostgresStorage(RedisStorage):
    """PostgreSQL-backed Agent Service storage.

    The public behavior matches :class:`RedisStorage`; the underlying
    persistence uses PostgreSQL tables and the optional ``asyncpg``
    dependency.  This keeps existing Agent Service code paths intact
    while allowing deployments to use PG as the service-state database.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        host: str = "localhost",
        port: int = 5432,
        database: str = "agentscope",
        user: str | None = None,
        password: str | None = None,
        pool: Any | None = None,
        table_prefix: str = "agentscope",
        key_ttl: int | None = None,
        key_config: "RedisStorage.KeyConfig | None" = None,
        **kwargs: Any,
    ) -> None:
        """Store connection parameters for the PostgreSQL backend.

        Args:
            dsn (`str | None`, optional):
                PostgreSQL DSN, e.g.
                ``postgresql://user:pass@localhost:5432/agentscope``.
                When omitted, ``host`` / ``port`` / ``database`` /
                ``user`` / ``password`` are passed to ``asyncpg``.
            host (`str`, defaults to ``"localhost"``):
                PostgreSQL host when ``dsn`` is omitted.
            port (`int`, defaults to ``5432``):
                PostgreSQL port when ``dsn`` is omitted.
            database (`str`, defaults to ``"agentscope"``):
                PostgreSQL database when ``dsn`` is omitted.
            user (`str | None`, optional):
                PostgreSQL user when ``dsn`` is omitted.
            password (`str | None`, optional):
                PostgreSQL password when ``dsn`` is omitted.
            pool (`Any | None`, optional):
                Externally managed ``asyncpg`` pool. When provided, it
                is used as-is and not closed by :meth:`aclose`.
            table_prefix (`str`, defaults to ``"agentscope"``):
                Prefix for the generated ``*_kv``, ``*_sets`` and
                ``*_lists`` tables.
            key_ttl (`int | None`, optional):
                Sliding TTL in seconds for record/message keys.
            key_config (`RedisStorage.KeyConfig | None`, optional):
                Existing key template configuration.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to
                ``asyncpg.create_pool``.
        """
        # Initialize RedisStorage fields used by inherited methods
        # without creating a Redis client or pool.
        self._host = host
        self._port = port
        self._db = database
        self._password = password
        self._external_pool = pool
        self._kwargs = kwargs
        self.key_ttl = key_ttl
        self.key_config = key_config or RedisStorage.KeyConfig()

        self._dsn = dsn
        self._database = database
        self._user = user
        self._table_prefix = table_prefix
        self._client: _PostgresKVAdapter | None = None
        self._owned_pool: Any | None = None

    async def __aenter__(self) -> Self:
        """Create the asyncpg pool and backing tables if needed."""
        if self._external_pool is not None:
            pool = self._external_pool
        else:
            try:
                import asyncpg
            except ImportError as e:
                raise ImportError(
                    "The 'asyncpg' package is required for PostgresStorage. "
                    'Install it with: pip install "agentscope[postgres]"',
                ) from e

            connect_kwargs: dict[str, Any]
            if self._dsn is not None:
                connect_kwargs = {"dsn": self._dsn}
            else:
                connect_kwargs = {
                    "host": self._host,
                    "port": self._port,
                    "database": self._database,
                    "user": self._user,
                    "password": self._password,
                }
            self._owned_pool = await asyncpg.create_pool(
                **connect_kwargs,
                **self._kwargs,
            )
            pool = self._owned_pool

        self._client = _PostgresKVAdapter(
            pool,
            table_prefix=self._table_prefix,
        )
        await self._client.ensure_schema()
        return self

    async def aclose(self) -> None:
        """Close the owned asyncpg pool, if any."""
        if self._owned_pool is not None:
            await self._owned_pool.close()
            self._owned_pool = None
        self._client = None

    def get_client(self) -> _PostgresKVAdapter:
        """Return the PostgreSQL-backed Redis compatibility adapter."""
        return self._client
