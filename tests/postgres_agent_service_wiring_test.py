# -*- coding: utf-8 -*-
"""Tests for PostgreSQL-backed Agent Service example wiring."""

from collections import defaultdict
import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest

if "tree_sitter_bash" not in sys.modules:
    tsbash = types.ModuleType("tree_sitter_bash")
    tsbash.language = lambda: object()
    sys.modules["tree_sitter_bash"] = tsbash

if "tree_sitter" not in sys.modules:
    tree_sitter = types.ModuleType("tree_sitter")

    class _FakeLanguage:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeParser:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeNode:
        type = ""
        children: list = []
        start_byte = 0
        end_byte = 0

    tree_sitter.Language = _FakeLanguage
    tree_sitter.Parser = _FakeParser
    tree_sitter.Node = _FakeNode
    sys.modules["tree_sitter"] = tree_sitter

if "frontmatter" not in sys.modules:
    frontmatter = types.ModuleType("frontmatter")

    class _FakeFrontmatterPost(dict):
        content = ""

    frontmatter.loads = lambda _text: _FakeFrontmatterPost()
    sys.modules["frontmatter"] = frontmatter

if "shortuuid" not in sys.modules:
    shortuuid = types.ModuleType("shortuuid")
    shortuuid.uuid = lambda: "test-shortuuid"
    sys.modules["shortuuid"] = shortuuid

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app.storage import (
    AgentData,
    AgentMcpBinding,
    AgentRecord,
    AgentShareGrant,
    AgentSkillBinding,
    AgentToolConfig,
    ChatModelConfig,
    InviteConfig,
    SessionConfig,
    SessionRecord,
    SessionSource,
    ShareConfig,
)
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState


def test_postgres_backends_are_exported() -> None:
    """PostgreSQL storage and vector store should be public APIs."""
    from agentscope.app.storage import PostgresStorage, StorageBase
    from agentscope.rag import PostgresVectorStore, VectorStoreBase

    assert issubclass(PostgresStorage, StorageBase)
    assert issubclass(PostgresVectorStore, VectorStoreBase)


def test_agent_service_example_uses_persistent_backends() -> None:
    """The official Agent Service example should use persistent backends."""
    source = Path("examples/agent_service/main.py").read_text(
        encoding="utf-8",
    )

    assert "PostgresStorage" in source
    assert "PostgresVectorStore" in source
    assert "RedisMessageBus" in source
    assert "InMemoryMessageBus" not in source
    assert 'QdrantStore(location=":memory:")' not in source


def test_agent_service_example_wires_long_term_memory() -> None:
    """The service example should expose long-term memory middleware."""
    source = Path("examples/agent_service/main.py").read_text(
        encoding="utf-8",
    )

    assert "Mem0Middleware" in source
    assert "ReMeMiddleware" in source
    assert "AgenticMemoryMiddleware" in source
    assert "extra_agent_middlewares=" in source


def test_agent_service_example_uses_explicit_redis_timeouts() -> None:
    """The service example should avoid fragile default Redis networking."""
    source = Path("examples/agent_service/main.py").read_text(
        encoding="utf-8",
    )

    assert 'os.getenv("REDIS_HOST", "127.0.0.1")' in source
    assert "REDIS_SOCKET_CONNECT_TIMEOUT" in source
    assert "REDIS_SOCKET_TIMEOUT" in source
    assert "REDIS_HEALTH_CHECK_INTERVAL" in source


def test_agent_service_example_loads_env_file_and_documents_config() -> None:
    """The example should load and document its local environment file."""
    source = Path("examples/agent_service/main.py").read_text(
        encoding="utf-8",
    )
    env_source = Path("examples/agent_service/.env").read_text(
        encoding="utf-8",
    )

    assert "_load_dotenv(BASE_DIR / \".env\")" in source
    for name in [
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "REDIS_HOST",
        "REDIS_PORT",
        "AGENT_SERVICE_MEMORY_SCOPE",
        "AGENT_SERVICE_ENABLE_REME",
        "REME_MEMORY_MODE",
        "MEMORY_CHAT_MODEL",
        "MEMORY_EMBEDDING_MODEL",
        "DASHSCOPE_API_KEY",
    ]:
        assert f"{name}=" in env_source


def test_agent_service_example_injects_memory_models_into_reme() -> None:
    """ReMe should use the service-level memory models when configured."""
    source = Path("examples/agent_service/main.py").read_text(
        encoding="utf-8",
    )

    assert "ReMeMiddleware.Parameters(" in source
    assert "chat_model=memory_chat_model" in source
    assert "embedding_model=memory_embedding_model" in source


