# -*- coding: utf-8 -*-
"""Tests for materialising Agent SkillHub bindings into workspaces."""

from __future__ import annotations

import io
import asyncio
import zipfile
from pathlib import Path
import sys
import types

import httpx

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

from agentscope.app._service import AgentBindingResolver
from agentscope.app._service._agent_binding import MAX_SKILL_ARCHIVE_BYTES
from agentscope.app._service._skillhub_catalog import SkillHubCatalogClient
from agentscope.app._router._schema import (
    CreateAgentRequest,
    CreateSessionRequest,
    UpdateAgentRequest,
)
from agentscope.app._router._agent import create_agent, update_agent
from agentscope.app._router._session import create_session
from agentscope.app.storage import (
    AgentData,
    AgentMcpBinding,
    AgentRecord,
    AgentSkillBinding,
    SessionConfig,
    SessionRecord,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.skill import Skill


class _FakeWorkspace:
    """Workspace stub that records materialised resources."""

    def __init__(self, workdir: str | None = None) -> None:
        self.workdir = workdir or "fake-workspace"
        self.skill_paths: list[str] = []
        self.mcps: list = []
        self.skills: list[Skill] = []
        self.removed_skills: list[str] = []
        self.removed_mcps: list[str] = []
        self.fail_add_mcp = False

    async def add_skill(self, skill_path: str) -> None:
        """Record a skill path and verify it contains a SKILL.md."""
        assert Path(skill_path, "SKILL.md").is_file()
        self.skill_paths.append(skill_path)

    async def add_mcp(self, mcp_client) -> None:
        """Record an MCP client."""
        if self.fail_add_mcp:
            self.fail_add_mcp = False
            raise RuntimeError("add mcp failed")
        self.mcps.append(mcp_client)

    async def list_skills(self) -> list[Skill]:
        """Return configured fake workspace skills."""
        return self.skills

    async def remove_skill(self, name: str) -> None:
        """Record a removed skill name."""
        self.removed_skills.append(name)
        self.skills = [skill for skill in self.skills if skill.name != name]

    async def list_mcps(self) -> list:
        """Return configured fake workspace MCP clients."""
        return self.mcps

    async def remove_mcp(self, name: str) -> None:
        """Record a removed MCP name."""
        self.removed_mcps.append(name)
        self.mcps = [mcp for mcp in self.mcps if mcp.name != name]


class _FakeStorage:
    """Storage stub for session creation."""

    def __init__(self) -> None:
        self.session: SessionRecord | None = None
        self.sessions: list[SessionRecord] = []
        self.upserted_agent: AgentRecord | None = None

    async def upsert_session(self, **kwargs) -> SessionRecord:
        """Store and return a session record."""
        self.session = SessionRecord(
            id="session-1",
            user_id=kwargs["user_id"],
            agent_id=kwargs["agent_id"],
            config=kwargs["config"],
        )
        return self.session

    async def upsert_agent(self, _user_id: str, agent_record: AgentRecord) -> str:
        """Store and return an Agent record id."""
        self.upserted_agent = agent_record
        return agent_record.id

    async def list_sessions(
        self,
        _user_id: str,
        _agent_id: str,
    ) -> list[SessionRecord]:
        """Return configured session records."""
        return self.sessions


class _FakeWorkspaceManager:
    """Workspace manager stub for session creation."""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self.workspace = workspace
        self.assign_calls: list[tuple[str, str, str]] = []
        self.calls: list[tuple[str, str, str, str]] = []

    def assign_workspace_id(self, **kwargs) -> str:
        """Return a deterministic workspace id."""
        self.assign_calls.append(
            (
                kwargs["user_id"],
                kwargs["agent_id"],
                kwargs["session_id"],
            ),
        )
        return "workspace-1"

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> _FakeWorkspace:
        """Record the lookup and return the fake workspace."""
        self.calls.append((user_id, agent_id, session_id, workspace_id))
        return self.workspace


class _FakeAgentWorkspaceService:
    """Agent workspace service stub for router tests."""

    def __init__(
        self,
        workspace: _FakeWorkspace,
        workspace_id: str = "agent-workspace-1",
    ) -> None:
        self.workspace = workspace
        self.workspace_id = workspace_id
        self.id_calls: list[tuple[str, str]] = []
        self.prepare_calls: list[tuple[str, AgentRecord]] = []
        self.write_tools_calls: list[tuple[_FakeWorkspace, AgentRecord]] = []

    def agent_workspace_id(self, *, user_id: str, agent_id: str) -> str:
        """Return the Agent-level workspace id."""
        self.id_calls.append((user_id, agent_id))
        return self.workspace_id

    async def prepare_agent_workspace(
        self,
        *,
        user_id: str,
        agent_record: AgentRecord,
        sync_tools_config: bool = True,
    ) -> _FakeWorkspace:
        """Record the lookup and return the fake workspace."""
        self.prepare_calls.append((user_id, agent_record))
        return self.workspace

    async def write_tools_config(
        self,
        workspace: _FakeWorkspace,
        agent_record: AgentRecord,
    ) -> None:
        """Record a tools config write."""
        self.write_tools_calls.append((workspace, agent_record))


class _FakeAccess:
    """Resource access stub returning one agent."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(self, _user_id: str, _agent_id: str) -> AgentRecord:
        """Return the configured agent."""
        return self.agent

    async def resolve_for_edit(
        self,
        _user_id: str,
        _kind,
        _resource_id: str,
    ) -> tuple[str, AgentRecord]:
        """Return the editable owner and Agent."""
        return self.agent.user_id, self.agent


class _RecordingResolver:
    """Resolver stub recording materialisation calls."""

    def __init__(self) -> None:
        self.calls: list[
            tuple[str, AgentRecord, _FakeWorkspace, str | None]
        ] = []
        self.reconcile_calls: list[
            tuple[
                str,
                AgentRecord,
                AgentRecord,
                _FakeWorkspace,
                str | None,
            ]
        ] = []

    async def materialize_agent_bindings(
        self,
        *,
        user_id: str,
        agent_record: AgentRecord,
        workspace: _FakeWorkspace,
        skillhub_token: str | None = None,
    ):
        """Record the call and return an empty result."""
        self.calls.append((user_id, agent_record, workspace, skillhub_token))
        return type("Result", (), {"errors": []})()

    async def reconcile_agent_bindings(
        self,
        *,
        user_id: str,
        before_agent_record: AgentRecord,
        after_agent_record: AgentRecord,
        workspace: _FakeWorkspace,
        skillhub_token: str | None = None,
    ):
        """Record a reconcile call and return an empty result."""
        self.reconcile_calls.append(
            (
                user_id,
                before_agent_record,
                after_agent_record,
                workspace,
                skillhub_token,
            ),
        )
        return type("Result", (), {"errors": []})()


class _FailingResolver(_RecordingResolver):
    """Resolver stub that reports materialization/reconcile errors."""

    async def materialize_agent_bindings(self, **kwargs):
        """Return a materialization error."""
        await super().materialize_agent_bindings(**kwargs)
        return type("Result", (), {"errors": ["skill missing"]})()

    async def reconcile_agent_bindings(self, **kwargs):
        """Return a reconcile error."""
        await super().reconcile_agent_bindings(**kwargs)
        return type("Result", (), {"errors": ["remove failed"]})()


def _skill_zip() -> bytes:
    """Build a minimal SkillHub skill package."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "demo-skill/SKILL.md",
            "---\nname: demo-skill\ndescription: Demo skill\n---\nUse me.",
        )
    return buf.getvalue()


