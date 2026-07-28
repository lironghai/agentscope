# -*- coding: utf-8 -*-
"""Agent router — CRUD endpoints for agent configurations."""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError

from ...agent import ContextConfig, ReActConfig
from ..._logging import logger
from ..._utils._common import _flatten_json_schema
from ..access import ResourceKind
from ..deps import (
    get_agent_binding_resolver,
    get_agent_workspace_service,
    get_current_user_id,
    get_resource_access_service,
    get_session_service,
    get_storage,
)
from ._schema import (
    AgentSchemaResponse,
    AgentSchemaV2Response,
    AgentListData,
    AgentListView,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from .._service import (
    AgentBindingResolver,
    AgentWorkspaceService,
    AgentView,
    ResourceAccessService,
    SessionService,
)
from ..storage import StorageBase, AgentData, AgentRecord

agent_router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={404: {"description": "Not found"}},
)

def _agent_list_view(entry: AgentView) -> AgentListView:
    """Project a full agent view to list display fields."""
    return AgentListView(
        id=entry.id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        user_id=entry.user_id,
        source=entry.source,
        editable=entry.editable,
        data=AgentListData(
            id=entry.data.id,
            name=entry.data.name,
            invite_config=entry.data.invite_config,
            share_config=entry.data.share_config,
        ),
    )


def _skillhub_token_value(raw: str | None) -> str | None:
    """Normalize the optional SkillHub token header."""
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    return token or None


@agent_router.get(
    "/schema",
    response_model=AgentSchemaResponse,
    deprecated=True,
    summary="[Deprecated] Legacy sectioned schema — use /schema/v2",
)
async def get_agent_schema() -> AgentSchemaResponse:
    """Return the legacy sectioned JSON Schema fragments.

    .. deprecated::
        Superseded by :func:`get_agent_schema_v2`, which returns the
        full :class:`AgentData` schema in a single ``schema`` field.
        Kept for backwards compatibility with existing API consumers.
        New consumers should call ``GET /agent/schema/v2``.

    The frontend previously used three sections — identity, context
    config, and react config — so we return them as separate
    self-contained schemas rather than a single :class:`AgentData`
    schema with ``$ref`` s.

    Returns:
        `AgentSchemaResponse`:
            Schemas for the three form sections.
    """
    # Slice ``AgentData``'s schema down to the identity-relevant fields.
    # Going through ``AgentData.model_json_schema()`` (rather than building
    # a dict by hand) keeps Pydantic as the single source of truth for
    # defaults, titles, descriptions, and the ``format: textarea`` hint.
    agent_schema = AgentData.model_json_schema()
    identity_keys = ("name", "system_prompt")
    identity = {
        "type": "object",
        "title": "Identity",
        "properties": {
            k: v
            for k, v in agent_schema.get("properties", {}).items()
            if k in identity_keys
        },
        "required": [
            r for r in agent_schema.get("required", []) if r in identity_keys
        ],
    }

    context_schema = ContextConfig.model_json_schema()
    # ``summary_schema`` holds a Pydantic JSON Schema describing how the
    # compression model should structure its output. The end-user is not
    # expected to edit it from the form, so we hide it.
    context_schema.get("properties", {}).pop("summary_schema", None)

    return AgentSchemaResponse(
        identity=identity,
        context_config=context_schema,
        react_config=ReActConfig.model_json_schema(),
    )