def _load_agent_service_main():
    module_dir = Path("examples/agent_service").resolve()
    spec = importlib.util.spec_from_file_location(
        "agent_service_main_test",
        module_dir / "main.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(module_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(module_dir))
    return module


def test_agent_service_memory_root_defaults_to_user_agent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File memory should be shared by the same user and agent across sessions."""
    monkeypatch.delenv("AGENT_SERVICE_MEMORY_SCOPE", raising=False)
    module = _load_agent_service_main()

    first = module._long_term_memory_root("user-1", "agent-1", "session-a")
    second = module._long_term_memory_root("user-1", "agent-1", "session-b")

    assert first == second
    assert first.parts[-3:] == ("user-1", "agent-1", "long_term_memory")
    assert "session-a" not in first.parts
    assert "session-b" not in second.parts


def test_agent_service_memory_root_can_be_session_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can still opt into isolated per-session file memory."""
    monkeypatch.setenv("AGENT_SERVICE_MEMORY_SCOPE", "session")
    module = _load_agent_service_main()

    root = module._long_term_memory_root("user-1", "agent-1", "session-a")

    assert root.parts[-4:] == (
        "user-1",
        "agent-1",
        "session-a",
        "long_term_memory",
    )


class _FakePgConnection:
    """Connection stub for Postgres KV adapter list operations."""

    def __init__(self, lists: dict[str, list[dict]]) -> None:
        self.lists = lists

    async def fetchval(self, query: str, *args):
        """Return count or list value depending on the SQL shape."""
        key = args[0]
        if "count(*)" in query:
            if "expires_at IS NULL" in query:
                return len([row for row in self.lists[key] if row["live"]])
            return len(self.lists[key])
        if "max(idx)" in query:
            rows = [row for row in self.lists[key] if row["live"]]
            return max((row["idx"] for row in rows), default=-1) + 1
        if "SELECT value" in query:
            index = args[1]
            for row in self.lists[key]:
                if row["idx"] == index and row["live"]:
                    return row["value"]
            return None
        return None

    async def execute(self, _query: str, *args):
        """Update one list entry."""
        if _query.lstrip().startswith("DELETE"):
            key = args[0]
            self.lists[key] = [row for row in self.lists[key] if row["live"]]
            return "DELETE 1"
        key, index, value = args
        for row in self.lists[key]:
            if row["idx"] == index:
                row["value"] = value
                break
        return "UPDATE 1"

    async def executemany(self, _query: str, rows) -> None:
        """Append list rows."""
        for key, index, value in rows:
            self.lists[key].append(
                {"idx": index, "value": value, "live": True},
            )


class _FakeAcquire:
    """Async context manager returned by fake pool.acquire()."""

    def __init__(self, conn: _FakePgConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakePgConnection:
        return self.conn

    async def __aexit__(self, *_args) -> None:
        return None


class _FakeTransaction:
    """Async context manager returned by fake connection.transaction()."""

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _FakePgPool:
    """Pool stub exposing acquire()."""

    def __init__(self) -> None:
        self.lists = defaultdict(list)
        self.conn = _FakePgConnection(self.lists)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


def _agent_record(
    user_id: str = "user-1",
    agent_id: str = "agent-1",
    *,
    source: str = "user",
    description: str | None = None,
    shared: bool = False,
    share_grants: list[AgentShareGrant] | None = None,
    tool_config: AgentToolConfig | None = None,
    with_bindings: bool = False,
) -> AgentRecord:
    """Build a minimal AgentRecord for Postgres dedicated table tests."""
    skill_bindings = []
    mcp_bindings = []
    if with_bindings:
        skill_bindings = [
            AgentSkillBinding(
                binding_id="skill-binding-1",
                skillhub_skill_id="skill-1",
                namespace="global",
                slug="summarizer",
                version="v1",
                display_name="Summarizer",
                description="Summarizes documents.",
                config_json={"mode": "short"},
                snapshot_json={"slug": "summarizer"},
            ),
        ]
        mcp_bindings = [
            AgentMcpBinding(
                binding_id="mcp-binding-1",
                skillhub_server_id="server-1",
                name="search-mcp",
                provider="skillhub",
                auth_type="none",
                transport="sse",
                endpoint_url="https://example.test/mcp",
                config_json={"timeout": 10},
                snapshot_json={"name": "search-mcp"},
            ),
        ]
    return AgentRecord(
        id=agent_id,
        user_id=user_id,
        source=source,
        data=AgentData(
            id=f"{agent_id}-data",
            name=agent_id,
            system_prompt="You are helpful.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            invite_config=InviteConfig(invite_description=description),
            share_config=ShareConfig(shared=shared),
            share_grants=share_grants or [],
            tool_config=tool_config or AgentToolConfig(),
            skill_bindings=skill_bindings,
            mcp_bindings=mcp_bindings,
        ),
    )


def _agent_record_with_multiple_bindings(
    *,
    user_id: str = "user-1",
    agent_id: str = "agent-ordered",
    shared: bool = False,
) -> AgentRecord:
    """Build an AgentRecord whose binding order must be preserved."""
    agent = _agent_record(user_id=user_id, agent_id=agent_id, shared=shared)
    agent.data.skill_bindings = [
        AgentSkillBinding(
            binding_id="skill-binding-b",
            skillhub_skill_id="skill-b",
            namespace="global",
            slug="second-skill",
        ),
        AgentSkillBinding(
            binding_id="skill-binding-a",
            skillhub_skill_id="skill-a",
            namespace="global",
            slug="first-skill",
        ),
    ]
    agent.data.mcp_bindings = [
        AgentMcpBinding(
            binding_id="mcp-binding-b",
            skillhub_server_id="server-b",
            name="second-mcp",
        ),
        AgentMcpBinding(
            binding_id="mcp-binding-a",
            skillhub_server_id="server-a",
            name="first-mcp",
        ),
    ]
    return agent


def _session_config(workspace_id: str = "ws-1") -> SessionConfig:
    """Build a minimal SessionConfig for Postgres dedicated table tests."""
    return SessionConfig(
        workspace_id=workspace_id,
        workspace_owner_id="workspace-owner",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


def _awaiting_permission_state() -> AgentState:
    """Build a persisted state parked on an asking tool call."""
    return AgentState(
        context=[
            Msg(
                name="assistant",
                role="assistant",
                content=[
                    ToolCallBlock(
                        id="tool-call-1",
                        name="NeedsApproval",
                        input="{}",
                        state=ToolCallState.ASKING,
                    ),
                ],
            ),
        ],
    )


class _DedicatedPgConnection(_FakePgConnection):
    """Connection stub for dedicated Postgres agent/session tables."""

    def __init__(self) -> None:
        super().__init__(defaultdict(list))
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.agents: dict[tuple[str, str], dict] = {}
        self.agent_skill_bindings: dict[tuple[str, str, str], dict] = {}
        self.agent_mcp_bindings: dict[tuple[str, str, str], dict] = {}
        self.agent_share_grants: dict[tuple[str, str, str, str], dict] = {}
        self.sessions: dict[tuple[str, str], dict] = {}
        self.reject_record_json_list_reads = False
        self.reject_state_json_list_reads = False

    def transaction(self) -> _FakeTransaction:
        """Return a no-op transaction for adapter delete paths."""
        return _FakeTransaction()

    async def fetchval(self, query: str, *args):
        """Return scalar values for KV/list/set compatibility queries."""
        if "FROM agentscope_sets" in query and "count(*)" in query:
            return len(self.sets[args[0]])
        if "SELECT value" in query and "FROM agentscope_kv" in query:
            return self.kv.get(args[0])
        return await super().fetchval(query, *args)

    async def fetch(self, query: str, *args):
        """Return rows for dedicated table list queries."""
        if "FROM agentscope_kv" in query:
            return [{"value": value} for value in self.kv.values()]
        if "FROM agentscope_sets" in query:
            return [{"member": member} for member in self.sets[args[0]]]
        if "FROM agentscope_agent_skill_bindings" in query:
            owner_ids, agent_ids = args[:2]
            pairs = set(zip(owner_ids, agent_ids))
            rows = [
                row
                for key, row in self.agent_skill_bindings.items()
                if key[:2] in pairs
            ]
            if "ORDER BY position" in query:
                rows.sort(
                    key=lambda row: (row.get("position", 0), row["binding_id"]),
                )
            else:
                rows.sort(key=lambda row: row["binding_id"])
            return rows
        if "FROM agentscope_agent_mcp_bindings" in query:
            owner_ids, agent_ids = args[:2]
            pairs = set(zip(owner_ids, agent_ids))
            rows = [
                row
                for key, row in self.agent_mcp_bindings.items()
                if key[:2] in pairs
            ]
            if "ORDER BY position" in query:
                rows.sort(
                    key=lambda row: (row.get("position", 0), row["binding_id"]),
                )
            else:
                rows.sort(key=lambda row: row["binding_id"])
            return rows
        if (
            "FROM agentscope_agent_share_grants" in query
            and "FROM agentscope_agents" not in query
        ):
            viewer_id = args[0]
            return [
                row
                for row in self.agent_share_grants.values()
                if row["user_id"] != viewer_id
                and (
                    (
                        row["grantee_type"] == "user"
                        and row["grantee_id"] == viewer_id
                    )
                    or (
                        row["grantee_type"] == "workspace"
                        and row["grantee_id"] == "*"
                    )
                )
            ]
        if "FROM agentscope_agents" in query:
            if self.reject_record_json_list_reads and "record_json" in query:
                raise AssertionError("agent list query read record_json")
            if "record_json" in query:
                return [
                    {
                        "user_id": row["user_id"],
                        "agent_id": row["agent_id"],
                        "record_json": row["record_json"],
                    }
                    for row in self.agents.values()
                ]
            user_id = args[0]
            if "user_id <> $1" in query:
                rows = [
                    row
                    for (row_user_id, _), row in self.agents.items()
                    if row_user_id != user_id
                    and row["source"] == "user"
                    and (
                        row["shared"] is True
                        or any(
                            grant["user_id"] == row_user_id
                            and grant["agent_id"] == row["agent_id"]
                            and (
                                (
                                    grant["grantee_type"] == "user"
                                    and grant["grantee_id"] == user_id
                                )
                                or (
                                    grant["grantee_type"] == "workspace"
                                    and grant["grantee_id"] == "*"
                                )
                            )
                            for grant in self.agent_share_grants.values()
                        )
                    )
                ]
            else:
                rows = [
                    row
                    for (row_user_id, _), row in self.agents.items()
                    if row_user_id == user_id
                    and (
                        "source = 'user'" not in query
                        or row["source"] == "user"
                    )
                ]
            rows.sort(key=lambda row: row["updated_at"], reverse=True)
            return rows
        if "FROM agentscope_sessions" in query:
            if self.reject_record_json_list_reads and "record_json" in query:
                raise AssertionError("session list query read record_json")
            if self.reject_state_json_list_reads and "state_json" in query:
                raise AssertionError("session list query read state_json")
            user_id = args[0]
            if "source_schedule_id = $2" in query:
                schedule_id = args[1]
                rows = [
                    row
                    for (row_user_id, _), row in self.sessions.items()
                    if (
                        row_user_id == user_id
                        and row["source_schedule_id"] == schedule_id
                    )
                ]
            else:
                agent_id = args[1]
                rows = [
                    row
                    for (row_user_id, _), row in self.sessions.items()
                    if row_user_id == user_id and row["agent_id"] == agent_id
                ]
            rows.sort(key=lambda row: row["created_at"], reverse=True)
            return rows
        return []

    async def execute(self, query: str, *args):
        """Handle dedicated table writes and fall back to KV list behavior."""
        if (
            "CREATE TABLE" in query
            or "CREATE INDEX" in query
            or "ALTER TABLE" in query
        ):
            return "CREATE"
        if "INSERT INTO agentscope_kv" in query:
            key, value = args[:2]
            self.kv[key] = value
            return "INSERT 0 1"
        if "DELETE FROM agentscope_kv" in query:
            key = args[0]
            existed = self.kv.pop(key, None) is not None
            return f"DELETE {1 if existed else 0}"
        if "DELETE FROM agentscope_sets" in query:
            key = args[0]
            if len(args) > 1:
                members = args[1]
                before = len(self.sets[key])
                self.sets[key].difference_update(members)
                return f"DELETE {before - len(self.sets[key])}"
            count = len(self.sets[key])
            self.sets.pop(key, None)
            return f"DELETE {count}"
        if "DELETE FROM agentscope_lists" in query:
            key = args[0]
            count = len(self.lists[key])
            self.lists.pop(key, None)
            return f"DELETE {count}"
        if "INSERT INTO agentscope_agents" in query:
            user_id, agent_id, agent_data_id, source = args[:4]
            self.agents[(user_id, agent_id)] = {
                "user_id": user_id,
                "agent_id": agent_id,
                "agent_data_id": agent_data_id,
                "source": source,
                "agent_name": args[4],
                "description": args[5],
                "system_prompt": args[6],
                "context_config_json": args[7],
                "react_config_json": args[8],
                "invite_config_json": args[9],
                "share_config_json": args[10],
                "tool_config_json": args[11],
                "shared": args[12],
                "created_at": args[13],
                "record_json": args[14],
                "updated_at": args[15],
            }
            return "INSERT 0 1"
        if "DELETE FROM agentscope_agents" in query:
            user_id, agent_id = args[:2]
            existed = self.agents.pop((user_id, agent_id), None) is not None
            return f"DELETE {1 if existed else 0}"
        if "DELETE FROM agentscope_agent_skill_bindings" in query:
            user_id, agent_id = args[:2]
            keys = [
                key
                for key, row in self.agent_skill_bindings.items()
                if row["user_id"] == user_id and row["agent_id"] == agent_id
            ]
            for key in keys:
                self.agent_skill_bindings.pop(key, None)
            return f"DELETE {len(keys)}"
        if "DELETE FROM agentscope_agent_mcp_bindings" in query:
            user_id, agent_id = args[:2]
            keys = [
                key
                for key, row in self.agent_mcp_bindings.items()
                if row["user_id"] == user_id and row["agent_id"] == agent_id
            ]
            for key in keys:
                self.agent_mcp_bindings.pop(key, None)
            return f"DELETE {len(keys)}"
        if "DELETE FROM agentscope_agent_share_grants" in query:
            user_id, agent_id = args[:2]
            keys = [
                key
                for key, row in self.agent_share_grants.items()
                if row["user_id"] == user_id and row["agent_id"] == agent_id
            ]
            for key in keys:
                self.agent_share_grants.pop(key, None)
            return f"DELETE {len(keys)}"
        if "UPDATE agentscope_agent_skill_bindings" in query:
            position, user_id, agent_id, binding_id = args[:4]
            row = self.agent_skill_bindings.get(
                (user_id, agent_id, binding_id),
            )
            if row is None:
                return "UPDATE 0"
            row["position"] = position
            return "UPDATE 1"
        if "UPDATE agentscope_agent_mcp_bindings" in query:
            position, user_id, agent_id, binding_id = args[:4]
            row = self.agent_mcp_bindings.get((user_id, agent_id, binding_id))
            if row is None:
                return "UPDATE 0"
            row["position"] = position
            return "UPDATE 1"
        if "INSERT INTO agentscope_sessions" in query:
            (
                user_id,
                agent_id,
                session_id,
                source,
                source_schedule_id,
            ) = args[:5]
            self.sessions[(user_id, session_id)] = {
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "source": source,
                "source_schedule_id": source_schedule_id,
                "workspace_id": args[5],
                "workspace_owner_id": args[6],
                "session_name": args[7],
                "chat_model_config_json": args[8],
                "fallback_chat_model_config_json": args[9],
                "tts_model_config_json": args[10],
                "knowledge_config_json": args[11],
                "model_type": args[12],
                "model_name": args[13],
                "team_id": args[14],
                "status": args[15],
                "state_json": args[16],
                "state_summary_json": args[17],
                "created_at": args[18],
                "record_json": args[19],
                "updated_at": args[20],
            }
            return "INSERT 0 1"
        if "DELETE FROM agentscope_sessions" in query:
            user_id = args[0]
            if "session_id = $2" in query:
                session_id = args[1]
                existed = (
                    self.sessions.pop((user_id, session_id), None) is not None
                )
                return f"DELETE {1 if existed else 0}"
            if "agent_id = $2" in query:
                agent_id = args[1]
                keys = [
                    key
                    for key, row in self.sessions.items()
                    if row["user_id"] == user_id and row["agent_id"] == agent_id
                ]
                for key in keys:
                    self.sessions.pop(key, None)
                return f"DELETE {len(keys)}"
        return await super().execute(query, *args)

    async def executemany(self, query: str, rows) -> None:
        """Handle set inserts, binding inserts, and KV list behavior."""
        if "INSERT INTO agentscope_sets" in query:
            for key, member in rows:
                self.sets[key].add(member)
            return
        if "INSERT INTO agentscope_agent_skill_bindings" in query:
            for row in rows:
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
                    updated_at,
                ) = row
                self.agent_skill_bindings[(user_id, agent_id, binding_id)] = {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "binding_id": binding_id,
                    "source": source,
                    "skillhub_skill_id": skillhub_skill_id,
                    "namespace": namespace,
                    "slug": slug,
                    "version": version,
                    "display_name": display_name,
                    "description": description,
                    "enabled": enabled,
                    "position": position,
                    "config_json": config_json,
                    "snapshot_json": snapshot_json,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            return
        if "INSERT INTO agentscope_agent_mcp_bindings" in query:
            for row in rows:
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
                    updated_at,
                ) = row
                self.agent_mcp_bindings[(user_id, agent_id, binding_id)] = {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "binding_id": binding_id,
                    "source": source,
                    "skillhub_server_id": skillhub_server_id,
                    "name": name,
                    "provider": provider,
                    "auth_type": auth_type,
                    "transport": transport,
                    "endpoint_url": endpoint_url,
                    "enabled": enabled,
                    "position": position,
                    "config_json": config_json,
                    "snapshot_json": snapshot_json,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            return
        if "INSERT INTO agentscope_agent_share_grants" in query:
            for row in rows:
                (
                    user_id,
                    agent_id,
                    grantee_type,
                    grantee_id,
                    tier,
                    created_at,
                    created_by,
                    updated_at,
                ) = row
                self.agent_share_grants[
                    (user_id, agent_id, grantee_type, grantee_id)
                ] = {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "grantee_type": grantee_type,
                    "grantee_id": grantee_id,
                    "tier": tier,
                    "created_at": created_at,
                    "created_by": created_by,
                    "updated_at": updated_at,
                }
            return
        await super().executemany(query, rows)


class _DedicatedPgPool(_FakePgPool):
    """Pool stub exposing a dedicated-table-aware connection."""

    def __init__(self) -> None:
        self.conn = _DedicatedPgConnection()
        self.lists = self.conn.lists


def test_postgres_adapter_syncs_and_lists_dedicated_agent_rows():
    """Dedicated agent rows should support list reads without KV index scans."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        user_agent = _agent_record(
            agent_id="agent-user",
            description="Handles reports.",
            shared=True,
            tool_config=AgentToolConfig(
                allow=["read_file"],
                deny=["shell"],
                defaults={"timeout_seconds": 30},
            ),
        )
        team_agent = _agent_record(agent_id="agent-team", source="team")

        await adapter.upsert_agent_record("user-1", user_agent)
        await adapter.upsert_agent_record("user-1", team_agent)

        pool.conn.reject_record_json_list_reads = True
        records = await adapter.list_agent_records("user-1")

        assert [record.id for record in records] == ["agent-user"]
        assert set(pool.conn.agents) == {
            ("user-1", "agent-user"),
            ("user-1", "agent-team"),
        }
        row = pool.conn.agents[("user-1", "agent-user")]
        assert row["agent_name"] == "agent-user"
        assert row["description"] == "Handles reports."
        assert row["shared"] is True
        assert '"shared":true' in row["share_config_json"].replace(" ", "")
        assert '"allow":["read_file"]' in row["tool_config_json"].replace(" ", "")
        assert records[0].data.tool_config.allow == ["read_file"]
        assert records[0].data.tool_config.deny == ["shell"]

    asyncio.run(run())


def test_postgres_adapter_lists_agent_bindings_from_dedicated_rows():
    """Agent list rows should reconstruct Skill/MCP bindings from tables."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record(agent_id="agent-bound-list", with_bindings=True)

        await adapter.upsert_agent_record("user-1", agent)

        pool.conn.reject_record_json_list_reads = True
        records = await adapter.list_agent_records("user-1")

        assert [record.id for record in records] == ["agent-bound-list"]
        assert [binding.slug for binding in records[0].data.skill_bindings] == [
            "summarizer",
        ]
        assert [binding.name for binding in records[0].data.mcp_bindings] == [
            "search-mcp",
        ]

    asyncio.run(run())


def test_postgres_adapter_preserves_agent_binding_order_from_tables():
    """Agent list binding order should survive dedicated table round-trips."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record_with_multiple_bindings()

        await adapter.upsert_agent_record("user-1", agent)

        records = await adapter.list_agent_records("user-1")

        assert [binding.slug for binding in records[0].data.skill_bindings] == [
            "second-skill",
            "first-skill",
        ]
        assert [binding.name for binding in records[0].data.mcp_bindings] == [
            "second-mcp",
            "first-mcp",
        ]

    asyncio.run(run())


def test_postgres_adapter_backfills_binding_positions_from_record_json():
    """Schema migration should recover order for pre-position binding rows."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record_with_multiple_bindings()

        await adapter.upsert_agent_record("user-1", agent)
        for row in pool.conn.agent_skill_bindings.values():
            row["position"] = 0
        for row in pool.conn.agent_mcp_bindings.values():
            row["position"] = 0

        before = await adapter.list_agent_records("user-1")
        assert [binding.slug for binding in before[0].data.skill_bindings] == [
            "first-skill",
            "second-skill",
        ]

        await adapter.ensure_schema()
        after = await adapter.list_agent_records("user-1")

        assert [binding.slug for binding in after[0].data.skill_bindings] == [
            "second-skill",
            "first-skill",
        ]
        assert [binding.name for binding in after[0].data.mcp_bindings] == [
            "second-mcp",
            "first-mcp",
        ]

    asyncio.run(run())


def test_postgres_adapter_syncs_dedicated_agent_binding_rows():
    """Agent Skill/MCP selections should be mirrored to dedicated tables."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record(agent_id="agent-bound", with_bindings=True)

        await adapter.upsert_agent_record("user-1", agent)

        skill_row = pool.conn.agent_skill_bindings[
            ("user-1", "agent-bound", "skill-binding-1")
        ]
        assert skill_row["source"] == "skillhub"
        assert skill_row["slug"] == "summarizer"
        assert skill_row["display_name"] == "Summarizer"
        assert '"mode":"short"' in skill_row["config_json"].replace(" ", "")

        mcp_row = pool.conn.agent_mcp_bindings[
            ("user-1", "agent-bound", "mcp-binding-1")
        ]
        assert mcp_row["source"] == "skillhub"
        assert mcp_row["name"] == "search-mcp"
        assert mcp_row["endpoint_url"] == "https://example.test/mcp"
        assert '"timeout":10' in mcp_row["config_json"].replace(" ", "")

        assert await adapter.delete_agent_record("user-1", "agent-bound")
        assert pool.conn.agent_skill_bindings == {}
        assert pool.conn.agent_mcp_bindings == {}

    asyncio.run(run())


def test_postgres_adapter_replaces_agent_binding_rows_on_update():
    """Editing Agent Skill/MCP selections should remove omitted bindings."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record(agent_id="agent-rebound", with_bindings=True)

        await adapter.upsert_agent_record("user-1", agent)
        assert pool.conn.agent_skill_bindings
        assert pool.conn.agent_mcp_bindings

        updated = agent.model_copy(
            update={
                "data": agent.data.model_copy(
                    update={"skill_bindings": [], "mcp_bindings": []},
                ),
            },
        )

        await adapter.upsert_agent_record("user-1", updated)

        assert pool.conn.agent_skill_bindings == {}
        assert pool.conn.agent_mcp_bindings == {}

    asyncio.run(run())


def test_postgres_adapter_syncs_and_queries_agent_share_grant_rows():
    """Agent ACL grants should use dedicated rows for shared list queries."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        granted = _agent_record(
            user_id="owner-1",
            agent_id="agent-granted",
            shared=False,
            share_grants=[
                AgentShareGrant(
                    grantee_type="user",
                    grantee_id="viewer-1",
                    tier="run",
                    created_by="owner-1",
                ),
            ],
        )
        private = _agent_record(
            user_id="owner-2",
            agent_id="agent-private",
            shared=False,
        )

        await adapter.upsert_agent_record("owner-1", granted)
        await adapter.upsert_agent_record("owner-2", private)

        grant_rows = pool.conn.agent_share_grants
        assert len(grant_rows) == 1
        grant_row = next(iter(grant_rows.values()))
        assert grant_row["grantee_type"] == "user"
        assert grant_row["grantee_id"] == "viewer-1"
        assert grant_row["tier"] == "run"

        pool.conn.reject_record_json_list_reads = True
        records = await adapter.list_shared_agent_records("viewer-1")

        assert [record.id for record in records] == ["agent-granted"]
        assert records[0].data.share_grants[0].tier == "run"

        updated = granted.model_copy(
            update={
                "data": granted.data.model_copy(
                    update={
                        "share_grants": [
                            AgentShareGrant(
                                grantee_type="workspace",
                                grantee_id="*",
                                tier="clone",
                                created_by="owner-1",
                            ),
                        ],
                    },
                ),
            },
        )
        await adapter.upsert_agent_record("owner-1", updated)

        assert list(pool.conn.agent_share_grants) == [
            ("owner-1", "agent-granted", "workspace", "*"),
        ]
        assert (await adapter.list_shared_agent_records("viewer-2"))[
            0
        ].data.share_grants[0].tier == "clone"

        assert await adapter.delete_agent_record("owner-1", "agent-granted")
        assert pool.conn.agent_share_grants == {}

    asyncio.run(run())


def test_postgres_adapter_lists_shared_agent_rows_without_record_json():
    """Shared agent rows should list from dedicated columns only."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        shared = _agent_record(
            user_id="owner-1",
            agent_id="agent-shared",
            shared=True,
        )
        private = _agent_record(
            user_id="owner-2",
            agent_id="agent-private",
            shared=False,
        )
        own = _agent_record(
            user_id="viewer-1",
            agent_id="agent-own-shared",
            shared=True,
        )

        await adapter.upsert_agent_record("owner-1", shared)
        await adapter.upsert_agent_record("owner-2", private)
        await adapter.upsert_agent_record("viewer-1", own)

        pool.conn.reject_record_json_list_reads = True
        records = await adapter.list_shared_agent_records("viewer-1")

        assert [record.id for record in records] == ["agent-shared"]
        assert records[0].user_id == "owner-1"
        assert records[0].data.share_config.shared is True

    asyncio.run(run())


def test_postgres_adapter_lists_shared_agent_bindings_from_dedicated_rows():
    """Shared Agent list rows should attach bindings without record_json."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        shared = _agent_record(
            user_id="owner-1",
            agent_id="agent-shared-bound",
            shared=True,
            with_bindings=True,
        )

        await adapter.upsert_agent_record("owner-1", shared)

        pool.conn.reject_record_json_list_reads = True
        records = await adapter.list_shared_agent_records("viewer-1")

        assert [record.id for record in records] == ["agent-shared-bound"]
        assert [binding.slug for binding in records[0].data.skill_bindings] == [
            "summarizer",
        ]
        assert [binding.name for binding in records[0].data.mcp_bindings] == [
            "search-mcp",
        ]

    asyncio.run(run())


def test_postgres_adapter_preserves_shared_agent_binding_order_from_tables():
    """Shared Agent binding order should survive dedicated table reads."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        shared = _agent_record_with_multiple_bindings(
            user_id="owner-1",
            agent_id="agent-shared-ordered",
            shared=True,
        )

        await adapter.upsert_agent_record("owner-1", shared)

        records = await adapter.list_shared_agent_records("viewer-1")

        assert [binding.slug for binding in records[0].data.skill_bindings] == [
            "second-skill",
            "first-skill",
        ]
        assert [binding.name for binding in records[0].data.mcp_bindings] == [
            "second-mcp",
            "first-mcp",
        ]

    asyncio.run(run())


def test_postgres_adapter_syncs_and_lists_dedicated_session_rows():
    """Dedicated session rows should support agent and schedule list reads."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        first = SessionRecord(
            id="session-old",
            user_id="user-1",
            agent_id="agent-1",
            config=_session_config("ws-old"),
            source=SessionSource.USER,
        )
        second = SessionRecord(
            id="session-new",
            user_id="user-1",
            agent_id="agent-1",
            config=_session_config("ws-new"),
            source=SessionSource.SCHEDULE,
            source_schedule_id="schedule-1",
        )
        first.created_at = first.created_at.replace(year=2025)
        second.created_at = second.created_at.replace(year=2026)

        await adapter.upsert_session_record("user-1", first)
        await adapter.upsert_session_record("user-1", second)

        pool.conn.reject_record_json_list_reads = True
        pool.conn.reject_state_json_list_reads = True
        agent_sessions = await adapter.list_session_records("user-1", "agent-1")
        schedule_sessions = await adapter.list_session_records_by_schedule(
            "user-1",
            "schedule-1",
        )

        assert [record.id for record in agent_sessions] == [
            "session-new",
            "session-old",
        ]
        assert [record.id for record in schedule_sessions] == ["session-new"]
        row = pool.conn.sessions[("user-1", "session-new")]
        assert row["workspace_id"] == "ws-new"
        assert row["workspace_owner_id"] == "workspace-owner"
        assert row["session_name"]
        assert row["model_type"] == "openai"
        assert row["model_name"] == "gpt-4"
        assert row["team_id"] is None
        assert row["status"] == "idle"
        assert '"context":[]' in row["state_json"].replace(" ", "")

        await adapter.delete_session_record("user-1", "session-new")
        await adapter.delete_agent_sessions("user-1", "agent-1")

        assert pool.conn.sessions == {}

    asyncio.run(run())


def test_postgres_adapter_backfills_dedicated_rows_from_legacy_kv():
    """Startup backfill should seed dedicated rows from legacy KV records."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        agent = _agent_record(agent_id="agent-legacy")
        session = SessionRecord(
            id="session-legacy",
            user_id="user-1",
            agent_id="agent-legacy",
            config=_session_config("ws-legacy"),
            source=SessionSource.USER,
        )

        await adapter.set(
            "agentscope:user:user-1:agent:agent-legacy",
            agent.model_dump_json(),
        )
        await adapter.set(
            "agentscope:user:user-1:session:session-legacy",
            session.model_dump_json(),
        )

        await adapter.backfill_dedicated_records(
            agent_key_like=module._key_template_to_sql_like(
                "agentscope:user:{user_id}:agent:{agent_id}",
            ),
            session_key_like=module._key_template_to_sql_like(
                "agentscope:user:{user_id}:session:{session_id}",
            ),
        )

        assert ("user-1", "agent-legacy") in pool.conn.agents
        assert ("user-1", "session-legacy") in pool.conn.sessions

    asyncio.run(run())


def test_postgres_storage_public_methods_sync_dedicated_rows():
    """PostgresStorage should keep dedicated rows aligned through its API."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _DedicatedPgPool()
        async with module.PostgresStorage(pool=pool) as storage:
            agent = _agent_record(agent_id="agent-public")
            await storage.upsert_agent("user-1", agent)
            session = await storage.upsert_session(
                "user-1",
                "agent-public",
                _session_config("ws-public"),
                source=SessionSource.SCHEDULE,
                source_schedule_id="schedule-public",
            )

            assert [record.id for record in await storage.list_agents("user-1")] == [
                "agent-public",
            ]
            assert [
                record.id
                for record in await storage.list_sessions("user-1", "agent-public")
            ] == [session.id]
            assert [
                record.id
                for record in await storage.list_sessions_by_schedule(
                    "user-1",
                    "schedule-public",
                )
            ] == [session.id]

            await storage.set_session_team_id(
                "user-1",
                session.id,
                "team-public",
            )
            row = pool.conn.sessions[("user-1", session.id)]
            assert row["team_id"] == "team-public"
            assert row["workspace_owner_id"] == "workspace-owner"

            await storage.update_session_state(
                "user-1",
                "agent-public",
                session.id,
                _awaiting_permission_state(),
            )
            row = pool.conn.sessions[("user-1", session.id)]
            assert row["status"] == "awaiting_permission"
            assert '"tool_call"' in row["state_json"]

            assert await storage.delete_session(
                "user-1",
                "agent-public",
                session.id,
            )
            assert ("user-1", session.id) not in pool.conn.sessions
            assert await storage.delete_agent("user-1", "agent-public")
            assert ("user-1", "agent-public") not in pool.conn.agents

    asyncio.run(run())


def test_postgres_kv_adapter_supports_redis_negative_list_indexes():
    """PG adapter must preserve lindex/lset(-1) behavior for messages."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _FakePgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        pool.lists["messages"].extend(
            [
                {"idx": 0, "value": "first", "live": True},
                {"idx": 1, "value": "last", "live": True},
            ],
        )

        assert await adapter.lindex("messages", -1) == "last"

        await adapter.lset("messages", -1, "updated")

        assert [row["value"] for row in pool.lists["messages"]] == [
            "first",
            "updated",
        ]

    asyncio.run(run())


def test_postgres_kv_adapter_ignores_expired_rows_for_negative_indexes():
    """Expired rows must not affect Redis-style tail lookup/update."""
    async def run() -> None:
        module = importlib.import_module("agentscope.app.storage._postgres_storage")
        pool = _FakePgPool()
        adapter = module._PostgresKVAdapter(pool, table_prefix="agentscope")
        pool.lists["messages"].extend(
            [
                {"idx": 0, "value": "live", "live": True},
                {"idx": 1, "value": "expired", "live": False},
            ],
        )

        assert await adapter.lindex("messages", -1) == "live"

        await adapter.lset("messages", -1, "updated-live")

        assert [row["value"] for row in pool.lists["messages"]] == [
            "updated-live",
        ]

    asyncio.run(run())