def _agent_with_defaults(**kwargs) -> AgentRecord:
    """Build an AgentRecord with required default config fields."""
    return AgentRecord(
        id=kwargs.pop("id", "agent-1"),
        user_id=kwargs.pop("user_id", "user-1"),
        data=AgentData(
            name=kwargs.pop("name", "agent"),
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            **kwargs,
        ),
    )


def test_create_session_does_not_block_on_agent_binding_materialization() -> None:
    """Creating a session should not sync Agent bindings inline."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        storage = _FakeStorage()
        workspace_manager = _FakeWorkspaceManager(workspace)
        agent = _agent_with_defaults(
            skill_bindings=[
                AgentSkillBinding(namespace="global", slug="demo-skill"),
            ],
        )

        response = await create_session(
            body=CreateSessionRequest(agent_id="agent-1"),
            user_id="user-1",
            storage=storage,
            workspace_manager=workspace_manager,
            agent_workspace_service=_FakeAgentWorkspaceService(
                workspace,
                workspace_id="agent-workspace-1",
            ),
            access=_FakeAccess(agent),
        )

        assert response.session_id == "session-1"
        assert storage.session is not None
        assert storage.session.config.workspace_id == "agent-workspace-1"
        assert workspace_manager.assign_calls == []
        assert workspace_manager.calls == []

    asyncio.run(run())


def test_create_session_records_shared_agent_workspace_owner() -> None:
    """Shared Agent sessions should remember the Agent workspace owner."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        storage = _FakeStorage()
        workspace_manager = _FakeWorkspaceManager(workspace)
        agent_workspace_service = _FakeAgentWorkspaceService(
            workspace,
            workspace_id="owner-agent-workspace",
        )
        agent = _agent_with_defaults(
            id="agent-1",
            user_id="owner-1",
        )

        response = await create_session(
            body=CreateSessionRequest(agent_id="agent-1"),
            user_id="viewer-1",
            storage=storage,
            workspace_manager=workspace_manager,
            agent_workspace_service=agent_workspace_service,
            access=_FakeAccess(agent),
        )

        assert response.session_id == "session-1"
        assert storage.session is not None
        assert storage.session.user_id == "viewer-1"
        assert storage.session.config.workspace_id == "owner-agent-workspace"
        assert storage.session.config.workspace_owner_id == "owner-1"
        assert agent_workspace_service.id_calls == [
            ("owner-1", "agent-1"),
        ]
        assert workspace_manager.assign_calls == []
        assert workspace_manager.calls == []

    asyncio.run(run())


