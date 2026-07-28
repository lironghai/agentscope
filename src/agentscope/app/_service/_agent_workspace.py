# -*- coding: utf-8 -*-
"""Agent-level workspace resolution and scaffolding."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel

from ..storage import AgentRecord
from ..workspace_manager import WorkspaceManagerBase
from ...workspace import LocalWorkspace, WorkspaceBase

AGENT_WORKSPACE_SESSION_ID = "__agent__"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_PUBLIC_MCP_CONFIG_KEYS = {
    "args",
    "command",
    "cwd",
    "disable_tools",
    "enable_tools",
    "execution_timeout",
    "is_stateful",
    "timeout",
    "transport",
    "url",
}


class AgentWorkspaceFile(BaseModel):
    """A file or directory in an Agent workspace."""

    path: str
    is_dir: bool
    size: int | None = None


class AgentWorkspaceListing(BaseModel):
    """Sessionless Agent workspace listing."""

    agent_id: str
    workspace_id: str
    workdir: str
    files: list[AgentWorkspaceFile]


class AgentWorkspaceService:
    """Resolve and prepare the deterministic workspace for an Agent."""

    def __init__(self, workspace_manager: WorkspaceManagerBase) -> None:
        self._workspace_manager = workspace_manager

    def agent_workspace_id(self, *, user_id: str, agent_id: str) -> str:
        """Return the stable Agent workspace id for ``user_id``/``agent``."""
        return hashlib.blake2b(
            f"{user_id}::{agent_id}".encode("utf-8"),
            digest_size=8,
        ).hexdigest()

    async def get_agent_workspace(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> WorkspaceBase:
        """Return the initialized Agent workspace."""
        workspace_id = self.agent_workspace_id(
            user_id=user_id,
            agent_id=agent_id,
        )
        return await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            AGENT_WORKSPACE_SESSION_ID,
            workspace_id,
        )

    async def prepare_agent_workspace(
        self,
        *,
        user_id: str,
        agent_record: AgentRecord,
        sync_tools_config: bool = True,
    ) -> WorkspaceBase:
        """Return the Agent workspace and ensure the standard layout."""
        workspace = await self.get_agent_workspace(
            user_id=user_id,
            agent_id=agent_record.id,
        )
        self.scaffold_workspace(workspace, agent_record)
        if sync_tools_config:
            await self.write_tools_config(workspace, agent_record)
        return workspace

    def scaffold_workspace(
        self,
        workspace: WorkspaceBase,
        agent_record: AgentRecord,
    ) -> None:
        """Create standard Agent workspace files when using local storage."""
        root = self._local_workdir(workspace)
        if root is None:
            return
        root.mkdir(parents=True, exist_ok=True)
        for dirname in ("skills", "subagents", "memory"):
            (root / dirname).mkdir(parents=True, exist_ok=True)
        self._write_if_missing(
            root / "AGENTS.md",
            self._agents_md(agent_record),
        )
        self._write_if_missing(root / "memory" / ".gitkeep", "")

    def build_tools_config(self, agent_record: AgentRecord) -> dict:
        """Project Agent tool/MCP config into the workspace tools contract."""
        tool_config = agent_record.data.tool_config
        return {
            "allow": list(tool_config.allow),
            "deny": list(tool_config.deny),
            "defaults": dict(tool_config.defaults),
            "mcpServers": self._project_mcp_servers(agent_record),
        }

    async def write_tools_config(
        self,
        workspace: WorkspaceBase,
        agent_record: AgentRecord,
    ) -> None:
        """Write the managed Agent tools projection to ``tools.json``."""
        content = (
            json.dumps(
                self.build_tools_config(agent_record),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        root = self._local_workdir(workspace)
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            (root / "tools.json").write_text(content, encoding="utf-8")
            return
        try:
            backend = workspace.get_backend()
        except (AttributeError, RuntimeError):
            return
        await backend.write_file(
            backend.join_path(workspace.workdir, "tools.json"),
            content.encode("utf-8"),
        )

    def list_agent_workspace_files(
        self,
        *,
        workspace: WorkspaceBase,
        max_entries: int = 500,
    ) -> list[AgentWorkspaceFile]:
        """List files under a local Agent workspace."""
        root = self._local_workdir(workspace)
        if root is None or not root.exists():
            return []
        files: list[AgentWorkspaceFile] = []
        for path in sorted(root.rglob("*")):
            if len(files) >= max_entries:
                break
            rel = path.relative_to(root).as_posix()
            files.append(
                AgentWorkspaceFile(
                    path=rel,
                    is_dir=path.is_dir(),
                    size=path.stat().st_size if path.is_file() else None,
                ),
            )
        return files

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _agents_md(agent_record: AgentRecord) -> str:
        prompt = agent_record.data.system_prompt.strip()
        if not prompt:
            prompt = "You're a helpful assistant."
        return (
            f"# {agent_record.data.name}\n\n"
            "This workspace belongs to this Agent. Keep reusable skills, "
            "tools, subagents, and memory here.\n\n"
            "## System Prompt\n\n"
            f"{prompt}\n"
        )

    @classmethod
    def _project_mcp_servers(cls, agent_record: AgentRecord) -> dict[str, dict]:
        servers: dict[str, dict] = {}
        for binding in agent_record.data.mcp_bindings:
            if not binding.enabled:
                continue
            name = (
                binding.name
                or cls._safe_segment(
                    binding.skillhub_server_id or "skillhub_mcp",
                )
            )
            server: dict = {}
            if isinstance(binding.config_json, dict):
                server.update(cls._public_mcp_config(binding.config_json))
            if binding.endpoint_url and "url" not in server:
                server["url"] = binding.endpoint_url
            if binding.transport and "transport" not in server:
                server["transport"] = binding.transport
            if binding.provider and "provider" not in server:
                server["provider"] = binding.provider
            if binding.auth_type and "authType" not in server:
                server["authType"] = binding.auth_type
            if binding.skillhub_server_id and "skillhubServerId" not in server:
                server["skillhubServerId"] = binding.skillhub_server_id
            servers[name] = server
        return servers

    @staticmethod
    def _local_workdir(workspace: WorkspaceBase) -> Path | None:
        host_workdir = getattr(workspace, "host_workdir", None)
        if host_workdir:
            return Path(host_workdir)
        if isinstance(workspace, LocalWorkspace):
            return Path(workspace.workdir)
        return None

    @classmethod
    def _public_mcp_config(cls, value: dict) -> dict:
        """Return a tools.json-safe MCP projection using a strict allow-list."""
        safe: dict = {}
        for key, item in value.items():
            if key not in _PUBLIC_MCP_CONFIG_KEYS:
                continue
            if cls._is_public_config_value(item):
                safe[key] = item
        return safe

    @staticmethod
    def _is_public_config_value(value: object) -> bool:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return True
        if isinstance(value, list):
            return all(
                isinstance(item, (str, int, float, bool)) or item is None
                for item in value
            )
        return False

    @staticmethod
    def _safe_segment(value: str) -> str:
        """Return the same safe fallback name used for runtime MCPs."""
        return _SAFE_NAME_RE.sub("_", value).strip("._") or "item"
