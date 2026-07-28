# -*- coding: utf-8 -*-
"""Read-only global SkillHub catalog router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from ._schema import SkillHubCatalogConfigResponse, SkillHubCatalogResponse
from .._service._skillhub_catalog import (
    SkillHubCatalogClient,
    SkillHubCatalogError,
)


skillhub_catalog_router = APIRouter(
    prefix="/global",
    tags=["global_catalog"],
    responses={404: {"description": "Not found"}},
)


def _get_client(request: Request) -> SkillHubCatalogClient:
    """Return the configured SkillHub catalog client."""
    client = getattr(request.app.state, "skillhub_catalog_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "SkillHub catalog is disabled; configure "
                "SKILLHUB_BASE_URL to enable it."
            ),
        )
    return client


def _append(
    params: list[tuple[str, str]],
    key: str,
    value: str | int | None,
) -> None:
    """Append a query parameter if the value is present."""
    if value is not None:
        params.append((key, str(value)))


def _append_many(
    params: list[tuple[str, str]],
    key: str,
    values: list[str] | None,
) -> None:
    """Append repeated query parameters."""
    if values:
        for value in values:
            params.append((key, value))


def _normalize_token(token: str | None) -> str | None:
    """Return a non-empty token value."""
    if token is None:
        return None
    normalized = token.strip()
    return normalized or None


def _normalize_catalog_response(data: dict[str, Any]) -> SkillHubCatalogResponse:
    """Normalize SkillHub data while preserving original item fields."""
    facets: dict[str, list[str]] = {}
    for key in ("categories", "authTypes", "providers", "tags"):
        value = data.get(key)
        if isinstance(value, list):
            facets[key] = [str(item) for item in value]

    items = data.get("items", [])
    if not isinstance(items, list):
        items = []

    return SkillHubCatalogResponse(
        items=[item for item in items if isinstance(item, dict)],
        total=int(data.get("total", len(items)) or 0),
        page=int(data.get("page", 0) or 0),
        size=int(data.get("size", len(items)) or 0),
        facets=facets,
    )


async def _proxy(call) -> SkillHubCatalogResponse:
    """Run a SkillHub call and map failures to HTTP responses."""
    try:
        return _normalize_catalog_response(await call())
    except SkillHubCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SkillHub request failed: {exc}",
        ) from exc


@skillhub_catalog_router.get(
    "/skillhub",
    response_model=SkillHubCatalogConfigResponse,
    summary="Return SkillHub catalog availability",
)
async def skillhub_config(request: Request) -> SkillHubCatalogConfigResponse:
    """Return whether the SkillHub catalog client is configured."""
    return SkillHubCatalogConfigResponse(
        enabled=getattr(request.app.state, "skillhub_catalog_client", None)
        is not None,
    )


@skillhub_catalog_router.get(
    "/skills",
    response_model=SkillHubCatalogResponse,
    summary="List SkillHub skills",
)
async def list_global_skills(
    request: Request,
    x_skillhub_token: str | None = Header(default=None),
    q: str | None = None,
    namespace: str | None = None,
    label: list[str] | None = Query(default=None),
    sort: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> SkillHubCatalogResponse:
    """Proxy SkillHub skill search."""
    client = _get_client(request)
    params: list[tuple[str, str]] = []
    _append(params, "q", q)
    _append(params, "namespace", namespace)
    _append_many(params, "label", label)
    _append(params, "sort", sort)
    _append(params, "page", page)
    _append(params, "size", size)
    return await _proxy(
        lambda: client.list_skills(params, _normalize_token(x_skillhub_token)),
    )


@skillhub_catalog_router.get(
    "/mcps/servers",
    response_model=SkillHubCatalogResponse,
    summary="List SkillHub MCP catalog servers",
)
async def list_global_mcp_servers(
    request: Request,
    x_skillhub_token: str | None = Header(default=None),
    search: str | None = None,
    category: str | None = None,
    auth_type: str | None = None,
    provider: str | None = None,
    tags: list[str] | None = Query(default=None),
    page: int | None = None,
    size: int | None = None,
) -> SkillHubCatalogResponse:
    """Proxy SkillHub MCP catalog search."""
    client = _get_client(request)
    params: list[tuple[str, str]] = []
    _append(params, "search", search)
    _append(params, "category", category)
    _append(params, "auth_type", auth_type)
    _append(params, "provider", provider)
    _append_many(params, "tags", tags)
    _append(params, "page", page)
    _append(params, "size", size)
    return await _proxy(
        lambda: client.list_mcp_servers(
            params,
            _normalize_token(x_skillhub_token),
        ),
    )


@skillhub_catalog_router.get(
    "/mcps/internal-servers",
    response_model=SkillHubCatalogResponse,
    summary="List SkillHub internal MCP servers",
)
async def list_global_internal_mcp_servers(
    request: Request,
    x_skillhub_token: str | None = Header(default=None),
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> SkillHubCatalogResponse:
    """Proxy SkillHub internal MCP server search."""
    client = _get_client(request)
    params: list[tuple[str, str]] = []
    _append(params, "search", search)
    _append(params, "page", page)
    _append(params, "size", size)
    return await _proxy(
        lambda: client.list_internal_mcp_servers(
            params,
            _normalize_token(x_skillhub_token),
        ),
    )
