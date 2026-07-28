# -*- coding: utf-8 -*-
"""Derived subagent registry models."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SubagentRunStatus = Literal[
    "running",
    "idle",
    "awaiting_permission",
    "awaiting_external_result",
    "missing",
]


class SubagentRunRecord(BaseModel):
    """Read-only registry row for one team worker run.

    The current implementation is intentionally derived from Team and
    Session records instead of being persisted independently. Team members
    already carry child agent/session/role metadata, while the leader
    session points back to the parent Agent.
    """

    run_id: str = Field(description="Stable registry id for this child run.")
    owner_id: str = Field(description="Owner of the parent team.")
    team_id: str = Field(description="Team id that owns this subagent run.")
    team_name: str = Field(description="Display name of the team.")
    parent_agent_id: str | None = Field(
        default=None,
        description="Agent id of the leader session, if it still exists.",
    )
    parent_session_id: str = Field(description="Leader session id.")
    child_owner_id: str = Field(description="Owner of the child agent.")
    child_agent_id: str = Field(description="Child agent id.")
    child_agent_name: str | None = Field(
        default=None,
        description="Child agent display name, if the agent still exists.",
    )
    child_session_id: str = Field(description="Team-scoped child session id.")
    role: Literal["created", "invited"] = Field(
        description="How the child joined the team.",
    )
    status: SubagentRunStatus = Field(
        description="Derived child run/session status.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Best available creation timestamp.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Best available update timestamp.",
    )
    error: str | None = Field(
        default=None,
        description="Consistency error when the derived row is incomplete.",
    )
