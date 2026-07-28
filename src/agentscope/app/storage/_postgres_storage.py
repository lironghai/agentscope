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

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Self

from ._model import (
    AgentData,
    AgentMcpBinding,
    AgentRecord,
    AgentShareGrant,
    AgentSkillBinding,
    AgentToolConfig,
    ChatModelConfig,
    InviteConfig,
    SessionKnowledgeConfig,
    SessionConfig,
    SessionRecord,
    SessionSource,
    ShareConfig,
    TTSModelConfig,
    effective_agent_share_tier,
)
from ._redis_storage import RedisStorage
from ...agent import ContextConfig, ReActConfig
from ...message import ToolCallState
from ...state import AgentState


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _validate_ident(value: str, *, label: str) -> str:
    """Validate a SQL identifier fragment used for generated table names."""
    if not _IDENT_RE.fullmatch(value):
        raise ValueError(f"{label} must be a valid SQL identifier.")
    return value


def _key_template_to_sql_like(template: str) -> str:
    """Convert a Redis key template into an escaped SQL LIKE pattern."""
    parts: list[str] = []
    cursor = 0
    for match in _KEY_PLACEHOLDER_RE.finditer(template):
        literal = template[cursor : match.start()]
        parts.append(
            literal.replace("!", "!!").replace("%", "!%").replace("_", "!_"),
        )
        parts.append("%")
        cursor = match.end()
    tail = template[cursor:]
    parts.append(tail.replace("!", "!!").replace("%", "!%").replace("_", "!_"))
    return "".join(parts)


def _parse_status_count(status: str) -> int:
    """Return the row count from an asyncpg command status string."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (IndexError, ValueError):
        return 0


def _derive_persisted_session_status(state: AgentState) -> str:
    """Derive the persisted UI status from the stored agent state."""
    if not state.context:
        return "idle"
    last_msg = state.context[-1]
    if last_msg.role != "assistant":
        return "idle"
    tool_calls = last_msg.get_content_blocks("tool_call")
    if not tool_calls:
        return "idle"
    if any(tc.state == ToolCallState.ASKING for tc in tool_calls):
        return "awaiting_permission"
    if any(tc.state == ToolCallState.SUBMITTED for tc in tool_calls):
        return "awaiting_external_result"
    return "idle"


def _list_state_json(state: AgentState) -> str:
    """Return a small state payload suitable for session list responses."""
    return json.dumps(
        state.model_dump(
            mode="json",
            exclude={
                "context",
                "summary",
                "tool_context",
                "middle_context",
            },
        ),
    )


def _json_or_default(model_type: type, value: str | None) -> Any:
    """Deserialize an optional Pydantic JSON column with a model default."""
    if not value:
        return model_type()
    return model_type.model_validate_json(value)


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Return a row value from asyncpg records or dict-like test rows."""
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _json_or_none(model_type: type, value: str | None) -> Any | None:
    """Deserialize an optional Pydantic JSON column."""
    if not value:
        return None
    return model_type.model_validate_json(value)


