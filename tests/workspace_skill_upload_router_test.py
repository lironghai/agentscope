# -*- coding: utf-8 -*-
"""Tests for uploading skill packages into a workspace."""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from agentscope.app._router._workspace import upload_skill
from agentscope.app.storage import SessionConfig, SessionRecord


class _FakeAccess:
    """Access stub with configurable edit permission."""

    def __init__(self, *, editable: bool = True) -> None:
        self.editable = editable

    async def get_resource(self, *_args):
        """Return an object carrying the edit flag used by the router."""
        return type("_AgentView", (), {"editable": self.editable})()


class _FakeWorkspace:
    """Workspace stub that records added skill directories."""

    def __init__(self) -> None:
        self.skill_paths: list[str] = []

    async def add_skill(self, skill_path: str) -> None:
        """Record the uploaded skill path."""
        assert Path(skill_path, "SKILL.md").is_file()
        assert Path(skill_path, "scripts", "run.py").is_file()
        assert Path(skill_path, "references", "guide.md").is_file()
        self.skill_paths.append(skill_path)


class _FakeStorage:
    """Storage stub with one session."""

    def __init__(self, workspace_owner_id: str | None = None) -> None:
        self.workspace_owner_id = workspace_owner_id

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a session record for the requested ids."""
        return SessionRecord(
            id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            config=SessionConfig(
                workspace_id="workspace-1",
                workspace_owner_id=self.workspace_owner_id,
            ),
        )


class _FakeWorkspaceManager:
    """Workspace manager stub returning a fake workspace."""

    def __init__(self, workspace: _FakeWorkspace) -> None:
        self.workspace = workspace
        self.calls: list[tuple] = []

    async def get_workspace(self, *_args) -> _FakeWorkspace:
        """Return the fake workspace."""
        self.calls.append(_args)
        return self.workspace


def _upload_file(entries: dict[str, str]) -> UploadFile:
    """Build an uploaded zip file from text entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    buf.seek(0)
    return UploadFile(filename="skill.zip", file=buf)


def test_upload_skill_adds_zip_package_to_workspace() -> None:
    """Uploading a zip skill package should call workspace.add_skill."""

    async def run() -> None:
        workspace = _FakeWorkspace()
        await upload_skill(
            file=_upload_file(
                {
                    "demo/SKILL.md": (
                        "---\nname: demo\ndescription: Demo skill\n---\n"
                        "Use this skill."
                    ),
                    "demo/scripts/run.py": "print('ok')\n",
                    "demo/references/guide.md": "# Guide\n",
                },
            ),
            agent_id="agent-1",
            session_id="session-1",
            user_id="user-1",
            storage=_FakeStorage(),
            workspace_manager=_FakeWorkspaceManager(workspace),
            access=_FakeAccess(),
        )

        assert len(workspace.skill_paths) == 1

    asyncio.run(run())


def test_upload_skill_uses_session_workspace_owner() -> None:
    """Legacy session workspace endpoints should reopen owner workspaces."""

    async def run() -> None:
        workspace = _FakeWorkspace()
        workspace_manager = _FakeWorkspaceManager(workspace)
        await upload_skill(
            file=_upload_file(
                {
                    "demo/SKILL.md": (
                        "---\nname: demo\ndescription: Demo skill\n---\n"
                        "Use this skill."
                    ),
                    "demo/scripts/run.py": "print('ok')\n",
                    "demo/references/guide.md": "# Guide\n",
                },
            ),
            agent_id="agent-1",
            session_id="session-1",
            user_id="viewer-1",
            storage=_FakeStorage(workspace_owner_id="owner-1"),
            workspace_manager=workspace_manager,
            access=_FakeAccess(),
        )

        assert workspace_manager.calls == [
            ("owner-1", "agent-1", "session-1", "workspace-1"),
        ]

    asyncio.run(run())


def test_upload_skill_rejects_read_only_session_workspace() -> None:
    """Read-only shared sessions must not upload into the owner workspace."""

    async def run() -> None:
        workspace = _FakeWorkspace()
        with pytest.raises(HTTPException) as exc:
            await upload_skill(
                file=_upload_file(
                    {
                        "demo/SKILL.md": "---\nname: demo\n---\n",
                        "demo/scripts/run.py": "print('ok')\n",
                        "demo/references/guide.md": "# Guide\n",
                    },
                ),
                agent_id="agent-1",
                session_id="session-1",
                user_id="viewer-1",
                storage=_FakeStorage(workspace_owner_id="owner-1"),
                workspace_manager=_FakeWorkspaceManager(workspace),
                access=_FakeAccess(editable=False),
            )

        assert exc.value.status_code == 403
        assert workspace.skill_paths == []

    asyncio.run(run())


def test_upload_skill_rejects_unsafe_zip_paths() -> None:
    """Uploaded zip packages must not escape the extraction directory."""

    async def run() -> None:
        workspace = _FakeWorkspace()
        with pytest.raises(HTTPException) as exc:
            await upload_skill(
                file=_upload_file({"../SKILL.md": "bad"}),
                agent_id="agent-1",
                session_id="session-1",
                user_id="user-1",
                storage=_FakeStorage(),
                workspace_manager=_FakeWorkspaceManager(workspace),
                access=_FakeAccess(),
            )

        assert exc.value.status_code == 400
        assert workspace.skill_paths == []

    asyncio.run(run())


def test_upload_skill_rejects_large_archives() -> None:
    """The upload endpoint should reject files above the configured cap."""

    async def run() -> None:
        workspace = _FakeWorkspace()
        oversized = UploadFile(
            filename="skill.zip",
            file=io.BytesIO(b"x" * (10 * 1024 * 1024 + 1)),
        )
        with pytest.raises(HTTPException) as exc:
            await upload_skill(
                file=oversized,
                agent_id="agent-1",
                session_id="session-1",
                user_id="user-1",
                storage=_FakeStorage(),
                workspace_manager=_FakeWorkspaceManager(workspace),
                access=_FakeAccess(),
            )

        assert exc.value.status_code == 413
        assert workspace.skill_paths == []

    asyncio.run(run())
