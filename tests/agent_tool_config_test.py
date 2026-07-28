# -*- coding: utf-8 -*-
"""Tests for Agent-level tool and MCP configuration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import types

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
from agentscope.app._router._agent_tools import (
    UpdateAgentToolsRequest,
    get_agent_tools_config,
    list_agent_tool_catalog,
    update_agent_tools_config,
)
from agentscope.app._service import AgentView, AgentWorkspaceService
from agentscope.app._service._toolkit import _filter_tools_for_agent_config
from agentscope.app.storage import (
    AgentData,
    AgentMcpBinding,
    AgentRecord,
    AgentToolConfig,
)


class _FakeWorkspace:
    """Local workspace stub with a real host workdir."""

    def __init__(self, workdir: Path, workspace_id: str = "workspace-1") -> None:
        self.workdir = str(workdir)
        self.host_workdir = str(workdir)
        self.workspace_id = workspace_id


class _FakeWorkspaceManager:
    """Workspace manager stub returning the configured workspace."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    async def get_workspace(
        self,
        _user_id: str,
        _agent_id: str,
        _session_id: str,
        workspace_id: str,
    ) -> _FakeWorkspace:
        return _FakeWorkspace(self.workdir, workspace_id)


class _FakeAccess:
    """Access service stub returning one editable Agent."""

    def __init__(self, agent: AgentRecord, *, editable: bool = True) -> None:
        self.agent = agent
        self.editable = editable

    async def get_resource(
        self,
        _user_id: str,
        _kind,
        _resource_id: str,
    ) -> AgentView:
        return AgentView.model_validate(
            {**self.agent.model_dump(), "editable": self.editable},
        )

    async def resolve_for_edit(
        self,
        _user_id: str,
        _kind,
        _resource_id: str,
    ) -> tuple[str, AgentRecord]:
        return self.agent.user_id, self.agent


class _FakeStorage:
    """Storage stub recording the updated Agent."""

    def __init__(self) -> None:
        self.upserted: AgentRecord | None = None

    async def upsert_agent(self, _user_id: str, agent_record: AgentRecord) -> str:
        self.upserted = agent_record
        return agent_record.id


class _FailingWriteAgentWorkspaceService(AgentWorkspaceService):
    """Workspace service that fails during tools projection."""

    async def write_tools_config(self, *_args, **_kwargs) -> None:
        raise RuntimeError("tools write failed")


class _NoopBindingResolver:
    """Binding resolver stub for API tests without SkillHub calls."""

    def __init__(self) -> None:
        self.calls = []

    async def reconcile_agent_bindings(self, **kwargs):
        self.calls.append(kwargs)

        class _Result:
            errors: list[str] = []

        return _Result()


def _agent_record(**updates) -> AgentRecord:
    data = AgentData(
        name="Tools agent",
        system_prompt="Use configured tools.",
        context_config=ContextConfig(),
        react_config=ReActConfig(),
        **updates,
    )
    return AgentRecord(id="agent-1", user_id="owner-1", data=data)


def test_agent_tool_catalog_lists_configurable_builtin_tools() -> None:
    """The Agent form should discover configurable built-in tool names."""

    catalog = list_agent_tool_catalog()
    by_name = {item.name: item for item in catalog}

    assert {
        "Bash",
        "PowerShell",
        "Edit",
        "Glob",
        "Grep",
        "Read",
        "Write",
        "TaskCreate",
        "TaskList",
        "TaskGet",
        "TaskUpdate",
        "ToolStop",
        "ScheduleCreate",
        "ScheduleList",
        "ScheduleView",
        "ScheduleDelete",
        "TeamCreate",
        "AgentCreate",
        "AgentInvite",
        "TeamSay",
        "TeamDelete",
    }.issubset(by_name)
    assert by_name["Bash"].source == "Workspace"
    assert by_name["TaskCreate"].source == "Planning"
    assert by_name["TeamCreate"].source == "Team"
    assert all(item.description for item in catalog)
    assert "skillhub" not in {item.source.lower() for item in catalog}


def test_agent_workspace_projects_tool_config_and_mcp_bindings(
    tmp_path: Path,
) -> None:
    """Preparing an Agent workspace should write the Agent tools projection."""

    async def run() -> None:
        service = AgentWorkspaceService(_FakeWorkspaceManager(tmp_path))
        agent = _agent_record(
            tool_config=AgentToolConfig(
                allow=["read_file", "grep"],
                deny=["shell"],
                defaults={"timeout_seconds": 30},
            ),
            mcp_bindings=[
                AgentMcpBinding(
                    name="amap",
                    provider="Amap",
                    transport="streamable_http",
                    endpoint_url="https://mcp.example.com/amap",
                    config_json={
                        "headers": {"x-test": "1"},
                        "private_key": "secret",
                        "timeout": 10,
                        "unknown": "hidden",
                    },
                ),
                AgentMcpBinding(
                    name="disabled",
                    endpoint_url="https://mcp.example.com/disabled",
                    enabled=False,
                ),
            ],
        )

        await service.prepare_agent_workspace(
            user_id="owner-1",
            agent_record=agent,
        )

        tools = json.loads((tmp_path / "tools.json").read_text("utf-8"))
        assert tools["allow"] == ["read_file", "grep"]
        assert tools["deny"] == ["shell"]
        assert tools["defaults"] == {"timeout_seconds": 30}
        assert tools["mcpServers"] == {
            "amap": {
                "provider": "Amap",
                "timeout": 10,
                "transport": "streamable_http",
                "url": "https://mcp.example.com/amap",
            },
        }
        assert "private_key" not in tools["mcpServers"]["amap"]
        assert "unknown" not in tools["mcpServers"]["amap"]

    asyncio.run(run())


