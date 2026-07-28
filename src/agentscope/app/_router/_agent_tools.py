# -*- coding: utf-8 -*-
"""Agent-level tool and MCP configuration endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from ..access import ResourceKind
from ..deps import (
    get_agent_binding_resolver,
    get_agent_workspace_service,
    get_current_user_id,
    get_resource_access_service,
    get_storage,
)
from .._service import (
    AgentBindingResolver,
    AgentView,
    AgentWorkspaceService,
    ResourceAccessService,
)
from ..storage import (
    AgentData,
    AgentMcpBinding,
    AgentToolConfig,
    StorageBase,
)
from ._agent import _skillhub_token_value


agent_tools_router = APIRouter(
    prefix="/agent",
    tags=["agent-tools"],
    responses={404: {"description": "Not found"}},
)


class AgentToolsConfigResponse(BaseModel):
    """Agent tools config read response."""

    agent_id: str = Field(description="Agent identifier.")
    tool_config: AgentToolConfig = Field(
        description="Built-in tool exposure configuration.",
    )
    mcp_bindings: list[AgentMcpBinding] = Field(
        description="Agent MCP bindings.",
    )
    tools_json: dict = Field(
        description="Projected workspace tools.json payload.",
    )


class UpdateAgentToolsRequest(BaseModel):
    """Request body for replacing Agent tools config slices."""

    tool_config: AgentToolConfig | None = Field(
        default=None,
        description="Replacement built-in tool exposure configuration.",
    )
    mcp_bindings: list[AgentMcpBinding] | None = Field(
        default=None,
        description="Replacement MCP binding list.",
    )


@agent_tools_router.get(
    "/{agent_id}/tools/config",
    response_model=AgentToolsConfigResponse,
    summary="Get Agent tool/MCP configuration",
)
async def get_agent_tools_config(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
) -> AgentToolsConfigResponse:
    """Return the visible Agent's tool config and tools.json projection."""
    agent_view = await access.get_resource(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    return AgentToolsConfigResponse(
        agent_id=agent_view.id,
        tool_config=agent_view.data.tool_config,
        mcp_bindings=_visible_mcp_bindings(agent_view),
        tools_json=_visible_tools_json(agent_view, agent_workspace_service),
    )


@agent_tools_router.put(
    "/{agent_id}/tools/config",
    response_model=AgentView,
    summary="Update Agent tool/MCP configuration",
)
async def update_agent_tools_config(
    agent_id: str,
    body: UpdateAgentToolsRequest,
    x_skillhub_token: str | None = Header(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
    binding_resolver: AgentBindingResolver = Depends(get_agent_binding_resolver),
) -> AgentView:
    """Replace Agent tool config slices and refresh the Agent workspace."""
    owner_id, existing = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    updates = body.model_dump(exclude_none=True)
    try:
        updated_data = AgentData.model_validate(
            {**existing.data.model_dump(), **updates},
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    updated_agent = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()},
    )
    try:
        workspace = await agent_workspace_service.prepare_agent_workspace(
            user_id=owner_id,
            agent_record=updated_agent,
            sync_tools_config=False,
        )
        if "mcp_bindings" in body.model_fields_set:
            result = await binding_resolver.reconcile_agent_bindings(
                user_id=owner_id,
                before_agent_record=existing,
                after_agent_record=updated_agent,
                workspace=workspace,
                skillhub_token=_skillhub_token_value(x_skillhub_token),
            )
            if result.errors:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "message": "Agent MCP binding reconcile failed.",
                        "errors": result.errors,
                    },
                )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent tools config update failed: {exc}",
        ) from exc
    await agent_workspace_service.write_tools_config(workspace, updated_agent)
    await storage.upsert_agent(owner_id, updated_agent)
    return AgentView.model_validate(
        {**updated_agent.model_dump(), "editable": True},
    )


def _visible_mcp_bindings(agent_view: AgentView) -> list[AgentMcpBinding]:
    """Mask private MCP config for read-only shared Agent viewers."""
    if agent_view.editable:
        return agent_view.data.mcp_bindings
    return [
        binding.model_copy(
            update={
                "config_json": {},
                "snapshot_json": {},
            },
        )
        for binding in agent_view.data.mcp_bindings
    ]


def _visible_tools_json(
    agent_view: AgentView,
    agent_workspace_service: AgentWorkspaceService,
) -> dict:
    """Return a tools.json projection safe for the current viewer."""
    tools_json = agent_workspace_service.build_tools_config(agent_view)
    if agent_view.editable:
        return tools_json
    return {
        **tools_json,
        "mcpServers": {},
    }
