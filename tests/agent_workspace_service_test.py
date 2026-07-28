# -*- coding: utf-8 -*-
"""Tests for Agent-level workspace resolution and scaffolding."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient

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
from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app._router import _workspace as workspace_router_module
from agentscope.app._service import AgentView, AgentWorkspaceService
from agentscope.app.storage import AgentData, AgentRecord, SessionConfig, SessionRecord
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.skill import Skill


class _FakeWorkspace:
    """Local workspace stub with a real workdir."""

    def __init__(self, workdir: Path, workspace_id: str) -> None:
        self.workdir = str(workdir)
        self.host_workdir = str(workdir)
        self.workspace_id = workspace_id
        self.skills: list[Skill] = []
        self.mcps: list[MCPClient] = []

    async def list_skills(self) -> list[Skill]:
        """Return fake workspace skills."""
        return self.skills

    async def add_skill(self, skill_path: str) -> None:
        """Record a skill as installed in this workspace."""
        name = Path(skill_path).name
        self.skills.append(
            Skill(
                name=name,
                description=f"{name} skill",
                dir=skill_path,
                markdown="",
                updated_at=0.0,
            ),
        )

    async def remove_skill(self, name: str) -> None:
        """Remove a fake skill by name."""
        self.skills = [skill for skill in self.skills if skill.name != name]

    async def list_mcps(self) -> list[MCPClient]:
        """Return fake workspace MCP clients."""
        return self.mcps

    async def add_mcp(self, mcp: MCPClient) -> None:
        """Record a fake MCP client."""
        self.mcps.append(mcp)

    async def remove_mcp(self, name: str) -> None:
        """Remove a fake MCP client by name."""
        self.mcps = [mcp for mcp in self.mcps if mcp.name != name]


class _FakeRemoteWorkspace:
    """Remote workspace stub exposing only a sandbox path."""

    def __init__(self, workspace_id: str) -> None:
        self.workdir = "/workspace"
        self.workspace_id = workspace_id


class _FakeWorkspaceManager:
    """Workspace manager stub recording Agent workspace lookups."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.calls: list[tuple[str, str, str, str]] = []
        self.remote = False
        self.workspaces: dict[str, _FakeWorkspace] = {}

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> _FakeWorkspace:
        """Return a workspace rooted at the configured temp directory."""
        self.calls.append((user_id, agent_id, session_id, workspace_id))
        if self.remote:
            return _FakeRemoteWorkspace(workspace_id)
        if workspace_id not in self.workspaces:
            self.workspaces[workspace_id] = _FakeWorkspace(
                self.workdir,
                workspace_id,
            )
        return self.workspaces[workspace_id]


class _FakeAccess:
    """Access stub returning an editable Agent."""

    def __init__(self, agent: AgentRecord, *, editable: bool = True) -> None:
        self.agent = agent
        self.editable = editable

    async def get_resource(
        self,
        _user_id: str,
        _kind,
        _resource_id: str,
    ) -> AgentView:
        """Return the viewer-relative Agent view."""
        return AgentView.model_validate(
            {**self.agent.model_dump(), "editable": self.editable},
        )

    async def resolve_for_edit(
        self,
        _user_id: str,
        _kind,
        _resource_id: str,
    ) -> tuple[str, AgentRecord]:
        """Return the Agent owner and record."""
        return self.agent.user_id, self.agent


