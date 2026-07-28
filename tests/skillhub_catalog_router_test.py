# -*- coding: utf-8 -*-
"""Tests for the SkillHub global catalog proxy router."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router import skillhub_catalog_router
from agentscope.app._service._skillhub_catalog import SkillHubCatalogClient


def _app_with_client(client: SkillHubCatalogClient | None) -> FastAPI:
    """Build a minimal FastAPI app with the catalog router."""
    app = FastAPI()
    app.state.skillhub_catalog_client = client
    app.include_router(skillhub_catalog_router)
    return app


def _json_response(data: dict[str, Any]) -> httpx.Response:
    """Return a SkillHub-style API response."""
    return httpx.Response(
        200,
        json={
            "code": 0,
            "msg": "ok",
            "data": data,
            "timestamp": "2026-07-24T00:00:00Z",
            "requestId": "req_1",
        },
    )


def test_list_skills_forwards_query_and_service_token() -> None:
    """The skills endpoint proxies SkillHub query params server-side."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return _json_response(
            {
                "items": [
                    {
                        "id": 101,
                        "slug": "writer",
                        "displayName": "Writer",
                        "summary": "Draft content",
                        "namespace": "default",
                    },
                ],
                "total": 1,
                "page": 0,
                "size": 20,
            },
        )

    client = SkillHubCatalogClient(
        base_url="https://skillhub.example.com",
        service_token="secret-token",
        transport=httpx.MockTransport(handler),
    )
    app = _app_with_client(client)

    response = TestClient(app).get(
        "/global/skills",
        params={
            "q": "write",
            "namespace": "default",
            "label": "office",
            "sort": "newest",
            "page": "0",
            "size": "20",
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["displayName"] == "Writer"
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["url"] == (
        "https://skillhub.example.com/api/web/skills?"
        "q=write&namespace=default&label=office&sort=newest&page=0&size=20"
    )


def test_user_skillhub_token_overrides_service_token() -> None:
    """A per-user SkillHub token takes precedence over the service token."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return _json_response({"items": [], "total": 0, "page": 0, "size": 20})

    client = SkillHubCatalogClient(
        base_url="https://skillhub.example.com",
        service_token="service-token",
        transport=httpx.MockTransport(handler),
    )
    app = _app_with_client(client)

    response = TestClient(app).get(
        "/global/skills",
        headers={"X-SkillHub-Token": "user-token"},
    )

    assert response.status_code == 200
    assert seen["authorization"] == "Bearer user-token"


def test_list_mcp_servers_forwards_filters() -> None:
    """The MCP catalog endpoint preserves SkillHub catalog filters."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _json_response(
            {
                "items": [
                    {
                        "id": "browser",
                        "name": "Browser",
                        "provider": "playwright",
                        "authType": "none",
                        "tags": ["automation"],
                    },
                ],
                "total": 1,
                "page": 0,
                "size": 24,
                "categories": ["browser"],
                "authTypes": ["none"],
                "providers": ["playwright"],
                "tags": ["automation"],
            },
        )

    client = SkillHubCatalogClient(
        base_url="https://skillhub.example.com/",
        transport=httpx.MockTransport(handler),
    )
    app = _app_with_client(client)

    response = TestClient(app).get(
        "/global/mcps/servers",
        params={
            "search": "browser",
            "category": "browser",
            "auth_type": "none",
            "provider": "playwright",
            "tags": "automation",
            "page": "0",
            "size": "24",
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Browser"
    assert seen["url"] == (
        "https://skillhub.example.com/api/web/mcp/servers?"
        "search=browser&category=browser&auth_type=none&provider=playwright&"
        "tags=automation&page=0&size=24"
    )


def test_list_internal_mcp_servers_forwards_search_and_paging() -> None:
    """The internal MCP endpoint proxies only supported query params."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _json_response(
            {
                "items": [{"id": "internal-browser", "name": "Internal Browser"}],
                "total": 1,
                "page": 1,
                "size": 10,
            },
        )

    client = SkillHubCatalogClient(
        base_url="https://skillhub.example.com",
        transport=httpx.MockTransport(handler),
    )
    app = _app_with_client(client)

    response = TestClient(app).get(
        "/global/mcps/internal-servers",
        params={"search": "browser", "page": "1", "size": "10"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Internal Browser"
    assert seen["url"] == (
        "https://skillhub.example.com/api/web/mcp/internal-servers?"
        "search=browser&page=1&size=10"
    )


def test_missing_skillhub_config_returns_503() -> None:
    """A missing catalog client produces a clear disabled-state response."""
    app = _app_with_client(None)

    response = TestClient(app).get("/global/skills")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "SkillHub catalog is disabled; configure SKILLHUB_BASE_URL to enable it."
    )


def test_skillhub_upstream_error_maps_to_502() -> None:
    """SkillHub HTTP failures are surfaced as proxy errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"code": 401, "msg": "unauthorized", "data": None},
        )

    client = SkillHubCatalogClient(
        base_url="https://skillhub.example.com",
        service_token="bad-token",
        transport=httpx.MockTransport(handler),
    )
    app = _app_with_client(client)

    response = TestClient(app).get("/global/skills")

    assert response.status_code == 502
    assert response.json()["detail"] == "SkillHub request failed: unauthorized"
