# -*- coding: utf-8 -*-
"""Request / response schemas for the subagent registry router."""
from pydantic import BaseModel, Field

from ...storage import SubagentRunRecord


class ListSubagentRunsResponse(BaseModel):
    """Response body for listing subagent registry rows."""

    runs: list[SubagentRunRecord] = Field(
        default_factory=list,
        description="Derived subagent run rows.",
    )
    total: int = Field(description="Number of returned rows.")