def test_create_agent_materializes_bindings_to_agent_workspace() -> None:
    """Creating an Agent should sync selected Skill/MCP bindings immediately."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        storage = _FakeStorage()
        agent_workspace_service = _FakeAgentWorkspaceService(workspace)
        resolver = _RecordingResolver()

        response = await create_agent(
            body=CreateAgentRequest(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                skill_bindings=[
                    AgentSkillBinding(namespace="global", slug="demo-skill"),
                ],
                mcp_bindings=[
                    AgentMcpBinding(
                        name="demo-mcp",
                        endpoint_url="https://mcp.example.com/mcp",
                    ),
                ],
            ),
            x_skillhub_token="user-token",
            user_id="user-1",
            storage=storage,
            agent_workspace_service=agent_workspace_service,
            binding_resolver=resolver,
        )

        assert response.agent_id == storage.upserted_agent.id
        assert agent_workspace_service.prepare_calls == [
            ("user-1", storage.upserted_agent),
        ]
        assert resolver.calls == [
            ("user-1", storage.upserted_agent, workspace, "user-token"),
        ]

    asyncio.run(run())


def test_create_agent_reports_binding_materialize_errors() -> None:
    """Create Agent should not silently save failed binding materialization."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        storage = _FakeStorage()

        try:
            await create_agent(
                body=CreateAgentRequest(
                    name="agent",
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                    skill_bindings=[
                        AgentSkillBinding(namespace="global", slug="missing"),
                    ],
                ),
                x_skillhub_token=None,
                user_id="user-1",
                storage=storage,
                agent_workspace_service=_FakeAgentWorkspaceService(workspace),
                binding_resolver=_FailingResolver(),
            )
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 502
            assert "skill missing" in str(getattr(exc, "detail", ""))
        else:
            raise AssertionError("Expected binding materialize failure.")

        assert storage.upserted_agent is None

    asyncio.run(run())


