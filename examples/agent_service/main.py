# -*- coding: utf-8 -*-
"""The example script to start the agent service."""
import os
from pathlib import Path

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from pydantic import SecretStr

from agentscope.app import (
    LongTermMemoryBrowser,
    SubAgentTemplate,
    create_app,
)
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import PostgresStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
from agentscope.middleware import (
    AgenticMemoryMiddleware,
    Mem0Middleware,
    ReMeMiddleware,
)
from agentscope.model import DashScopeChatModel
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.rag import PostgresVectorStore

try:
    from .shared_agents import SharedAgentPolicy
except ImportError:  # pragma: no cover - supports ``uvicorn main:app``.
    if __package__:
        raise
    from shared_agents import SharedAgentPolicy  # type: ignore[no-redef]


BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding the environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable."""
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable."""
    value = os.getenv(name)
    return float(value) if value else default


def _postgres_kwargs() -> dict:
    """Build shared PostgreSQL connection kwargs from environment."""
    dsn = os.getenv("POSTGRES_DSN")
    if dsn:
        return {"dsn": dsn}
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": _env_int("POSTGRES_PORT", 5432),
        "database": os.getenv("POSTGRES_DB", "agentscope"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
    }


def _build_dashscope_memory_models() -> tuple[
    DashScopeChatModel,
    DashScopeEmbeddingModel,
] | None:
    """Build service-level models for long-term memory backends."""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return None

    credential = DashScopeCredential(api_key=SecretStr(api_key))
    return (
        DashScopeChatModel(
            credential=credential,
            model=os.getenv("MEMORY_CHAT_MODEL", "qwen3.7-max"),
            stream=False,
        ),
        DashScopeEmbeddingModel(
            credential=credential,
            model=os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-v4"),
            dimensions=_env_int("MEMORY_EMBEDDING_DIMENSIONS", 1536),
        ),
    )


def _memory_scope() -> str:
    """Return the file-memory sharing scope for filesystem backends."""
    value = os.getenv("AGENT_SERVICE_MEMORY_SCOPE", "user_agent")
    scope = value.strip().lower().replace("-", "_")
    if scope not in {"user_agent", "session"}:
        raise ValueError(
            "AGENT_SERVICE_MEMORY_SCOPE must be 'user_agent' or 'session'.",
        )
    return scope


