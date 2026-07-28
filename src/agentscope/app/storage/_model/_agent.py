# -*- coding: utf-8 -*-
"""The agent storage class."""
from datetime import datetime, timezone
from typing import Any, Literal, Self

from pydantic import Field, BaseModel, model_validator
from pydantic.json_schema import SkipJsonSchema

from ...._utils._common import _generate_id
from ._base import _RecordBase
from ....agent import ContextConfig, ReActConfig


class InviteConfig(BaseModel):
    """User-editable invite settings for :class:`AgentData`.

    Kept in its own sub-model so the frontend's schema-driven form —
    which renders any nested-object property as its own fieldset —
    picks it up as a dedicated section without a per-field allowlist.
    Also keeps the cross-field ``invitable ⇒ non-empty description``
    invariant local to this model.
    """

    invitable: bool = Field(
        default=False,
        description=(
            "Whether this agent may be borrowed into another agent's team "
            "via the ``AgentInvite`` tool. Independent from "
            ":attr:`invite_description` so the user can preserve an "
            "authored blurb while temporarily disabling the toggle. "
            "``invitable=True`` requires a non-empty "
            ":attr:`invite_description` (enforced by validator)."
        ),
        title="Invitable",
    )

    invite_description: str | None = Field(
        default=None,
        description=(
            "Free-text blurb shown to a leader LLM in the ``AgentInvite`` "
            "tool description — used by the leader to decide whether to "
            "borrow this agent. Persisted across toggle off/on so the "
            "user's authored draft is not lost when :attr:`invitable` "
            "is temporarily disabled."
        ),
        title="Invite Description",
        json_schema_extra={"format": "textarea"},
    )

    @model_validator(mode="after")
    def _check_invitable_has_description(self) -> Self:
        """Reject ``invitable=True`` without a non-empty description.

        The blurb is what the leader LLM sees when it inspects the
        ``AgentInvite`` tool; without it, the LLM cannot make a sensible
        choice. Rejecting at the model boundary (rather than in the
        service layer) means PATCH / POST return HTTP 422 automatically.
        """
        if self.invitable and not (self.invite_description or "").strip():
            raise ValueError(
                "invite_description must be non-empty when invitable=True",
            )
        return self


class ShareConfig(BaseModel):
    """User-editable sharing settings for :class:`AgentData`."""

    shared: bool = Field(
        default=False,
        description=(
            "Whether this agent is visible to other users as a read-only "
            "shared agent."
        ),
        title="Shared",
    )


AgentShareTier = Literal["read", "clone", "run", "edit"]
AgentShareGranteeType = Literal["user", "workspace"]


class AgentShareGrant(BaseModel):
    """Explicit ACL grant for a shared Agent.

    The legacy :class:`ShareConfig` toggle remains for compatibility.
    New share grants make the viewer and the allowed action explicit.
    """

    grantee_type: AgentShareGranteeType = Field(
        description="Grant target type, currently user or workspace.",
    )
    grantee_id: str = Field(
        description=(
            "Target user id, or '*' for a workspace-wide compatibility grant."
        ),
    )
    tier: AgentShareTier = Field(
        default="read",
        description="Granted Agent access tier: read, clone, run, or edit.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Grant creation timestamp.",
    )
    created_by: str | None = Field(
        default=None,
        description="User id that created this grant.",
    )

    def matches_viewer(self, viewer_id: str) -> bool:
        """Return whether this grant applies to ``viewer_id``."""
        if self.grantee_type == "user":
            return self.grantee_id == viewer_id
        return self.grantee_id == "*"


_AGENT_SHARE_TIER_RANK: dict[str, int] = {
    "read": 0,
    "clone": 1,
    "run": 2,
    "edit": 3,
}


def effective_agent_share_tier(
    data: "AgentData",
    viewer_id: str,
) -> AgentShareTier | None:
    """Return the highest non-owner share tier applying to ``viewer_id``."""
    tier: AgentShareTier | None = "read" if data.share_config.shared else None
    for grant in data.share_grants:
        if not grant.matches_viewer(viewer_id):
            continue
        if tier is None or (
            _AGENT_SHARE_TIER_RANK[grant.tier]
            > _AGENT_SHARE_TIER_RANK[tier]
        ):
            tier = grant.tier
    return tier


class AgentToolConfig(BaseModel):
    """Agent-level built-in tool exposure configuration.

    This model controls which workspace/built-in tools are exposed to the
    agent runtime. It is intentionally separate from permission approval
    state, which is evaluated when a tool call is made.
    """

    allow: list[str] = Field(
        default_factory=list,
        description=(
            "Optional allow-list of built-in tool names exposed to the Agent. "
            "Empty means no explicit allow-list is applied."
        ),
        title="Allowed Tools",
    )
    deny: list[str] = Field(
        default_factory=list,
        description=(
            "Built-in tool names hidden from the Agent. Deny entries take "
            "precedence over allow entries."
        ),
        title="Denied Tools",
    )
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional Agent-level default metadata projected to tools.json. "
            "Runtime adapter application is intentionally not enabled yet."
        ),
        title="Tool Defaults",
    )