def test_binding_resolver_materializes_enabled_skill_and_mcp(
    tmp_path: Path,
) -> None:
    """Enabled Agent bindings should become workspace skills and MCPs."""
    async def run() -> None:
        seen_urls: list[str] = []
        seen_auth_headers: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            seen_auth_headers.append(request.headers.get("Authorization"))
            if request.url.path.endswith("/resolve"):
                return httpx.Response(
                    200,
                    json={
                        "namespace": "global",
                        "slug": "demo-skill",
                        "version": "1.0.0",
                        "downloadUrl": (
                            "https://skillhub.example.com/api/cli/v1/skills/"
                            "global/demo-skill/versions/1.0.0/download"
                        ),
                    },
                )
            if request.url.path.endswith("/download"):
                return httpx.Response(200, content=_skill_zip())
            return httpx.Response(404)

        client = SkillHubCatalogClient(
            base_url="https://skillhub.example.com",
            service_token="service-token",
            transport=httpx.MockTransport(handler),
        )
        resolver = AgentBindingResolver(
            skillhub_client=client,
            cache_dir=tmp_path,
        )
        workspace = _FakeWorkspace()
        agent = AgentRecord(
            id="agent-1",
            user_id="user-1",
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                skill_bindings=[
                    AgentSkillBinding(
                        namespace="global",
                        slug="demo-skill",
                        version="1.0.0",
                    ),
                    AgentSkillBinding(
                        namespace="global",
                        slug="disabled-skill",
                        enabled=False,
                    ),
                ],
                mcp_bindings=[
                    AgentMcpBinding(
                        name="demo_mcp",
                        endpoint_url="https://mcp.example.com/sse",
                        transport="sse",
                    ),
                ],
            ),
        )

        result = await resolver.materialize_agent_bindings(
            user_id="user-1",
            agent_record=agent,
            workspace=workspace,
            skillhub_token="user-token",
        )

        assert result.skills_added == 1
        assert result.mcps_added == 1
        assert result.errors == []
        assert len(workspace.skill_paths) == 1
        assert workspace.mcps[0].name == "demo_mcp"
        assert workspace.mcps[0].mcp_config.url == "https://mcp.example.com/sse"
        assert any("/resolve?version=1.0.0" in url for url in seen_urls)
        assert seen_auth_headers == ["Bearer user-token", "Bearer user-token"]

    asyncio.run(run())


def test_binding_resolver_adds_skillhub_token_to_mcp_http_config() -> None:
    """SkillHub MCP bindings should carry the user's token at runtime."""
    async def run() -> None:
        resolver = AgentBindingResolver()
        workspace = _FakeWorkspace()
        agent = AgentRecord(
            id="agent-1",
            user_id="user-1",
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                mcp_bindings=[
                    AgentMcpBinding(
                        source="skillhub",
                        skillhub_server_id="bdc4_group",
                        name="bdc4_group",
                        endpoint_url="https://skillhub.example.com/mcp/bdc4",
                        transport="http",
                    ),
                    AgentMcpBinding(
                        source="custom",
                        name="custom_mcp",
                        endpoint_url="https://mcp.example.com/custom",
                    ),
                ],
            ),
        )

        result = await resolver.materialize_agent_bindings(
            user_id="user-1",
            agent_record=agent,
            workspace=workspace,
            skillhub_token=" user-token ",
        )

        assert result.errors == []
        assert result.mcps_added == 2
        skillhub_mcp = next(
            mcp for mcp in workspace.mcps if mcp.name == "bdc4_group"
        )
        custom_mcp = next(
            mcp for mcp in workspace.mcps if mcp.name == "custom_mcp"
        )
        assert skillhub_mcp.mcp_config.headers == {
            "Authorization": "Bearer user-token",
            "x-skillhub-token": "user-token",
        }
        assert custom_mcp.mcp_config.headers is None

    asyncio.run(run())


