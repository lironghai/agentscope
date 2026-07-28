# -*- coding: utf-8 -*-
"""Read-only SkillHub catalog client for global Skill/MCP browsing."""

from __future__ import annotations

from urllib.parse import quote
from typing import Any, Iterable

import httpx


class SkillHubCatalogError(RuntimeError):
    """Raised when SkillHub cannot serve a catalog request."""


class SkillHubCatalogClient:
    """Small HTTP client for SkillHub read-only catalog endpoints."""

    def __init__(
        self,
        base_url: str,
        service_token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialize the SkillHub client.

        Args:
            base_url (`str`):
                Base URL of the SkillHub service.
            service_token (`str | None`, optional):
                Backend-only bearer token used for SkillHub requests.
            timeout (`float`, optional):
                Request timeout in seconds.
            transport (`httpx.AsyncBaseTransport | None`, optional):
                Optional transport used by tests.
        """
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = timeout
        self.transport = transport

    async def list_skills(
        self,
        params: Iterable[tuple[str, str]],
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """List SkillHub skills."""
        return await self._get("/api/web/skills", params, token_override)

    async def list_mcp_servers(
        self,
        params: Iterable[tuple[str, str]],
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """List SkillHub MCP catalog servers."""
        return await self._get(
            "/api/web/mcp/servers",
            params,
            token_override,
        )

    async def list_internal_mcp_servers(
        self,
        params: Iterable[tuple[str, str]],
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """List SkillHub internal MCP servers."""
        return await self._get(
            "/api/web/mcp/internal-servers",
            params,
            token_override,
        )

    async def resolve_skill(
        self,
        namespace: str,
        slug: str,
        version: str | None = None,
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a SkillHub skill to a concrete downloadable version."""
        params = [("version", version)] if version else []
        return await self._get(
            (
                "/api/cli/v1/skills/"
                f"{quote(namespace, safe='')}/{quote(slug, safe='')}/resolve"
            ),
            params,
            token_override,
        )

    async def download_skill(
        self,
        namespace: str,
        slug: str,
        version: str | None = None,
        token_override: str | None = None,
    ) -> bytes:
        """Download a SkillHub skill package."""
        if version:
            path = (
                "/api/cli/v1/skills/"
                f"{quote(namespace, safe='')}/{quote(slug, safe='')}/"
                f"versions/{quote(version, safe='')}/download"
            )
        else:
            path = (
                "/api/cli/v1/skills/"
                f"{quote(namespace, safe='')}/{quote(slug, safe='')}/download"
            )
        return await self._get_bytes(path, token_override)

    async def _get(
        self,
        path: str,
        params: Iterable[tuple[str, str]],
        token_override: str | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request and unwrap SkillHub's response envelope."""
        headers: dict[str, str] = {}
        token = token_override or self.service_token
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    path,
                    params=list(params),
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise SkillHubCatalogError(str(exc)) from exc

        if response.status_code >= 400:
            raise SkillHubCatalogError(self._extract_error_detail(response))

        payload = response.json()
        if isinstance(payload, dict) and "data" in payload:
            data = payload["data"]
            if isinstance(data, dict):
                return data
            return {"items": data if isinstance(data, list) else []}
        if isinstance(payload, dict):
            return payload
        return {"items": []}

    async def _get_bytes(
        self,
        path: str,
        token_override: str | None = None,
    ) -> bytes:
        """Perform a GET request and return a binary body."""
        headers: dict[str, str] = {}
        token = token_override or self.service_token
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.get(path, headers=headers)
        except httpx.HTTPError as exc:
            raise SkillHubCatalogError(str(exc)) from exc

        if response.status_code >= 400:
            raise SkillHubCatalogError(self._extract_error_detail(response))
        return response.content

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        """Extract a readable error message from a SkillHub response."""
        try:
            payload = response.json()
        except ValueError:
            return response.text or response.reason_phrase

        if isinstance(payload, dict):
            for key in ("msg", "message", "detail", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        return response.reason_phrase
