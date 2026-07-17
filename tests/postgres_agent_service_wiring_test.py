# -*- coding: utf-8 -*-
"""Tests for PostgreSQL-backed Agent Service example wiring."""

from collections import defaultdict
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
    spec = importlib.util.spec_from_file_location(
        "agent_service_main_test",
        Path("examples/agent_service/main.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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


class _FakePgPool:
    """Pool stub exposing acquire()."""

    def __init__(self) -> None:
        self.lists = defaultdict(list)
        self.conn = _FakePgConnection(self.lists)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


@pytest.mark.asyncio
async def test_postgres_kv_adapter_supports_redis_negative_list_indexes():
    """PG adapter must preserve lindex/lset(-1) behavior for messages."""
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


@pytest.mark.asyncio
async def test_postgres_kv_adapter_ignores_expired_rows_for_negative_indexes():
    """Expired rows must not affect Redis-style tail lookup/update."""
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