def test_binding_resolver_does_not_partially_materialize_on_errors(
    tmp_path: Path,
) -> None:
    """Bad bindings should not leave partial workspace materialization."""
    async def run() -> None:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"msg": "missing"})

        client = SkillHubCatalogClient(
            base_url="https://skillhub.example.com",
            transport=httpx.MockTransport(handler),
        )
        resolver = AgentBindingResolver(
            skillhub_client=client,
            cache_dir=tmp_path,
        )
        workspace = _FakeWorkspace()
        agent = AgentRecord(
            id="agent-1",
            user_id="user-1",
            data=AgentData(
                name="agent",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
                skill_bindings=[
                    AgentSkillBinding(namespace="global", slug="missing"),
                ],
                mcp_bindings=[
                    AgentMcpBinding(
                        name="still_added",
                        endpoint_url="https://mcp.example.com/mcp",
                        transport="streamable-http",
                    ),
                ],
            ),
        )

        result = await resolver.materialize_agent_bindings(
            user_id="user-1",
            agent_record=agent,
            workspace=workspace,
        )

        assert result.skills_added == 0
        assert result.mcps_added == 0
        assert len(result.errors) == 1
        assert "missing" in result.errors[0]
        assert workspace.mcps == []

    asyncio.run(run())


def test_binding_resolver_reconciles_agent_binding_edits(
    tmp_path: Path,
) -> None:
    """Editing Agent bindings should sync workspace additions and removals."""
    async def run() -> None:
        old_skill_dir = tmp_path / "old-skill"
        old_skill_dir.mkdir()
        (old_skill_dir / "SKILL.md").write_text(
            "---\nname: old-skill\ndescription: Old skill\n---\n",
            encoding="utf-8",
        )
        new_skill_dir = tmp_path / "new-skill"
        new_skill_dir.mkdir()
        (new_skill_dir / "SKILL.md").write_text(
            "---\nname: new-skill\ndescription: New skill\n---\n",
            encoding="utf-8",
        )

        resolver = AgentBindingResolver()
        workspace = _FakeWorkspace()
        workspace.skills = [
            Skill(
                name="old-skill",
                description="Old skill",
                dir=str(old_skill_dir),
                markdown="",
                updated_at=0.0,
            ),
        ]
        workspace.mcps = [
            type("Mcp", (), {"name": "old-mcp"})(),
        ]
        before = _agent_with_defaults(
            skill_bindings=[
                AgentSkillBinding(
                    source="custom",
                    slug="old-skill",
                    display_name="old-skill",
                    config_json={"skill_path": str(old_skill_dir)},
                ),
            ],
            mcp_bindings=[
                AgentMcpBinding(
                    name="old-mcp",
                    endpoint_url="https://old.example.com/mcp",
                ),
            ],
        )
        after = _agent_with_defaults(
            skill_bindings=[
                AgentSkillBinding(
                    source="custom",
                    slug="new-skill",
                    display_name="new-skill",
                    config_json={"skill_path": str(new_skill_dir)},
                ),
            ],
            mcp_bindings=[
                AgentMcpBinding(
                    name="new-mcp",
                    endpoint_url="https://new.example.com/mcp",
                ),
            ],
        )

        result = await resolver.reconcile_agent_bindings(
            user_id="user-1",
            before_agent_record=before,
            after_agent_record=after,
            workspace=workspace,
        )

        assert workspace.removed_skills == ["old-skill"]
        assert workspace.removed_mcps == ["old-mcp"]
        assert workspace.skill_paths == [str(new_skill_dir)]
        assert [mcp.name for mcp in workspace.mcps] == ["new-mcp"]
        assert result.skills_added == 1
        assert result.mcps_added == 1

    asyncio.run(run())