class AgentSkillBinding(BaseModel):
    """A SkillHub Skill selected for an Agent."""

    binding_id: str = Field(
        default_factory=_generate_id,
        description="Stable binding id scoped to one agent.",
    )
    source: Literal["skillhub", "custom"] = Field(
        default="skillhub",
        description="Where this skill binding comes from.",
    )
    skillhub_skill_id: str | None = Field(
        default=None,
        description="SkillHub skill identifier when source is skillhub.",
    )
    namespace: str = Field(
        default="",
        description="SkillHub namespace or owner scope.",
    )
    slug: str = Field(
        default="",
        description="SkillHub skill slug.",
    )
    version: str | None = Field(
        default=None,
        description="Pinned or displayed SkillHub version.",
    )
    display_name: str | None = Field(
        default=None,
        description="Display name snapshot for list/edit pages.",
    )
    description: str | None = Field(
        default=None,
        description="Description snapshot for list/edit pages.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this binding participates in runtime resolution.",
    )
    config_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent-local binding configuration.",
    )
    snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        description="SkillHub catalog snapshot captured when selected.",
    )


class AgentMcpBinding(BaseModel):
    """A SkillHub MCP server selected for an Agent."""

    binding_id: str = Field(
        default_factory=_generate_id,
        description="Stable binding id scoped to one agent.",
    )
    source: Literal["skillhub", "custom"] = Field(
        default="skillhub",
        description="Where this MCP binding comes from.",
    )
    skillhub_server_id: str | None = Field(
        default=None,
        description="SkillHub MCP server identifier when source is skillhub.",
    )
    name: str = Field(
        default="",
        description="MCP server name snapshot.",
    )
    provider: str | None = Field(
        default=None,
        description="MCP provider snapshot.",
    )
    auth_type: str | None = Field(
        default=None,
        description="MCP authentication type snapshot.",
    )
    transport: str | None = Field(
        default=None,
        description="MCP transport snapshot.",
    )
    endpoint_url: str | None = Field(
        default=None,
        description="MCP endpoint URL snapshot when exposed by SkillHub.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this binding participates in runtime resolution.",
    )
    config_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent-local binding configuration.",
    )
    snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        description="SkillHub catalog snapshot captured when selected.",
    )


class AgentData(BaseModel):
    """The agent data model."""

    id: SkipJsonSchema[str] = Field(
        description="Unique agent id",
        default_factory=_generate_id,
    )
    """The agent id.

    Server-assigned; never edited via the create / update form.
    Annotated with :class:`SkipJsonSchema` so it is dropped from
    ``AgentData.model_json_schema()`` (the frontend renders the form
    off that schema) while still being serialised in normal JSON
    dumps (so persisted records keep the id).
    """

    name: str = Field(
        description="The name of the agent.",
        title="Name",
    )

    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="The system prompt for the agent.",
        title="System Prompt",
        # Hint for schema-driven UI renderers; see ``ContextConfig`` for
        # the same pattern on long-form prompts.
        json_schema_extra={"format": "textarea"},
    )

    context_config: ContextConfig = Field(
        description="The context config for the agent.",
        title="Context Config",
    )

    react_config: ReActConfig = Field(
        description="The react config for the agent.",
        title="React Config",
    )

    invite_config: InviteConfig = Field(
        default_factory=InviteConfig,
        description="The invite config for the agent.",
        title="Invite Config",
    )

    share_config: ShareConfig = Field(
        default_factory=ShareConfig,
        description="The sharing config for the agent.",
        title="Share Config",
    )

    share_grants: list[AgentShareGrant] = Field(
        default_factory=list,
        description="Explicit cross-owner Agent access grants.",
        title="Share Grants",
    )

    tool_config: AgentToolConfig = Field(
        default_factory=AgentToolConfig,
        description="Built-in tool exposure configuration for this agent.",
        title="Tool Config",
    )

    skill_bindings: list[AgentSkillBinding] = Field(
        default_factory=list,
        description="SkillHub or custom skills selected for this agent.",
        title="Skill Bindings",
    )

    mcp_bindings: list[AgentMcpBinding] = Field(
        default_factory=list,
        description="SkillHub or custom MCP servers selected for this agent.",
        title="MCP Bindings",
    )

    @model_validator(mode="after")
    def _check_share_grants_are_unique(self) -> Self:
        """Reject duplicate grants for the same target."""
        seen: set[tuple[str, str]] = set()
        for grant in self.share_grants:
            key = (grant.grantee_type, grant.grantee_id)
            if key in seen:
                raise ValueError(
                    "share_grants must be unique by grantee_type and "
                    "grantee_id",
                )
            seen.add(key)
        return self


class AgentRecord(_RecordBase):
    """The agent ORM model."""

    user_id: str
    """The user id"""

    source: Literal["user", "team"] = "user"
    """How this agent was created.

    - ``"user"``: created directly by the user (default). Can have multiple
      sessions and is listed in the user's regular agent list.
    - ``"team"``: spawned as a team worker by another agent's
      ``create_team`` / ``team_add_member`` tool. Has exactly one session.
      Team membership itself is session-level and stored on
      :class:`SessionRecord.team_id`.
    """

    data: AgentData
    """The agent data"""
