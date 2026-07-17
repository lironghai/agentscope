# -*- coding: utf-8 -*-
"""Execution trace record for session diagnostics."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from ._base import _RecordBase


ExecutionTraceStatus = Literal[
    "running",
    "completed",
    "interrupted",
    "error",
]


class ExecutionTraceRecord(_RecordBase):
    """A coarse-grained trace for one backend chat execution."""

    user_id: str
    """The user id."""

    agent_id: str
    """The agent id."""

    session_id: str
    """The session id."""

    reply_id: str | None = None
    """The assistant reply id, once known."""

    input_message_id: str | None = None
    """The initiating input message id, when the run has one."""

    status: ExecutionTraceStatus = "running"
    """Execution status."""

    started_at: datetime | None = None
    """When execution started."""

    finished_at: datetime | None = None
    """When execution finished."""

    duration_ms: int | None = None
    """Total execution duration in milliseconds."""

    usage: dict[str, int] | None = None
    """Aggregated model token usage."""

    model: dict[str, Any] | None = None
    """Primary model summary."""

    fallback_model: dict[str, Any] | None = None
    """Fallback model summary."""

    error: dict[str, Any] | None = None
    """Bounded error summary when status is ``error``."""

    stages: list[dict[str, Any]] = Field(default_factory=list)
    """Coarse execution stages and durations."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    """Bounded summaries of emitted events."""

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Bounded summaries of tool calls observed during the run."""