def _long_term_memory_root(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> Path:
    """Resolve the file-memory root used by agent middlewares."""
    base = BASE_DIR / "workspaces" / user_id / agent_id
    if _memory_scope() == "session":
        return base / session_id / "long_term_memory"
    return base / "long_term_memory"


async def long_term_memory_factory(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    """Attach long-term memory middleware to Agent Service agents."""
    memory_root = _long_term_memory_root(user_id, agent_id, session_id)
    memory_root.mkdir(parents=True, exist_ok=True)
    memory_models = _build_dashscope_memory_models()

    middlewares = [
        AgenticMemoryMiddleware(workdir=str(memory_root / "agentic")),
    ]

    if _env_bool("AGENT_SERVICE_ENABLE_REME", default=True):
        memory_chat_model = None
        memory_embedding_model = None
        if memory_models is not None:
            memory_chat_model, memory_embedding_model = memory_models
        middlewares.append(
            ReMeMiddleware(
                workspace_dir=str(memory_root / "reme"),
                parameters=ReMeMiddleware.Parameters(
                    chat_model=memory_chat_model,
                    embedding_model=memory_embedding_model,
                    mode=os.getenv("REME_MEMORY_MODE", "both"),
                    top_k=_env_int("REME_MEMORY_TOP_K", 5),
                ),
            ),
        )

    mem0_default = bool(
        os.getenv("MEM0_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
    )
    if _env_bool("AGENT_SERVICE_ENABLE_MEM0", default=mem0_default):
        mem0_api_key = os.getenv("MEM0_API_KEY")
        if mem0_api_key:
            from mem0 import AsyncMemoryClient

            middlewares.append(
                Mem0Middleware(
                    user_id=user_id,
                    agent_id=agent_id,
                    client=AsyncMemoryClient(api_key=mem0_api_key),
                    mode=os.getenv("MEM0_MEMORY_MODE", "both"),
                    top_k=_env_int("MEM0_MEMORY_TOP_K", 5),
                ),
            )
        else:
            if memory_models is not None:
                chat_model, embedding_model = memory_models
                middlewares.append(
                    Mem0Middleware(
                        user_id=user_id,
                        agent_id=agent_id,
                        chat_model=chat_model,
                        embedding_model=embedding_model,
                        mode=os.getenv("MEM0_MEMORY_MODE", "both"),
                        top_k=_env_int("MEM0_MEMORY_TOP_K", 5),
                    ),
                )

    return middlewares

default_mcps = [
    # MCPClient(
    #     name="browser-use",
    #     mcp_config=StdioMCPConfig(
    #         command="npx",
    #         args=["@playwright/mcp@latest"],
    #     ),
    #     is_stateful=True,
    # ),
]

if os.getenv("AMAP_API_KEY"):
    default_mcps.append(
        MCPClient(
            name="amap",
            mcp_config=HttpMCPConfig(
                url=f"https://mcp.amap.com/mcp?key="
                f"{os.environ['AMAP_API_KEY']}",
            ),
            is_stateful=False,
        ),
    )

storage = PostgresStorage(
    **_postgres_kwargs(),
    table_prefix=os.getenv("AGENT_SERVICE_PG_TABLE_PREFIX", "agentscope"),
)

vector_store = PostgresVectorStore(
    **_postgres_kwargs(),
    table_prefix=os.getenv(
        "AGENT_SERVICE_RAG_PG_TABLE_PREFIX",
        "agentscope_rag",
    ),
)

shared_agent_policy = SharedAgentPolicy()

app = create_app(
    storage=storage,
    resource_access_policy=shared_agent_policy,
    message_bus=RedisMessageBus(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=_env_int("REDIS_PORT", 6379),
        db=_env_int("REDIS_DB", 0),
        password=os.getenv("REDIS_PASSWORD"),
        socket_connect_timeout=_env_float(
            "REDIS_SOCKET_CONNECT_TIMEOUT",
            5.0,
        ),
        socket_timeout=_env_float("REDIS_SOCKET_TIMEOUT", 30.0),
        health_check_interval=_env_int("REDIS_HEALTH_CHECK_INTERVAL", 30),
    ),
    workspace_manager=LocalWorkspaceManager(
        basedir=str(BASE_DIR / "workspaces"),
        # The default MCP servers that will be added into the workspace
        default_mcps=default_mcps,
    ),
    # Knowledge base feature backed by PostgreSQL pgvector. The
    # CollectionPerKbManager allocates one collection per knowledge base.
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    # Customize your own subagent templates
    custom_subagent_templates=[
        SubAgentTemplate(
            type="explorer",
            description=(
                "Read-only agents specialized in exploration tasks. It can "
                "read files but cannot modify, create, or delete them. Use "
                "this agent type when you need to investigate the codebase, "
                "understand its structure, or gather information from files "
                "to support planning—without making any changes."
            ),
            system_prompt_template="""You are {member_name}, an explorer \
agent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

## Responsibilities
- Complete the exploration tasks assigned by the team leader.
- You are read-only: you may inspect files and the codebase, but you must \
never modify, create, or delete anything.

## Reporting
- Always report the task result back to {leader_name} using the TeamSay \
tool, whether the task succeeds or fails.
- Keep your private reasoning private; only share conclusions and findings \
that the leader needs.

Note: `TeamSay` is your ONLY channel to communicate with {leader_name} and \
the other team members. Any other output you produce is invisible to them, \
so anything you want them to see MUST be sent through `TeamSay`.""",
            permission_context=PermissionContext(
                # Read-only
                mode=PermissionMode.EXPLORE,
            ),
        ),
    ],
    long_term_memory_browser=LongTermMemoryBrowser(_long_term_memory_root),
    extra_agent_middlewares=long_term_memory_factory,
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)


if __name__ == "__main__":
    # Start the service
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
