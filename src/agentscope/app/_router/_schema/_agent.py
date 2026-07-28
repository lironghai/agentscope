# -*- coding: utf-8 -*-
"""Request / response schemas for the agent router."""
import warnings
from datetime import datetime

from pydantic import BaseModel, Field

from ....agent import ContextConfig, ReActConfig
from ...storage import (
    AgentMcpBinding,
    AgentShareGrant,
    AgentSkillBinding,
    AgentToolConfig,
    InviteConfig,
    ShareConfig,
)
from ..._service import AgentView


class CreateAgentRequest(BaseModel):
    """Request body for creating a new agent."""

    name: str = Field(description="Display name of the agent.")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="Base system prompt fed to the agent.",
    )
    context_config: ContextConfig = Field(
        default_factory=ContextConfig,
        description="Context-window management configuration.",
    )
    react_config: ReActConfig = Field(
        default_factory=ReActConfig,
        description="ReAct loop configuration.",
    )
    invite_config: InviteConfig = Field(
        default_factory=InviteConfig,
        description=(
            "Invite-pool settings for this agent. See "
            ":class:`InviteConfig` 鈥?enforces the "
            "``invitable 鈬?non-empty description`` invariant."
        ),
    )
    share_config: ShareConfig = Field(
        default_factory=ShareConfig,
        description="Sharing settings for this agent.",
    )
    share_grants: list[AgentShareGrant] = Field(
        default_factory=list,
        description="Explicit cross-owner Agent access grants.",
    )
    tool_config: AgentToolConfig = Field(
        default_factory=AgentToolConfig,
        description="Built-in tool exposure configuration.",
    )
    skill_bindings: list[AgentSkillBinding] = Field(
        default_factory=list,
        description="SkillHub or custom skills selected for this agent.",
    )
    mcp_bindings: list[AgentMcpBinding] = Field(
        default_factory=list,
        description="SkillHub or custom MCP servers selected for this agent.",
    )


class CreateAgentResponse(BaseModel):
    """Response body after creating an agent."""

    agent_id: str = Field(description="Server-assigned agent identifier.")


class UpdateAgentRequest(BaseModel):
    """Request body for partially updating an agent.

    Omit any field to keep its current value.
    """

    name: str | None = Field(default=None, description="New display name.")
    system_prompt: str | None = Field(
        default=None,
        description="New system prompt.",
    )
    context_config: ContextConfig | None = Field(
        default=None,
        description="New context configuration.",
    )
    react_config: ReActConfig | None = Field(
        default=None,
        description="New ReAct loop configuration.",
    )
    invite_config: InviteConfig | None = Field(
        default=None,
        description=(
            "New invite-pool settings. Pass the full :class:`InviteConfig` "
            "object to update; omit to leave both invitable-related "
            "fields unchanged."
        ),
    )
    share_config: ShareConfig | None = Field(
        default=None,
        description="New sharing settings. Omit to leave sharing unchanged.",
    )
    share_grants: list[AgentShareGrant] | None = Field(
        default=None,
        description="Replacement explicit Agent access grant list.",
    )
    tool_config: AgentToolConfig | None = Field(
        default=None,
        description="New built-in tool exposure configuration.",
    )
    skill_bindings: list[AgentSkillBinding] | None = Field(
        default=None,
        description="Replacement Skill/MCP skill selection list.",
    )
    mcp_bindings: list[AgentMcpBinding] | None = Field(
        default=None,
        description="Replacement MCP server selection list.",
    )


class AgentListData(BaseModel):
    """Lightweight agent data needed by list views."""

    id: str = Field(description="Agent data identifier.")
    name: str = Field(description="Display name of the agent.")
    invite_config: InviteConfig = Field(
        default_factory=InviteConfig,
        description="Invite-pool display settings for this agent.",
    )
    share_config: ShareConfig = Field(
        default_factory=ShareConfig,
        description="Sharing display settings for this agent.",
    )


class AgentListView(BaseModel):
    """Viewer-relative lightweight agent list item."""

    id: str = Field(description="Server-assigned agent identifier.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")
    user_id: str = Field(description="Owner user id.")
    source: str = Field(description="Agent source, such as user or team.")
    data: AgentListData = Field(description="Display fields for this agent.")
    editable: bool = Field(
        description="Whether the current viewer may PATCH/DELETE this agent.",
    )


class ListAgentsResponse(BaseModel):
    """Response body for listing agents."""

    agents: list[AgentListView] = Field(description="Agent list records.")
    total: int = Field(description="Total number of agents.")


class AgentSchemaResponse(BaseModel):
    """**Deprecated.** JSON Schema fragments used by the frontend to
    render the agent create / edit forms.

    Superseded by :class:`AgentSchemaV2Response`, which returns the full
    :class:`AgentData` JSON Schema in a single ``schema`` field so newly
    added agent fields (like the ``invite_config`` sub-model) reach the
    frontend automatically without the router having to know about them.

    The frontend previously split :class:`AgentData` into three
    hand-picked sections (``identity``, ``context_config``,
    ``react_config``) here, which required a router edit every time a
    new user-editable field landed on :class:`AgentData`. Kept for
    backwards compatibility with pre-v2 API consumers.
    """

    identity: dict = Field(
        description=(
            "Schema for the agent's identity fields (``name``, "
            "``system_prompt``)."
        ),
    )
    context_config: dict = Field(
        description="Schema for ``ContextConfig``.",
    )
    react_config: dict = Field(
        description="Schema for ``ReActConfig``.",
    )


# The ``schema`` field name below is intentional 鈥?the wire contract for
# ``GET /agent/schema/v2`` is ``{"schema": ...}`` so the response is
# self-documenting. Pydantic v2's :meth:`BaseModel.schema` is a
# deprecated legacy classmethod (superseded by ``model_json_schema``);
# a like-named instance field triggers a cosmetic "shadows an attribute
# in parent BaseModel" warning that is irrelevant here because we never
# call the legacy classmethod. Suppress it locally instead of adding an
# alias that would obscure the wire contract at every call site.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r'Field name "schema" in "AgentSchemaV2Response"',
    )

    class AgentSchemaV2Response(BaseModel):
        """Response for ``GET /agent/schema/v2``.

        Wraps the full :class:`AgentData` JSON Schema in a single
        ``schema`` field so the frontend can render every user-editable
        property without the router having to enumerate them.
        """

        schema: dict = Field(
            description="The full JSON Schema of the AgentData model.",
        )