def _json_dict_or_empty(value: Any) -> dict[str, Any]:
    """Return a dict from a JSON/JSONB row value."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


class _PostgresKVAdapter:
    """Small Redis-like adapter backed by PostgreSQL tables."""

    def __init__(self, pool: Any, *, table_prefix: str) -> None:
        self._pool = pool
        prefix = _validate_ident(table_prefix, label="table_prefix")
        self._kv_table = f"{prefix}_kv"
        self._set_table = f"{prefix}_sets"
        self._list_table = f"{prefix}_lists"
        self._agent_table = f"{prefix}_agents"
        self._agent_skill_binding_table = f"{prefix}_agent_skill_bindings"
        self._agent_mcp_binding_table = f"{prefix}_agent_mcp_bindings"
        self._agent_share_grant_table = f"{prefix}_agent_share_grants"
        self._session_table = f"{prefix}_sessions"

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
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._agent_table} (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_data_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    description TEXT NULL,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    context_config_json TEXT NOT NULL DEFAULT '{{}}',
                    react_config_json TEXT NOT NULL DEFAULT '{{}}',
                    invite_config_json TEXT NOT NULL DEFAULT '{{}}',
                    share_config_json TEXT NOT NULL DEFAULT '{{}}',
                    tool_config_json TEXT NOT NULL DEFAULT '{{}}',
                    shared BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    record_json TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, agent_id)
                )
                """,
            )
            await conn.execute(
                f"""
                ALTER TABLE {self._agent_table}
                    ADD COLUMN IF NOT EXISTS agent_data_id TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS agent_name TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS description TEXT NULL,
                    ADD COLUMN IF NOT EXISTS system_prompt TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS context_config_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS react_config_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS invite_config_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS share_config_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS tool_config_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS shared BOOLEAN NOT NULL DEFAULT false,
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._agent_table}_user_source_idx
                ON {self._agent_table} (user_id, source)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._agent_table}_user_shared_idx
                ON {self._agent_table} (user_id, shared)
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._agent_share_grant_table} (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    grantee_type TEXT NOT NULL,
                    grantee_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    created_by TEXT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (
                        user_id,
                        agent_id,
                        grantee_type,
                        grantee_id
                    )
                )
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_share_grant_table}_agent_idx
                ON {self._agent_share_grant_table} (user_id, agent_id)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_share_grant_table}_grantee_idx
                ON {self._agent_share_grant_table}
                    (grantee_type, grantee_id, tier)
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._agent_skill_binding_table} (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'skillhub',
                    skillhub_skill_id TEXT NULL,
                    namespace TEXT NOT NULL DEFAULT '',
                    slug TEXT NOT NULL DEFAULT '',
                    version TEXT NULL,
                    display_name TEXT NULL,
                    description TEXT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT true,
                    position INTEGER NOT NULL DEFAULT 0,
                    config_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, agent_id, binding_id)
                )
                """,
            )
            await conn.execute(
                f"""
                ALTER TABLE {self._agent_skill_binding_table}
                    ADD COLUMN IF NOT EXISTS position INTEGER NOT NULL DEFAULT 0
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_skill_binding_table}_agent_enabled_idx
                ON {self._agent_skill_binding_table}
                    (user_id, agent_id, enabled)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_skill_binding_table}_skillhub_idx
                ON {self._agent_skill_binding_table}
                    (skillhub_skill_id)
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._agent_mcp_binding_table} (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'skillhub',
                    skillhub_server_id TEXT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    provider TEXT NULL,
                    auth_type TEXT NULL,
                    transport TEXT NULL,
                    endpoint_url TEXT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT true,
                    position INTEGER NOT NULL DEFAULT 0,
                    config_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, agent_id, binding_id)
                )
                """,
            )
            await conn.execute(
                f"""
                ALTER TABLE {self._agent_mcp_binding_table}
                    ADD COLUMN IF NOT EXISTS position INTEGER NOT NULL DEFAULT 0
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_mcp_binding_table}_agent_enabled_idx
                ON {self._agent_mcp_binding_table}
                    (user_id, agent_id, enabled)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._agent_mcp_binding_table}_skillhub_idx
                ON {self._agent_mcp_binding_table}
                    (skillhub_server_id)
                """,
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._session_table} (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    source_schedule_id TEXT NULL,
                    workspace_id TEXT NOT NULL DEFAULT '',
                    workspace_owner_id TEXT NULL,
                    session_name TEXT NOT NULL DEFAULT '',
                    chat_model_config_json TEXT NULL,
                    fallback_chat_model_config_json TEXT NULL,
                    tts_model_config_json TEXT NULL,
                    knowledge_config_json TEXT NULL,
                    model_type TEXT NULL,
                    model_name TEXT NULL,
                    team_id TEXT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    state_json TEXT NOT NULL DEFAULT '{{}}',
                    state_summary_json TEXT NOT NULL DEFAULT '{{}}',
                    created_at TIMESTAMPTZ NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (user_id, session_id)
                )
                """,
            )
            await conn.execute(
                f"""
                ALTER TABLE {self._session_table}
                    ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS workspace_owner_id TEXT NULL,
                    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'user',
                    ADD COLUMN IF NOT EXISTS session_name TEXT NOT NULL DEFAULT '',
                    ADD COLUMN IF NOT EXISTS chat_model_config_json TEXT NULL,
                    ADD COLUMN IF NOT EXISTS fallback_chat_model_config_json TEXT NULL,
                    ADD COLUMN IF NOT EXISTS tts_model_config_json TEXT NULL,
                    ADD COLUMN IF NOT EXISTS knowledge_config_json TEXT NULL,
                    ADD COLUMN IF NOT EXISTS model_type TEXT NULL,
                    ADD COLUMN IF NOT EXISTS model_name TEXT NULL,
                    ADD COLUMN IF NOT EXISTS team_id TEXT NULL,
                    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'idle',
                    ADD COLUMN IF NOT EXISTS state_json TEXT NOT NULL DEFAULT '{{}}',
                    ADD COLUMN IF NOT EXISTS state_summary_json TEXT NOT NULL DEFAULT '{{}}'
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._session_table}_user_agent_created_idx
                ON {self._session_table} (user_id, agent_id, created_at DESC)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._session_table}_user_schedule_idx
                ON {self._session_table} (user_id, source_schedule_id)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._session_table}_user_agent_status_idx
                ON {self._session_table} (user_id, agent_id, status)
                """,
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                    {self._session_table}_user_workspace_idx
                ON {self._session_table} (user_id, workspace_id)
                """,
            )
            await self._backfill_agent_binding_positions(conn)

    async def _backfill_agent_binding_positions(self, conn: Any) -> None:
        """Backfill binding order from Agent record snapshots.

        The ``position`` column was added after the dedicated binding tables.
        Existing rows get PostgreSQL's default ``0`` and would otherwise be
        ordered by ``binding_id``. The compatibility ``record_json`` snapshot
        still carries the original list order, so use it as the migration
        source.
        """
        rows = await conn.fetch(
            f"""
            SELECT user_id, agent_id, record_json
            FROM {self._agent_table}
            """,
        )
        for row in rows:
            try:
                record = AgentRecord.model_validate_json(row["record_json"])
            except Exception:  # pylint: disable=broad-except
                continue
            for position, binding in enumerate(record.data.skill_bindings):
                await conn.execute(
                    f"""
                    UPDATE {self._agent_skill_binding_table}
                    SET position = $1
                    WHERE user_id = $2
                        AND agent_id = $3
                        AND binding_id = $4
                    """,
                    position,
                    row["user_id"],
                    row["agent_id"],
                    binding.binding_id,
                )
            for position, binding in enumerate(record.data.mcp_bindings):
                await conn.execute(
                    f"""
                    UPDATE {self._agent_mcp_binding_table}
                    SET position = $1
                    WHERE user_id = $2
                        AND agent_id = $3
                        AND binding_id = $4
                    """,
                    position,
                    row["user_id"],
                    row["agent_id"],
                    binding.binding_id,
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

    async def upsert_agent_record(
        self,
        user_id: str,
        record: AgentRecord,
    ) -> None:
        """Mirror an agent record into the dedicated agent table."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"""
                    INSERT INTO {self._agent_table}
                        (
                            user_id,
                            agent_id,
                            agent_data_id,
                            source,
                            agent_name,
                            description,
                            system_prompt,
                            context_config_json,
                            react_config_json,
                            invite_config_json,
                            share_config_json,
                            tool_config_json,
                            shared,
                            created_at,
                            record_json,
                            updated_at
                        )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11, $12, $13, $14, $15, $16
                    )
                    ON CONFLICT (user_id, agent_id) DO UPDATE
                    SET agent_data_id = EXCLUDED.agent_data_id,
                        source = EXCLUDED.source,
                        agent_name = EXCLUDED.agent_name,
                        description = EXCLUDED.description,
                        system_prompt = EXCLUDED.system_prompt,
                        context_config_json = EXCLUDED.context_config_json,
                        react_config_json = EXCLUDED.react_config_json,
                        invite_config_json = EXCLUDED.invite_config_json,
                        share_config_json = EXCLUDED.share_config_json,
                        tool_config_json = EXCLUDED.tool_config_json,
                        shared = EXCLUDED.shared,
                        created_at = EXCLUDED.created_at,
                        record_json = EXCLUDED.record_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    user_id,
                    record.id,
                    record.data.id,
                    record.source,
                    record.data.name,
                    record.data.invite_config.invite_description,
                    record.data.system_prompt,
                    record.data.context_config.model_dump_json(),
                    record.data.react_config.model_dump_json(),
                    record.data.invite_config.model_dump_json(),
                    record.data.share_config.model_dump_json(),
                    record.data.tool_config.model_dump_json(),
                    record.data.share_config.shared,
                    record.created_at,
                    record.model_dump_json(),
                    record.updated_at,
                )
                await self._replace_agent_binding_rows(conn, user_id, record)
                await self._replace_agent_share_grant_rows(
                    conn,
                    user_id,
                    record,
                )

    async def _replace_agent_binding_rows(
        self,
        conn: Any,
        user_id: str,
        record: AgentRecord,
    ) -> None:
        """Replace the dedicated Skill/MCP binding rows for one agent."""
        await conn.execute(
            f"""
            DELETE FROM {self._agent_skill_binding_table}
            WHERE user_id = $1 AND agent_id = $2
            """,
            user_id,
            record.id,
        )
        await conn.execute(
            f"""
            DELETE FROM {self._agent_mcp_binding_table}
            WHERE user_id = $1 AND agent_id = $2
            """,
            user_id,
            record.id,
        )
        if record.data.skill_bindings:
            await conn.executemany(
                f"""
                INSERT INTO {self._agent_skill_binding_table}
                    (
                        user_id,
                        agent_id,
                        binding_id,
                        source,
                        skillhub_skill_id,
                        namespace,
                        slug,
                        version,
                        display_name,
                        description,
                        enabled,
                        position,
                        config_json,
                        snapshot_json,
                        created_at,
                        updated_at
                    )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, $16
                )
                """,
                [
                    (
                        user_id,
                        record.id,
                        binding.binding_id,
                        binding.source,
                        binding.skillhub_skill_id,
                        binding.namespace,
                        binding.slug,
                        binding.version,
                        binding.display_name,
                        binding.description,
                        binding.enabled,
                        position,
                        json.dumps(binding.config_json),
                        json.dumps(binding.snapshot_json),
                        record.created_at,
                        record.updated_at,
                    )
                    for position, binding in enumerate(
                        record.data.skill_bindings,
                    )
                ],
            )
        if record.data.mcp_bindings:
            await conn.executemany(
                f"""
                INSERT INTO {self._agent_mcp_binding_table}
                    (
                        user_id,
                        agent_id,
                        binding_id,
                        source,
                        skillhub_server_id,
                        name,
                        provider,
                        auth_type,
                        transport,
                        endpoint_url,
                        enabled,
                        position,
                        config_json,
                        snapshot_json,
                        created_at,
                        updated_at
                    )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, $16
                )
                """,
                [
                    (
                        user_id,
                        record.id,
                        binding.binding_id,
                        binding.source,
                        binding.skillhub_server_id,
                        binding.name,
                        binding.provider,
                        binding.auth_type,
                        binding.transport,
                        binding.endpoint_url,
                        binding.enabled,
                        position,
                        json.dumps(binding.config_json),
                        json.dumps(binding.snapshot_json),
                        record.created_at,
                        record.updated_at,
                    )
                    for position, binding in enumerate(
                        record.data.mcp_bindings,
                    )
                ],
            )

    async def _replace_agent_share_grant_rows(
        self,
        conn: Any,
        user_id: str,
        record: AgentRecord,
    ) -> None:
        """Replace the dedicated share-grant rows for one Agent."""
        await conn.execute(
            f"""
            DELETE FROM {self._agent_share_grant_table}
            WHERE user_id = $1 AND agent_id = $2
            """,
            user_id,
            record.id,
        )
        if not record.data.share_grants:
            return
        await conn.executemany(
            f"""
            INSERT INTO {self._agent_share_grant_table}
                (
                    user_id,
                    agent_id,
                    grantee_type,
                    grantee_id,
                    tier,
                    created_at,
                    created_by,
                    updated_at
                )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            [
                (
                    user_id,
                    record.id,
                    grant.grantee_type,
                    grant.grantee_id,
                    grant.tier,
                    grant.created_at,
                    grant.created_by,
                    record.updated_at,
                )
                for grant in record.data.share_grants
            ],
        )

    async def list_agent_records(self, user_id: str) -> list[AgentRecord]:
        """Return user-created agents from the dedicated agent table."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    agent_id,
                    agent_data_id,
                    source,
                    agent_name,
                    system_prompt,
                    context_config_json,
                    react_config_json,
                    invite_config_json,
                    share_config_json,
                    tool_config_json,
                    created_at,
                    updated_at
                FROM {self._agent_table}
                WHERE user_id = $1 AND source = 'user'
                ORDER BY updated_at DESC
                """,
                user_id,
            )
        records = [
            self._agent_record_from_list_row(row)
            for row in rows
            if row["source"] == "user"
        ]
        async with self._pool.acquire() as conn:
            await self._attach_agent_binding_rows(conn, records)
        return records

    async def list_shared_agent_records(
        self,
        viewer_id: str,
    ) -> list[AgentRecord]:
        """Return shared user-created agents from dedicated agent rows."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    agent_id,
                    agent_data_id,
                    source,
                    agent_name,
                    system_prompt,
                    context_config_json,
                    react_config_json,
                    invite_config_json,
                    share_config_json,
                    tool_config_json,
                    created_at,
                    updated_at
                FROM {self._agent_table}
                WHERE user_id <> $1
                    AND source = 'user'
                    AND (
                        shared = true
                        OR EXISTS (
                            SELECT 1
                            FROM {self._agent_share_grant_table} grants
                            WHERE grants.user_id = {self._agent_table}.user_id
                                AND grants.agent_id = {self._agent_table}.agent_id
                                AND (
                                    (
                                        grants.grantee_type = 'user'
                                        AND grants.grantee_id = $1
                                    )
                                    OR (
                                        grants.grantee_type = 'workspace'
                                        AND grants.grantee_id = '*'
                                    )
                                )
                        )
                    )
                ORDER BY updated_at DESC
                """,
                viewer_id,
            )
            grant_rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    agent_id,
                    grantee_type,
                    grantee_id,
                    tier,
                    created_at,
                    created_by
                FROM {self._agent_share_grant_table}
                WHERE user_id <> $1
                    AND (
                        (
                            grantee_type = 'user'
                            AND grantee_id = $1
                        )
                        OR (
                            grantee_type = 'workspace'
                            AND grantee_id = '*'
                        )
                    )
                """,
                viewer_id,
            )
        grants_by_agent: dict[tuple[str, str], list[AgentShareGrant]] = {}
        for grant_row in grant_rows:
            key = (grant_row["user_id"], grant_row["agent_id"])
            grants_by_agent.setdefault(key, []).append(
                AgentShareGrant(
                    grantee_type=grant_row["grantee_type"],
                    grantee_id=grant_row["grantee_id"],
                    tier=grant_row["tier"],
                    created_at=grant_row["created_at"],
                    created_by=grant_row["created_by"],
                ),
            )
        records: list[AgentRecord] = []
        for row in rows:
            record = self._agent_record_from_list_row(row)
            record.data.share_grants = grants_by_agent.get(
                (record.user_id, record.id),
                [],
            )
            if (
                record.source == "user"
                and record.user_id != viewer_id
                and effective_agent_share_tier(record.data, viewer_id)
                is not None
            ):
                records.append(record)
        async with self._pool.acquire() as conn:
            await self._attach_agent_binding_rows(conn, records)
        return records

    async def _attach_agent_binding_rows(
        self,
        conn: Any,
        records: list[AgentRecord],
    ) -> None:
        """Attach dedicated Skill/MCP binding rows to list records."""
        if not records:
            return
        owner_ids = [record.user_id for record in records]
        agent_ids = [record.id for record in records]
        skill_rows = await conn.fetch(
            f"""
            SELECT
                user_id,
                agent_id,
                binding_id,
                source,
                skillhub_skill_id,
                namespace,
                slug,
                version,
                display_name,
                description,
                enabled,
                position,
                config_json,
                snapshot_json
            FROM {self._agent_skill_binding_table}
            WHERE (user_id, agent_id) IN (
                SELECT *
                FROM unnest($1::text[], $2::text[])
            )
            ORDER BY position ASC, binding_id ASC
            """,
            owner_ids,
            agent_ids,
        )
        mcp_rows = await conn.fetch(
            f"""
            SELECT
                user_id,
                agent_id,
                binding_id,
                source,
                skillhub_server_id,
                name,
                provider,
                auth_type,
                transport,
                endpoint_url,
                enabled,
                position,
                config_json,
                snapshot_json
            FROM {self._agent_mcp_binding_table}
            WHERE (user_id, agent_id) IN (
                SELECT *
                FROM unnest($1::text[], $2::text[])
            )
            ORDER BY position ASC, binding_id ASC
            """,
            owner_ids,
            agent_ids,
        )

        skills_by_agent: dict[tuple[str, str], list[AgentSkillBinding]] = {}
        for row in skill_rows:
            key = (row["user_id"], row["agent_id"])
            skills_by_agent.setdefault(key, []).append(
                AgentSkillBinding(
                    binding_id=row["binding_id"],
                    source=row["source"],
                    skillhub_skill_id=row["skillhub_skill_id"],
                    namespace=row["namespace"],
                    slug=row["slug"],
                    version=row["version"],
                    display_name=row["display_name"],
                    description=row["description"],
                    enabled=row["enabled"],
                    config_json=_json_dict_or_empty(row["config_json"]),
                    snapshot_json=_json_dict_or_empty(row["snapshot_json"]),
                ),
            )

        mcps_by_agent: dict[tuple[str, str], list[AgentMcpBinding]] = {}
        for row in mcp_rows:
            key = (row["user_id"], row["agent_id"])
            mcps_by_agent.setdefault(key, []).append(
                AgentMcpBinding(
                    binding_id=row["binding_id"],
                    source=row["source"],
                    skillhub_server_id=row["skillhub_server_id"],
                    name=row["name"],
                    provider=row["provider"],
                    auth_type=row["auth_type"],
                    transport=row["transport"],
                    endpoint_url=row["endpoint_url"],
                    enabled=row["enabled"],
                    config_json=_json_dict_or_empty(row["config_json"]),
                    snapshot_json=_json_dict_or_empty(row["snapshot_json"]),
                ),
            )

        for record in records:
            key = (record.user_id, record.id)
            record.data.skill_bindings = skills_by_agent.get(key, [])
            record.data.mcp_bindings = mcps_by_agent.get(key, [])

    @staticmethod
    def _agent_record_from_list_row(row: Any) -> AgentRecord:
        """Rebuild an agent list record from dedicated columns."""
        return AgentRecord(
            id=row["agent_id"],
            user_id=row["user_id"],
            source=row["source"],
            data=AgentData(
                id=row["agent_data_id"] or row["agent_id"],
                name=row["agent_name"],
                system_prompt=row["system_prompt"],
                context_config=_json_or_default(
                    ContextConfig,
                    row["context_config_json"],
                ),
                react_config=_json_or_default(
                    ReActConfig,
                    row["react_config_json"],
                ),
                invite_config=_json_or_default(
                    InviteConfig,
                    row["invite_config_json"],
                ),
                share_config=_json_or_default(
                    ShareConfig,
                    row["share_config_json"],
                ),
                tool_config=_json_or_default(
                    AgentToolConfig,
                    _row_get(row, "tool_config_json"),
                ),
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_agent_record(self, user_id: str, agent_id: str) -> int:
        """Delete one dedicated agent row and its binding rows."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"""
                    DELETE FROM {self._agent_skill_binding_table}
                    WHERE user_id = $1 AND agent_id = $2
                    """,
                    user_id,
                    agent_id,
                )
                await conn.execute(
                    f"""
                    DELETE FROM {self._agent_mcp_binding_table}
                    WHERE user_id = $1 AND agent_id = $2
                    """,
                    user_id,
                    agent_id,
                )
                await conn.execute(
                    f"""
                    DELETE FROM {self._agent_share_grant_table}
                    WHERE user_id = $1 AND agent_id = $2
                    """,
                    user_id,
                    agent_id,
                )
                status = await conn.execute(
                    f"""
                    DELETE FROM {self._agent_table}
                    WHERE user_id = $1 AND agent_id = $2
                    """,
                    user_id,
                    agent_id,
                )
        return _parse_status_count(status)

    async def upsert_session_record(
        self,
        user_id: str,
        record: SessionRecord,
    ) -> None:
        """Mirror a session record into the dedicated session table."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._session_table}
                    (
                        user_id,
                        agent_id,
                        session_id,
                        source,
                        source_schedule_id,
                        workspace_id,
                        workspace_owner_id,
                        session_name,
                        chat_model_config_json,
                        fallback_chat_model_config_json,
                        tts_model_config_json,
                        knowledge_config_json,
                        model_type,
                        model_name,
                        team_id,
                        status,
                        state_json,
                        state_summary_json,
                        created_at,
                        record_json,
                        updated_at
                    )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14, $15,
                    $16, $17, $18, $19, $20, $21
                )
                ON CONFLICT (user_id, session_id) DO UPDATE
                SET agent_id = EXCLUDED.agent_id,
                    source = EXCLUDED.source,
                    source_schedule_id = EXCLUDED.source_schedule_id,
                    workspace_id = EXCLUDED.workspace_id,
                    workspace_owner_id = EXCLUDED.workspace_owner_id,
                    session_name = EXCLUDED.session_name,
                    chat_model_config_json = EXCLUDED.chat_model_config_json,
                    fallback_chat_model_config_json =
                        EXCLUDED.fallback_chat_model_config_json,
                    tts_model_config_json = EXCLUDED.tts_model_config_json,
                    knowledge_config_json = EXCLUDED.knowledge_config_json,
                    model_type = EXCLUDED.model_type,
                    model_name = EXCLUDED.model_name,
                    team_id = EXCLUDED.team_id,
                    status = EXCLUDED.status,
                    state_json = EXCLUDED.state_json,
                    state_summary_json = EXCLUDED.state_summary_json,
                    created_at = EXCLUDED.created_at,
                    record_json = EXCLUDED.record_json,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                record.agent_id,
                record.id,
                record.source.value,
                record.source_schedule_id,
                record.config.workspace_id,
                record.config.workspace_owner_id,
                record.config.name,
                (
                    record.config.chat_model_config.model_dump_json()
                    if record.config.chat_model_config is not None
                    else None
                ),
                (
                    record.config.fallback_chat_model_config.model_dump_json()
                    if record.config.fallback_chat_model_config is not None
                    else None
                ),
                (
                    record.config.tts_model_config.model_dump_json()
                    if record.config.tts_model_config is not None
                    else None
                ),
                (
                    record.config.knowledge_config.model_dump_json()
                    if record.config.knowledge_config is not None
                    else None
                ),
                (
                    record.config.chat_model_config.type
                    if record.config.chat_model_config is not None
                    else None
                ),
                (
                    record.config.chat_model_config.model
                    if record.config.chat_model_config is not None
                    else None
                ),
                record.team_id,
                _derive_persisted_session_status(record.state),
                record.state.model_dump_json(),
                _list_state_json(record.state),
                record.created_at,
                record.model_dump_json(),
                record.updated_at,
            )

    async def list_session_records(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return sessions for an agent from the dedicated session table."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    agent_id,
                    session_id,
                    source,
                    source_schedule_id,
                    workspace_id,
                    workspace_owner_id,
                    session_name,
                    chat_model_config_json,
                    fallback_chat_model_config_json,
                    tts_model_config_json,
                    knowledge_config_json,
                    team_id,
                    state_summary_json,
                    created_at,
                    updated_at
                FROM {self._session_table}
                WHERE user_id = $1 AND agent_id = $2
                ORDER BY created_at DESC
                """,
                user_id,
                agent_id,
            )
        return [self._session_record_from_list_row(row) for row in rows]

    async def list_session_records_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return schedule-created sessions from the dedicated table."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    user_id,
                    agent_id,
                    session_id,
                    source,
                    source_schedule_id,
                    workspace_id,
                    workspace_owner_id,
                    session_name,
                    chat_model_config_json,
                    fallback_chat_model_config_json,
                    tts_model_config_json,
                    knowledge_config_json,
                    team_id,
                    state_summary_json,
                    created_at,
                    updated_at
                FROM {self._session_table}
                WHERE user_id = $1 AND source_schedule_id = $2
                ORDER BY created_at DESC
                """,
                user_id,
                schedule_id,
            )
        return [self._session_record_from_list_row(row) for row in rows]

    @staticmethod
    def _session_record_from_list_row(row: Any) -> SessionRecord:
        """Build a session list record from dedicated list columns."""
        return SessionRecord(
            id=row["session_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            source=SessionSource(row["source"] or SessionSource.USER.value),
            source_schedule_id=row["source_schedule_id"],
            team_id=row["team_id"],
            config=SessionConfig(
                workspace_id=row["workspace_id"],
                workspace_owner_id=_row_get(row, "workspace_owner_id"),
                name=row["session_name"],
                chat_model_config=_json_or_none(
                    ChatModelConfig,
                    row["chat_model_config_json"],
                ),
                fallback_chat_model_config=_json_or_none(
                    ChatModelConfig,
                    row["fallback_chat_model_config_json"],
                ),
                tts_model_config=_json_or_none(
                    TTSModelConfig,
                    row["tts_model_config_json"],
                ),
                knowledge_config=_json_or_none(
                    SessionKnowledgeConfig,
                    row["knowledge_config_json"],
                ),
            ),
            state=AgentState.model_validate_json(row["state_summary_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_session_record(
        self,
        user_id: str,
        session_id: str,
    ) -> int:
        """Delete one dedicated session row."""
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                f"""
                DELETE FROM {self._session_table}
                WHERE user_id = $1 AND session_id = $2
                """,
                user_id,
                session_id,
            )
        return _parse_status_count(status)

    async def delete_agent_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> int:
        """Delete all dedicated session rows for an agent."""
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                f"""
                DELETE FROM {self._session_table}
                WHERE user_id = $1 AND agent_id = $2
                """,
                user_id,
                agent_id,
            )
        return _parse_status_count(status)

    async def backfill_dedicated_records(
        self,
        *,
        agent_key_like: str,
        session_key_like: str,
    ) -> None:
        """Seed dedicated tables from legacy KV rows during startup."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT value
                FROM {self._kv_table}
                WHERE key LIKE $1 ESCAPE '!'
                   OR key LIKE $2 ESCAPE '!'
                """,
                agent_key_like,
                session_key_like,
            )
        for row in rows:
            value = row["value"]
            try:
                agent = AgentRecord.model_validate_json(value)
            except Exception:  # pylint: disable=broad-except
                agent = None
            if agent is not None:
                await self.upsert_agent_record(agent.user_id, agent)
                continue
            try:
                session = SessionRecord.model_validate_json(value)
            except Exception:  # pylint: disable=broad-except
                continue
            await self.upsert_session_record(session.user_id, session)

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
        await self._client.backfill_dedicated_records(
            agent_key_like=_key_template_to_sql_like(self.key_config.agent),
            session_key_like=_key_template_to_sql_like(
                self.key_config.session,
            ),
        )
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

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an agent in legacy KV and the dedicated agent table."""
        agent_id = await super().upsert_agent(user_id, agent_record)
        await self.get_client().upsert_agent_record(user_id, agent_record)
        return agent_id

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return user-created agents from the dedicated PostgreSQL table."""
        return await self.get_client().list_agent_records(user_id)

    async def list_shared_agents(self, viewer_id: str) -> list[AgentRecord]:
        """Return shared agents from the dedicated PostgreSQL table."""
        return await self.get_client().list_shared_agent_records(viewer_id)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent from legacy KV and dedicated agent/session rows."""
        deleted = await super().delete_agent(user_id, agent_id)
        await self.get_client().delete_agent_record(user_id, agent_id)
        await self.get_client().delete_agent_sessions(user_id, agent_id)
        return deleted

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: AgentState | None = None,
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        source_schedule_id: str | None = None,
    ) -> SessionRecord:
        """Persist a session in legacy KV and the dedicated session table."""
        record = await super().upsert_session(
            user_id,
            agent_id,
            config,
            state=state,
            session_id=session_id,
            source=source,
            source_schedule_id=source_schedule_id,
        )
        await self.get_client().upsert_session_record(user_id, record)
        return record

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Update session state in legacy KV and refresh the session row."""
        await super().update_session_state(user_id, agent_id, session_id, state)
        record = await self.get_session(user_id, agent_id, session_id)
        if record is not None:
            await self.get_client().upsert_session_record(user_id, record)

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return sessions for an agent from the dedicated table."""
        return await self.get_client().list_session_records(user_id, agent_id)

    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return schedule sessions from the dedicated table."""
        return await self.get_client().list_session_records_by_schedule(
            user_id,
            schedule_id,
        )

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session from legacy KV and the dedicated session table."""
        deleted = await super().delete_session(user_id, agent_id, session_id)
        if deleted:
            await self.get_client().delete_session_record(user_id, session_id)
        return deleted

    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Update team membership in legacy KV and refresh the session row."""
        await super().set_session_team_id(user_id, session_id, team_id)
        record = await self.get_session(user_id, "", session_id)
        if record is not None:
            await self.get_client().upsert_session_record(user_id, record)
