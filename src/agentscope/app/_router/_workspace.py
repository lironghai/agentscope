# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from ..deps import (
    get_agent_workspace_service,
    get_current_user_id,
    get_resource_access_service,
    get_storage,
    get_workspace_manager,
)
from ..access import ResourceKind
from .._service import (
    AgentWorkspaceListing,
    AgentWorkspaceService,
    ResourceAccessService,
)
from ..workspace_manager import WorkspaceManagerBase
from ..storage import AgentRecord, StorageBase
from ...mcp import MCPClient
from ...skill import Skill
from ...workspace import WorkspaceBase

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])
MAX_SKILL_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SKILL_ZIP_ENTRIES = 512
MAX_SKILL_ZIP_ENTRY_BYTES = 5 * 1024 * 1024
MAX_SKILL_ZIP_TOTAL_BYTES = 50 * 1024 * 1024


class AddSkillRequest(BaseModel):
    """The request to add skill."""

    skill_path: str


class ToolInfo(BaseModel):
    """The tool info."""

    name: str
    description: str | None = None


class MCPClientStatus(MCPClient):
    """MCPClient enriched with live tool list and health status."""

    is_healthy: bool = False
    tools: list[ToolInfo] = Field(default_factory=list)


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str | None,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    access: ResourceAccessService | None = None,
    agent_workspace_service: AgentWorkspaceService | None = None,
    require_edit: bool = False,
) -> WorkspaceBase:
    """Resolve a session workspace or the sessionless Agent workspace."""
    if not session_id:
        if access is None or agent_workspace_service is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Agent workspace dependencies are not configured.",
            )
        agent_view = await access.get_resource(
            user_id,
            ResourceKind.AGENT,
            agent_id,
        )
        if require_edit and not agent_view.editable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent workspace is read-only for this user.",
            )
        workspace = await agent_workspace_service.get_agent_workspace(
            user_id=agent_view.user_id,
            agent_id=agent_id,
        )
        if agent_view.editable:
            agent_record = AgentRecord.model_validate(
                agent_view.model_dump(exclude={"editable"}),
            )
            agent_workspace_service.scaffold_workspace(workspace, agent_record)
            await agent_workspace_service.write_tools_config(
                workspace,
                agent_record,
            )
        return workspace

    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    if require_edit:
        if access is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Agent access dependencies are not configured.",
            )
        agent_view = await access.get_resource(
            user_id,
            ResourceKind.AGENT,
            agent_id,
        )
        if not agent_view.editable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent workspace is read-only for this user.",
            )
    workspace_owner_id = session_record.config.workspace_owner_id or user_id
    return await workspace_manager.get_workspace(
        workspace_owner_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )


@workspace_router.get("/agent", response_model=AgentWorkspaceListing)
async def get_agent_workspace_view(
    agent_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> AgentWorkspaceListing:
    """Return the Agent workspace file list without requiring a session."""
    agent_view = await access.get_resource(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    workspace = await agent_workspace_service.get_agent_workspace(
        user_id=agent_view.user_id,
        agent_id=agent_id,
    )
    if agent_view.editable:
        agent_workspace_service.scaffold_workspace(
            workspace,
            AgentRecord.model_validate(
                agent_view.model_dump(exclude={"editable"}),
            ),
        )
        await agent_workspace_service.write_tools_config(
            workspace,
            AgentRecord.model_validate(
                agent_view.model_dump(exclude={"editable"}),
            ),
        )
    return AgentWorkspaceListing(
        agent_id=agent_id,
        workspace_id=workspace.workspace_id,
        workdir=workspace.workdir,
        files=agent_workspace_service.list_agent_workspace_files(
            workspace=workspace,
        ),
    )


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/mcp")
async def list_mcps(
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
    )
    clients = await workspace.list_mcps()

    results = []
    for client in clients:
        base = client.model_dump()
        try:
            mcp_tools = await client.list_tools()
            tools = [
                ToolInfo(name=t.name, description=t.description)
                for t in mcp_tools
            ]
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=True,
                    tools=tools,
                ),
            )
        except Exception:
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=False,
                ),
            )

    return results


@workspace_router.post("/mcp", status_code=status.HTTP_201_CREATED)
async def add_mcp(
    mcp: MCPClient,
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> None:
    """Add an MCP client to the session's workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
        True,
    )
    await workspace.add_mcp(mcp)


@workspace_router.delete(
    "/mcp/{mcp_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_mcp(
    mcp_name: str,
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> None:
    """Remove an MCP client from the session's workspace by name."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
        True,
    )
    await workspace.remove_mcp(mcp_name)


# ---------------------------------------------------------------------------
# Skill endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/skill")
async def list_skills(
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
    )
    return await workspace.list_skills()


@workspace_router.post("/skill", status_code=status.HTTP_201_CREATED)
async def add_skill(
    body: AddSkillRequest,
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> None:
    """Add a skill to the session's workspace from the given path."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
        True,
    )
    await workspace.add_skill(body.skill_path)


@workspace_router.post("/skill/upload", status_code=status.HTTP_201_CREATED)
async def upload_skill(
    file: UploadFile = File(...),
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> None:
    """Upload a zip skill package into the session's workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
        True,
    )
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Skill upload must be a .zip file.",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="agentscope-skill-upload-"))
    try:
        content = await file.read(MAX_SKILL_UPLOAD_BYTES + 1)
        if len(content) > MAX_SKILL_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Skill upload is too large.",
            )
        _extract_zip_safely(content, temp_dir)
        skill_dir = _find_single_skill_dir(temp_dir)
        if skill_dir is None:
            raise ValueError("Uploaded package does not contain SKILL.md.")
        await workspace.add_skill(str(skill_dir))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    finally:
        await file.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


@workspace_router.delete(
    "/skill/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_skill(
    skill_name: str,
    agent_id: str = Query(...),
    session_id: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> None:
    """Remove a skill from the session's workspace by name."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        access,
        agent_workspace_service,
        True,
    )
    await workspace.remove_skill(skill_name)


def _extract_zip_safely(archive: bytes, target_dir: Path) -> None:
    """Extract a zip archive without allowing path traversal."""
    root = target_dir.resolve()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        entries = zf.infolist()
        if len(entries) > MAX_SKILL_ZIP_ENTRIES:
            raise ValueError("Skill upload contains too many files.")
        total_size = 0
        for info in entries:
            target = (root / info.filename).resolve()
            if root not in (target, *target.parents):
                raise ValueError(f"Unsafe zip entry path: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if info.file_size > MAX_SKILL_ZIP_ENTRY_BYTES:
                raise ValueError(f"Skill upload file is too large: {info.filename}")
            total_size += info.file_size
            if total_size > MAX_SKILL_ZIP_TOTAL_BYTES:
                raise ValueError("Skill upload expands to too much data.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _find_single_skill_dir(root: Path) -> Path | None:
    """Return the only directory containing SKILL.md under ``root``."""
    matches = list(root.rglob("SKILL.md"))
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("Uploaded package contains multiple SKILL.md files.")
    return matches[0].parent