class _FunctionalStorage:
    """Small in-memory storage used by the HTTP functional smoke test."""

    def __init__(self) -> None:
        self.agents: dict[tuple[str, str], AgentRecord] = {}
        self.sessions: dict[tuple[str, str, str], SessionRecord] = {}

    async def __aenter__(self) -> "_FunctionalStorage":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an Agent record."""
        agent = agent_record.model_copy(update={"user_id": user_id})
        self.agents[(user_id, agent.id)] = agent
        return agent.id

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Return one Agent record."""
        return self.agents.get((user_id, agent_id))

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return all Agents for the user."""
        return [
            record
            for (owner, _agent_id), record in self.agents.items()
            if owner == user_id
        ]

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config,
        session_id: str | None = None,
    ) -> SessionRecord:
        """Persist a Session record."""
        session = SessionRecord(
            id=session_id or f"session-{len(self.sessions) + 1}",
            user_id=user_id,
            agent_id=agent_id,
            config=config,
        )
        self.sessions[(user_id, agent_id, session.id)] = session
        return session

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return sessions for the user and Agent."""
        return [
            record
            for (owner, owner_agent_id, _session_id), record
            in self.sessions.items()
            if owner == user_id and owner_agent_id == agent_id
        ]

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return one Session record."""
        return self.sessions.get((user_id, agent_id, session_id))

    async def list_all_schedules(self) -> list:
        """Return no persisted schedules for the functional smoke test."""
        return []


def _agent_record() -> AgentRecord:
    """Build a representative Agent record."""
    return AgentRecord(
        id="agent-1",
        user_id="owner-1",
        data=AgentData(
            name="Research agent",
            system_prompt="Use the local workspace.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _expected_agent_workspace_id(user_id: str, agent_id: str) -> str:
    """Mirror the public Agent workspace id contract."""
    return hashlib.blake2b(
        f"{user_id}::{agent_id}".encode("utf-8"),
        digest_size=8,
    ).hexdigest()


def test_agent_workspace_service_scaffolds_and_refreshes_tools_config(
    tmp_path: Path,
) -> None:
    """Preparing an Agent workspace should scaffold and refresh tools.json."""
    async def run() -> None:
        (tmp_path / "tools.json").write_text(
            json.dumps({"custom": True}),
            encoding="utf-8",
        )
        manager = _FakeWorkspaceManager(tmp_path)
        service = AgentWorkspaceService(manager)
        agent = _agent_record()

        workspace = await service.prepare_agent_workspace(
            user_id="owner-1",
            agent_record=agent,
        )

        expected_id = _expected_agent_workspace_id("owner-1", "agent-1")
        assert workspace.workspace_id == expected_id
        assert manager.calls == [
            ("owner-1", "agent-1", "__agent__", expected_id),
        ]
        assert (tmp_path / "AGENTS.md").is_file()
        assert "Research agent" in (tmp_path / "AGENTS.md").read_text(
            encoding="utf-8",
        )
        assert (tmp_path / "skills").is_dir()
        assert (tmp_path / "subagents").is_dir()
        assert (tmp_path / "memory").is_dir()
        assert (tmp_path / "memory" / ".gitkeep").is_file()
        assert json.loads((tmp_path / "tools.json").read_text(
            encoding="utf-8",
        )) == {
            "allow": [],
            "deny": [],
            "defaults": {},
            "mcpServers": {},
        }

    asyncio.run(run())


def test_agent_workspace_service_skips_sandbox_only_paths(
    tmp_path: Path,
) -> None:
    """Sandbox-only workdir paths must not be treated as host paths."""
    async def run() -> None:
        manager = _FakeWorkspaceManager(tmp_path)
        manager.remote = True
        service = AgentWorkspaceService(manager)

        workspace = await service.prepare_agent_workspace(
            user_id="owner-1",
            agent_record=_agent_record(),
        )

        assert workspace.workdir == "/workspace"
        assert list(tmp_path.iterdir()) == []
        assert service.list_agent_workspace_files(workspace=workspace) == []

    asyncio.run(run())


def test_agent_workspace_endpoint_lists_sessionless_workspace(
    tmp_path: Path,
) -> None:
    """The workspace router should expose Agent workspace files by agent id."""
    async def run() -> None:
        service = AgentWorkspaceService(_FakeWorkspaceManager(tmp_path))
        agent = _agent_record()

        endpoint = getattr(workspace_router_module, "get_agent_workspace_view")
        response = await endpoint(
            agent_id="agent-1",
            user_id="viewer-1",
            access=_FakeAccess(agent),
            agent_workspace_service=service,
        )

        paths = {entry.path for entry in response.files}
        assert response.agent_id == "agent-1"
        assert response.workspace_id == _expected_agent_workspace_id(
            "owner-1",
            "agent-1",
        )
        assert "AGENTS.md" in paths
        assert "tools.json" in paths
        assert "skills" in paths
        assert "subagents" in paths
        assert "memory" in paths

    asyncio.run(run())


def test_agent_workspace_endpoint_allows_read_only_shared_agent(
    tmp_path: Path,
) -> None:
    """Read-only shared users should be able to view Agent workspace files."""
    async def run() -> None:
        service = AgentWorkspaceService(_FakeWorkspaceManager(tmp_path))
        agent = _agent_record()
        await service.prepare_agent_workspace(
            user_id="owner-1",
            agent_record=agent,
        )

        response = await workspace_router_module.get_agent_workspace_view(
            agent_id="agent-1",
            user_id="viewer-1",
            access=_FakeAccess(agent, editable=False),
            agent_workspace_service=service,
        )

        assert response.workspace_id == _expected_agent_workspace_id(
            "owner-1",
            "agent-1",
        )
        assert "AGENTS.md" in {entry.path for entry in response.files}

    asyncio.run(run())


def test_sessionless_workspace_skill_and_mcp_lists_use_agent_workspace(
    tmp_path: Path,
) -> None:
    """Skill/MCP panel reads should not require a session id."""

    async def run() -> None:
        manager = _FakeWorkspaceManager(tmp_path)
        service = AgentWorkspaceService(manager)
        agent = _agent_record()
        workspace = await service.prepare_agent_workspace(
            user_id="owner-1",
            agent_record=agent,
        )
        workspace.skills.append(
            Skill(
                name="demo-skill",
                description="Demo skill",
                dir=str(tmp_path / "skills" / "demo-skill"),
                markdown="",
                updated_at=0.0,
            ),
        )
        workspace.mcps.append(
            MCPClient(
                name="demo-mcp",
                is_stateful=False,
                mcp_config=HttpMCPConfig(url="http://127.0.0.1/mcp"),
            ),
        )

        skills = await workspace_router_module.list_skills(
            agent_id="agent-1",
            session_id=None,
            user_id="viewer-1",
            storage=_FunctionalStorage(),
            workspace_manager=manager,
            access=_FakeAccess(agent, editable=False),
            agent_workspace_service=service,
        )
        mcps = await workspace_router_module.list_mcps(
            agent_id="agent-1",
            session_id=None,
            user_id="viewer-1",
            storage=_FunctionalStorage(),
            workspace_manager=manager,
            access=_FakeAccess(agent, editable=False),
            agent_workspace_service=service,
        )

        assert [skill.name for skill in skills] == ["demo-skill"]
        assert [mcp.name for mcp in mcps] == ["demo-mcp"]

    asyncio.run(run())


def test_sessionless_workspace_writes_require_edit_access(
    tmp_path: Path,
) -> None:
    """Read-only shared users must not mutate an Agent workspace."""

    async def run() -> None:
        manager = _FakeWorkspaceManager(tmp_path)
        service = AgentWorkspaceService(manager)
        agent = _agent_record()

        try:
            await workspace_router_module.add_mcp(
                mcp=MCPClient(
                    name="demo-mcp",
                    is_stateful=False,
                    mcp_config=HttpMCPConfig(url="http://127.0.0.1/mcp"),
                ),
                agent_id="agent-1",
                session_id=None,
                user_id="viewer-1",
                storage=_FunctionalStorage(),
                workspace_manager=manager,
                access=_FakeAccess(agent, editable=False),
                agent_workspace_service=service,
            )
        except Exception as exc:  # noqa: BLE001 - assert HTTP status below
            assert getattr(exc, "status_code", None) == 403
        else:
            raise AssertionError("Expected read-only Agent workspace write to fail")

    asyncio.run(run())


def test_session_workspace_writes_require_edit_access(
    tmp_path: Path,
) -> None:
    """Legacy session-id writes must also respect Agent edit grants."""

    async def run() -> None:
        manager = _FakeWorkspaceManager(tmp_path)
        storage = _FunctionalStorage()
        await storage.upsert_session(
            user_id="viewer-1",
            agent_id="agent-1",
            config=SessionConfig(
                workspace_id=_expected_agent_workspace_id("owner-1", "agent-1"),
                workspace_owner_id="owner-1",
            ),
            session_id="session-1",
        )

        try:
            await workspace_router_module.add_mcp(
                mcp=MCPClient(
                    name="demo-mcp",
                    is_stateful=False,
                    mcp_config=HttpMCPConfig(url="http://127.0.0.1/mcp"),
                ),
                agent_id="agent-1",
                session_id="session-1",
                user_id="viewer-1",
                storage=storage,
                workspace_manager=manager,
                access=_FakeAccess(_agent_record(), editable=False),
                agent_workspace_service=AgentWorkspaceService(manager),
            )
        except Exception as exc:  # noqa: BLE001 - assert HTTP status below
            assert getattr(exc, "status_code", None) == 403
        else:
            raise AssertionError("Expected read-only session workspace write to fail")

    asyncio.run(run())


def test_agent_workspace_http_flow_uses_agent_workspace(
    tmp_path: Path,
) -> None:
    """Create Agent, list workspace, and create Session through HTTP."""
    storage = _FunctionalStorage()
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=LocalWorkspaceManager(str(tmp_path)),
        enable_index_worker=False,
    )

    with TestClient(app) as client:
        headers = {"X-User-ID": "owner-1"}
        created = client.post(
            "/agent/",
            headers=headers,
            json={
                "name": "Research agent",
                "context_config": {},
                "react_config": {},
            },
        )
        assert created.status_code == 201
        agent_id = created.json()["agent_id"]

        workspace = client.get(
            "/workspace/agent",
            headers=headers,
            params={"agent_id": agent_id},
        )
        assert workspace.status_code == 200
        workspace_body = workspace.json()
        expected_id = _expected_agent_workspace_id("owner-1", agent_id)
        assert workspace_body["workspace_id"] == expected_id
        assert workspace_body["workdir"] == str(tmp_path / agent_id)
        assert {entry["path"] for entry in workspace_body["files"]} >= {
            "AGENTS.md",
            "tools.json",
            "skills",
            "subagents",
            "memory",
        }
        assert "agentscope-core" not in workspace_body["workdir"]

        session = client.post(
            "/sessions/",
            headers=headers,
            json={"agent_id": agent_id},
        )
        assert session.status_code == 201
        session_record = next(iter(storage.sessions.values()))
        assert session_record.config.workspace_id == expected_id