@agent_router.get(
    "/schema/v2",
    response_model=AgentSchemaV2Response,
    summary="Full AgentData JSON Schema for the agent form",
)
async def get_agent_schema_v2() -> AgentSchemaV2Response:
    """Return the full :class:`AgentData` JSON Schema.

    Superset of the legacy sectioned endpoint. The response body is a
    single ``schema`` field carrying the whole Pydantic-generated
    schema of :class:`AgentData`, with two curated exclusions handled
    at the model layer (so no post-processing is needed here):

    - ``id``: server-assigned, marked :class:`SkipJsonSchema` on
      :attr:`AgentData.id`.
    - ``context_config.summary_schema``: internal structured-output
      spec for the compression model, dropped below since it is not
      user-editable and there is no equivalent hook on the Pydantic
      side.

    ``$ref`` inlining is delegated to
    :func:`~agentscope._utils._common._flatten_json_schema` so the
    frontend can render every property from the response body alone.

    The frontend derives its section grouping (identity / context /
    react / invite) directly from this schema — top-level scalar
    properties are the "identity" section, and top-level nested-object
    properties each become their own section. Adding a new
    user-editable field to :class:`AgentData` is thus enough to have it
    appear in the create / edit form without a router change.

    Returns:
        `AgentSchemaV2Response`:
            ``schema`` = the full :class:`AgentData` JSON Schema.
    """
    schema = _flatten_json_schema(AgentData.model_json_schema())
    # ``summary_schema`` is Pydantic's structured-output spec fed to the
    # compression model — internal, not user-editable. No pydantic-side
    # hook covers this deep nested field, so drop it after inlining.
    context_config = schema.get("properties", {}).get("context_config", {})
    context_config.get("properties", {}).pop("summary_schema", None)
    return AgentSchemaV2Response(schema=schema)


