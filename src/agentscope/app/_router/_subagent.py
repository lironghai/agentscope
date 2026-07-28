# -*- coding: utf-8 -*-
"""Subagent registry endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status

from ._schema import ListSubagentRunsResponse
from .._service import SubagentRegistryService
from ..deps import get_current_user_id, get_message_bus, get_storage
from ..message_bus import MessageBus
from ..storage import StorageBase, SubagentRunRecord


subagent_router = APIRouter(
    prefix="/subagents",
    tags=["subagents"],
    responses={404: {"description": "Not found"}},
)


@subagent_router.get(
    "/",
    response_model=ListSubagentRunsResponse,
    summary="List subagent runs",
)
async def list_subagent_runs(
    parent_agent_id: str | None = None,
    parent_session_id: str | None = None,
    team_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListSubagentRunsResponse:
    """List subagent/team-worker runs derived from team membership."""
    service = SubagentRegistryService(storage, message_bus)
    runs = await service.list_runs(
        user_id,
        parent_agent_id=parent_agent_id,
        parent_session_id=parent_session_id,
        team_id=team_id,
    )
    return ListSubagentRunsResponse(runs=runs, total=len(runs))


@subagent_router.get(
    "/{child_session_id}",
    response_model=SubagentRunRecord,
    summary="Get a subagent run",
)
async def get_subagent_run(
    child_session_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> SubagentRunRecord:
    """Get one subagent/team-worker run by child session id."""
    service = SubagentRegistryService(storage, message_bus)
    record = await service.get_run(user_id, child_session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subagent run '{child_session_id}' not found.",
        )
    return record
