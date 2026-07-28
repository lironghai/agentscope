# -*- coding: utf-8 -*-
"""Schemas for the SkillHub global catalog proxy."""

from typing import Any

from pydantic import BaseModel, Field


class SkillHubCatalogResponse(BaseModel):
    """Generic paged catalog response returned to the Web UI."""

    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Catalog items from SkillHub.",
    )
    total: int = Field(default=0, description="Total result count.")
    page: int = Field(default=0, description="Page number.")
    size: int = Field(default=0, description="Page size.")
    facets: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Optional SkillHub facet values.",
    )


class SkillHubCatalogConfigResponse(BaseModel):
    """SkillHub catalog availability state."""

    enabled: bool = Field(description="Whether SkillHub catalog is enabled.")