def test_update_agent_tools_config_persists_and_refreshes_workspace(
    tmp_path: Path,
) -> None:
    """The Agent tools API should persist config and update tools.json."""

    async def run() -> None:
        existing = _agent_record()
        access = _FakeAccess(existing)
        storage = _FakeStorage()
        service = AgentWorkspaceService(_FakeWorkspaceManager(tmp_path))
        resolver = _NoopBindingResolver()

        result = await update_agent_tools_config(
            agent_id="agent-1",
            body=UpdateAgentToolsRequest(
                tool_config=AgentToolConfig(allow=["read_file"], deny=["shell"]),
                mcp_bindings=[
                    AgentMcpBinding(
                        source="custom",
                        name="custom-db",
                        transport="stdio",
                        config_json={
                            "command": "node",
                            "args": ["server.js"],
                            "env": {"DB": "demo"},
                        },
                    ),
                ],
            ),
            user_id="viewer-1",
            storage=storage,
            access=access,
            agent_workspace_service=service,
            binding_resolver=resolver,
        )

        assert isinstance(result, AgentView)
        assert storage.upserted is not None
        assert storage.upserted.data.tool_config.allow == ["read_file"]
        assert storage.upserted.data.mcp_bindings[0].name == "custom-db"
        assert len(resolver.calls) == 1
        tools = json.loads((tmp_path / "tools.json").read_text("utf-8"))
        assert tools["allow"] == ["read_file"]
        assert tools["deny"] == ["shell"]
        assert tools["mcpServers"]["custom-db"]["command"] == "node"
        assert tools["mcpServers"]["custom-db"]["args"] == ["server.js"]
        assert "env" not in tools["mcpServers"]["custom-db"]

    asyncio.run(run())


def test_agent_tool_config_filters_tools_with_deny_precedence() -> None:
    """Agent tool config should filter exposed tools before toolkit creation."""

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    agent = _agent_record(
        tool_config=AgentToolConfig(
            allow=["read_file", "shell"],
            deny=["shell"],
        ),
    )

    filtered = _filter_tools_for_agent_config(
        [_Tool("Read"), _Tool("Bash"), _Tool("Write")],
        agent,
    )

    assert [tool.name for tool in filtered] == ["Read"]


def test_agent_tool_config_shell_alias_filters_powershell() -> None:
    """The legacy shell alias should also hide the Windows shell tool."""

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    agent = _agent_record(
        tool_config=AgentToolConfig(
            deny=["shell"],
        ),
    )

    filtered = _filter_tools_for_agent_config(
        [_Tool("PowerShell"), _Tool("Read")],
        agent,
    )

    assert [tool.name for tool in filtered] == ["Read"]


def test_get_agent_tools_config_masks_private_mcp_config_for_read_only_viewer(
    tmp_path: Path,
) -> None:
    """Read-only shared viewers should not receive raw MCP secrets."""

    async def run() -> None:
        agent = _agent_record(
            mcp_bindings=[
                AgentMcpBinding(
                    name="private-mcp",
                    endpoint_url="https://mcp.example.com/private",
                    config_json={
                        "headers": {"Authorization": "Bearer secret"},
                        "timeout": 10,
                    },
                    snapshot_json={"api_key": "secret"},
                ),
            ],
        )
        response = await get_agent_tools_config(
            agent_id="agent-1",
            user_id="viewer-1",
            access=_FakeAccess(agent, editable=False),
            agent_workspace_service=AgentWorkspaceService(
                _FakeWorkspaceManager(tmp_path),
            ),
        )

        assert response.mcp_bindings[0].config_json == {}
        assert response.mcp_bindings[0].snapshot_json == {}
        assert response.tools_json["mcpServers"] == {}

    asyncio.run(run())


def test_update_agent_tools_config_does_not_persist_when_tools_write_fails(
    tmp_path: Path,
) -> None:
    """A tools.json write failure should leave Agent storage unchanged."""

    async def run() -> None:
        existing = _agent_record()
        access = _FakeAccess(existing)
        storage = _FakeStorage()
        resolver = _NoopBindingResolver()
        service = _FailingWriteAgentWorkspaceService(
            _FakeWorkspaceManager(tmp_path),
        )

        try:
            await update_agent_tools_config(
                agent_id="agent-1",
                body=UpdateAgentToolsRequest(
                    tool_config=AgentToolConfig(allow=["Read"]),
                ),
                user_id="viewer-1",
                storage=storage,
                access=access,
                agent_workspace_service=service,
                binding_resolver=resolver,
            )
        except Exception as exc:  # pylint: disable=broad-except
            assert "tools write failed" in str(exc)
        else:
            raise AssertionError("Expected tools write failure.")

        assert storage.upserted is None

    asyncio.run(run())