def test_binding_resolver_refreshes_existing_skillhub_mcp_auth() -> None:
    """Existing SkillHub HTTP MCPs should be refreshed when auth headers are absent."""
    async def run() -> None:
        resolver = AgentBindingResolver()
        workspace = _FakeWorkspace()
        workspace.mcps = [
            MCPClient(
                name="bdc4_group",
                is_stateful=False,
                mcp_config=HttpMCPConfig(
                    url="https://skillhub.example.com/mcp/bdc4",
                ),
            ),
        ]
        before = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    source="skillhub",
                    skillhub_server_id="bdc4_group",
                    name="bdc4_group",
                    endpoint_url="https://skillhub.example.com/mcp/bdc4",
                ),
            ],
        )
        after = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    source="skillhub",
                    skillhub_server_id="bdc4_group",
                    name="bdc4_group",
                    endpoint_url="https://skillhub.example.com/mcp/bdc4",
                ),
            ],
        )

        result = await resolver.reconcile_agent_bindings(
            user_id="user-1",
            before_agent_record=before,
            after_agent_record=after,
            workspace=workspace,
            skillhub_token="user-token",
        )

        assert result.errors == []
        assert result.mcps_added == 1
        assert workspace.removed_mcps == ["bdc4_group"]
        assert len(workspace.mcps) == 1
        assert workspace.mcps[0].mcp_config.headers == {
            "Authorization": "Bearer user-token",
            "x-skillhub-token": "user-token",
        }

    asyncio.run(run())


def test_binding_resolver_rejects_large_skillhub_download(
    tmp_path: Path,
) -> None:
    """SkillHub downloads should have the same archive-size guard as uploads."""

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/resolve"):
                return httpx.Response(
                    200,
                    json={
                        "namespace": "global",
                        "slug": "huge-skill",
                        "version": "1.0.0",
                        "downloadUrl": (
                            "https://skillhub.example.com/api/cli/v1/skills/"
                            "global/huge-skill/versions/1.0.0/download"
                        ),
                    },
                )
            if request.url.path.endswith("/download"):
                return httpx.Response(
                    200,
                    content=b"x" * (MAX_SKILL_ARCHIVE_BYTES + 1),
                )
            return httpx.Response(404)

        client = SkillHubCatalogClient(
            base_url="https://skillhub.example.com",
            transport=httpx.MockTransport(handler),
        )
        resolver = AgentBindingResolver(
            skillhub_client=client,
            cache_dir=tmp_path,
        )
        workspace = _FakeWorkspace()
        agent = _agent_with_defaults(
            skill_bindings=[
                AgentSkillBinding(
                    namespace="global",
                    slug="huge-skill",
                    version="1.0.0",
                ),
            ],
        )

        result = await resolver.materialize_agent_bindings(
            user_id="user-1",
            agent_record=agent,
            workspace=workspace,
        )

        assert result.skills_added == 0
        assert workspace.skill_paths == []
        assert result.errors
        assert "too large" in result.errors[0]

    asyncio.run(run())


def test_binding_resolver_replaces_changed_mcp_with_same_name() -> None:
    """Editing a same-name MCP should refresh the runtime workspace config."""
    async def run() -> None:
        resolver = AgentBindingResolver()
        workspace = _FakeWorkspace()
        workspace.mcps = [type("Mcp", (), {"name": "search-mcp"})()]
        before = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    binding_id="mcp-1",
                    name="search-mcp",
                    endpoint_url="https://old.example.com/mcp",
                ),
            ],
        )
        after = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    binding_id="mcp-1",
                    name="search-mcp",
                    endpoint_url="https://new.example.com/mcp",
                ),
            ],
        )

        result = await resolver.reconcile_agent_bindings(
            user_id="user-1",
            before_agent_record=before,
            after_agent_record=after,
            workspace=workspace,
        )

        assert workspace.removed_mcps == ["search-mcp"]
        assert [mcp.name for mcp in workspace.mcps] == ["search-mcp"]
        assert workspace.mcps[0].mcp_config.url == "https://new.example.com/mcp"
        assert result.mcps_added == 1

    asyncio.run(run())