@agent_router.get(
    "/",
    response_model=ListAgentsResponse,
    summary="List all agents",
)
async def list_agents(
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListAgentsResponse:
    """Return all agent records visible to the authenticated user.

    Includes the caller's own ``source == "user"`` agents plus any agents
    shared to them through :class:`ResourceAccessPolicyBase`. Each entry
    carries an ``editable`` flag indicating whether the caller may
    PATCH/DELETE it.

    Args:
        user_id (`str`):
            Injected authenticated user ID.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ListAgentsResponse`:
            All visible agent records paired with per-viewer editability.
    """
    entries = await access.list_resource(user_id, ResourceKind.AGENT)
    agents = [_agent_list_view(entry) for entry in entries]
    return ListAgentsResponse(agents=agents, total=len(agents))


@agent_router.post(
    "/",
    response_model=CreateAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent",
)
async def create_agent(
    body: CreateAgentRequest,
    x_skillhub_token: str | None = Header(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
    binding_resolver: AgentBindingResolver = Depends(get_agent_binding_resolver),
) -> CreateAgentResponse:
    """Create and persist a new agent configuration.

    Args:
        body (`CreateAgentRequest`):
            Agent configuration to store.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.
        agent_workspace_service (`AgentWorkspaceService`):
            Injected service used to prepare the Agent workspace.
        binding_resolver (`AgentBindingResolver`):
            Injected resolver that materialises Agent Skill/MCP bindings.

    Returns:
        `CreateAgentResponse`:
            The server-assigned agent identifier.

    Raises:
        `HTTPException`: 422 if the request body passes
            :class:`CreateAgentRequest` validation but the resulting
            :class:`AgentData` fails its cross-field invariants (e.g.
            ``invite_config.invitable=True`` without a non-empty
            ``invite_description``). Symmetrical with
            :func:`update_agent`.
    """
    try:
        data = AgentData(
            name=body.name,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
            invite_config=body.invite_config,
            share_config=body.share_config,
            share_grants=body.share_grants,
            tool_config=body.tool_config,
            skill_bindings=body.skill_bindings,
            mcp_bindings=body.mcp_bindings,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    record = AgentRecord(user_id=user_id, data=data)
    try:
        workspace = await agent_workspace_service.prepare_agent_workspace(
            user_id=user_id,
            agent_record=record,
            sync_tools_config=False,
        )
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent workspace prepare failed: {exc}",
        ) from exc
    if record.data.skill_bindings or record.data.mcp_bindings:
        try:
            result = await binding_resolver.materialize_agent_bindings(
                user_id=user_id,
                agent_record=record,
                workspace=workspace,
                skillhub_token=_skillhub_token_value(x_skillhub_token),
            )
            if result.errors:
                logger.warning(
                    "Agent binding materialize for agent %s completed "
                    "with errors: %s",
                    record.id,
                    result.errors,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "message": "Agent binding materialize failed.",
                        "errors": result.errors,
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Agent binding materialize failed for agent %s: %s",
                record.id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Agent binding materialize failed: {exc}",
            ) from exc
    await agent_workspace_service.write_tools_config(workspace, record)
    agent_id = await storage.upsert_agent(user_id, record)
    return CreateAgentResponse(agent_id=agent_id)


@agent_router.get(
    "/{agent_id}",
    response_model=AgentView,
    summary="Get an agent",
)
async def get_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> AgentView:
    """Return one visible agent with full editable configuration."""
    return await access.get_resource(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )


@agent_router.patch(
    "/{agent_id}",
    response_model=AgentView,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    x_skillhub_token: str | None = Header(default=None),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    agent_workspace_service: AgentWorkspaceService = Depends(
        get_agent_workspace_service,
    ),
    binding_resolver: AgentBindingResolver = Depends(get_agent_binding_resolver),
) -> AgentView:
    """Partially update an existing agent configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        agent_id (`str`): The agent to update.
        body (`UpdateAgentRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.
        agent_workspace_service (`AgentWorkspaceService`):
            Injected service used to sync the Agent workspace.
        binding_resolver (`AgentBindingResolver`):
            Injected resolver that reconciles Agent Skill/MCP bindings.

    Returns:
        `AgentView`: The full agent record after the update.

    Raises:
        `HTTPException`: 404 if the agent is not visible to the caller;
            403 if visible but only readable.
    """
    owner_id, existing = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )

    updates = body.model_dump(exclude_none=True)
    # ``model_copy(update=...)`` skips validators; re-run
    # ``AgentData.model_validate`` on the merged shape so the
    # ``invite_config`` sub-model's ``invitable ⇒ non-empty description``
    # invariant enforced by ``@model_validator(mode="after")`` produces
    # an HTTP 422 instead of a stored-but-invalid record.
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
    if (
        "skill_bindings" in body.model_fields_set
        or "mcp_bindings" in body.model_fields_set
        or "tool_config" in body.model_fields_set
    ):
        try:
            workspace = await agent_workspace_service.prepare_agent_workspace(
                user_id=owner_id,
                agent_record=updated_agent,
                sync_tools_config=False,
            )
            if (
                "skill_bindings" in body.model_fields_set
                or "mcp_bindings" in body.model_fields_set
            ):
                result = await binding_resolver.reconcile_agent_bindings(
                    user_id=owner_id,
                    before_agent_record=existing,
                    after_agent_record=updated_agent,
                    workspace=workspace,
                    skillhub_token=_skillhub_token_value(x_skillhub_token),
                )
                if result.errors:
                    logger.warning(
                        "Agent binding reconcile for agent %s completed "
                        "with errors: %s",
                        agent_id,
                        result.errors,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail={
                            "message": "Agent binding reconcile failed.",
                            "errors": result.errors,
                        },
                    )
        except HTTPException:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Agent binding reconcile failed for agent %s: %s",
                agent_id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Agent binding reconcile failed: {exc}",
            ) from exc
    if (
        "skill_bindings" in body.model_fields_set
        or "mcp_bindings" in body.model_fields_set
        or "tool_config" in body.model_fields_set
    ):
        await agent_workspace_service.write_tools_config(
            workspace,
            updated_agent,
        )
    await storage.upsert_agent(owner_id, updated_agent)
    # Only reachable via ``resolve_for_edit``, so the caller has edit
    # permission by construction.
    return AgentView.model_validate(
        {**updated_agent.model_dump(), "editable": True},
    )


@agent_router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
)
async def delete_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> None:
    """Permanently delete an agent configuration.

    Cascades through every session owned by this agent (and, for team
    leaders, through every worker session) — cancelling any in-flight
    chat run, removing storage records, and purging bus state.

    Args:
        agent_id (`str`): The agent to delete.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.
        access (`ResourceAccessService`): Injected access service — used
            to resolve the owning user and enforce the edit permission
            when a shared editor deletes the agent.

    Raises:
        `HTTPException`: 404 if the agent is not visible to the caller;
            403 if visible but only readable.
    """
    owner_id, _ = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    deleted = await session_service.delete_agent(owner_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