def test_binding_resolver_rolls_back_same_name_mcp_update_failure() -> None:
    """Failed same-name MCP replacement should restore the old runtime MCP."""
    async def run() -> None:
        resolver = AgentBindingResolver()
        workspace = _FakeWorkspace()
        old_mcp = type("Mcp", (), {"name": "search-mcp"})()
        workspace.mcps = [old_mcp]
        before = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    binding_id="mcp-1",
                    name="search-mcp",
                    endpoint_url="https://old.example.com/mcp",
                ),
            ],
        )
        after = _agent_with_defaults(
            mcp_bindings=[
                AgentMcpBinding(
                    binding_id="mcp-1",
                    name="search-mcp",
                    endpoint_url="https://new.example.com/mcp",
                ),
            ],
        )
        workspace.fail_add_mcp = True

        result = await resolver.reconcile_agent_bindings(
            user_id="user-1",
            before_agent_record=before,
            after_agent_record=after,
            workspace=workspace,
        )

        assert result.errors
        assert workspace.mcps == [old_mcp]

    asyncio.run(run())


def test_update_agent_reconciles_agent_workspace() -> None:
    """PATCH Agent should sync the Agent workspace, independent of sessions."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        agent_workspace_service = _FakeAgentWorkspaceService(workspace)
        storage = _FakeStorage()
        storage.sessions = [
            SessionRecord(
                id="session-1",
                user_id="user-1",
                agent_id="agent-1",
                config=SessionConfig(workspace_id="session-workspace"),
            ),
        ]
        before = _agent_with_defaults(
            id="agent-1",
            skill_bindings=[
                AgentSkillBinding(
                    source="custom",
                    slug="old-skill",
                    display_name="old-skill",
                    config_json={"skill_path": "old"},
                ),
            ],
        )
        resolver = _RecordingResolver()

        response = await update_agent(
            agent_id="agent-1",
            body=UpdateAgentRequest(
                skill_bindings=[
                    AgentSkillBinding(
                        source="custom",
                        slug="new-skill",
                        display_name="new-skill",
                        config_json={"skill_path": "new"},
                    ),
                ],
            ),
            x_skillhub_token="user-token",
            user_id="user-1",
            storage=storage,
            access=_FakeAccess(before),
            agent_workspace_service=agent_workspace_service,
            binding_resolver=resolver,
        )

        assert response.data.skill_bindings[0].slug == "new-skill"
        assert storage.upserted_agent is not None
        assert agent_workspace_service.prepare_calls == [
            ("user-1", storage.upserted_agent),
        ]
        assert len(resolver.reconcile_calls) == 1
        call = resolver.reconcile_calls[0]
        assert call[0] == "user-1"
        assert call[1].data.skill_bindings[0].slug == "old-skill"
        assert call[2].data.skill_bindings[0].slug == "new-skill"
        assert call[3] is workspace
        assert call[4] == "user-token"

    asyncio.run(run())


def test_update_agent_reports_binding_reconcile_errors() -> None:
    """PATCH Agent should not silently save failed binding reconciliation."""
    async def run() -> None:
        workspace = _FakeWorkspace()
        storage = _FakeStorage()
        before = _agent_with_defaults(id="agent-1")

        try:
            await update_agent(
                agent_id="agent-1",
                body=UpdateAgentRequest(
                    skill_bindings=[
                        AgentSkillBinding(
                            source="custom",
                            slug="new-skill",
                            display_name="new-skill",
                            config_json={"skill_path": "new"},
                        ),
                    ],
                ),
                x_skillhub_token=None,
                user_id="user-1",
                storage=storage,
                access=_FakeAccess(before),
                agent_workspace_service=_FakeAgentWorkspaceService(workspace),
                binding_resolver=_FailingResolver(),
            )
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 502
            assert "remove failed" in str(getattr(exc, "detail", ""))
        else:
            raise AssertionError("Expected binding reconcile failure.")

        assert storage.upserted_agent is None

    asyncio.run(run())
