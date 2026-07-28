# -*- coding: utf-8 -*-
"""Materialise Agent-level Skill/MCP bindings into runtime workspaces."""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any

from ..._logging import logger
from ...mcp import HttpMCPConfig, MCPClient
from ...workspace import WorkspaceBase
from ..storage import AgentMcpBinding, AgentRecord, AgentSkillBinding
from ._skillhub_catalog import SkillHubCatalogClient


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_SKILL_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_SKILL_ZIP_ENTRIES = 512
MAX_SKILL_ZIP_ENTRY_BYTES = 5 * 1024 * 1024
MAX_SKILL_ZIP_TOTAL_BYTES = 50 * 1024 * 1024


@dataclass(slots=True)
class AgentBindingMaterializationResult:
    """Summary of one Agent binding materialisation attempt."""

    skills_added: int = 0
    mcps_added: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ResolvedAgentBindings:
    """Resolved bindings ready to write into a workspace."""

    skills: list[tuple[AgentSkillBinding, Path]] = field(default_factory=list)
    mcps: list[tuple[AgentMcpBinding, MCPClient]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AgentBindingResolver:
    """Resolve enabled Agent bindings into an existing workspace.

    Resolution is performed before workspace mutation so failed Agent updates
    do not leave persisted storage and runtime workspace state diverged.
    """

    def __init__(
        self,
        *,
        skillhub_client: SkillHubCatalogClient | None = None,
        cache_dir: str | PathLike[str] | None = None,
    ) -> None:
        """Bind resolver dependencies.

        Args:
            skillhub_client (`SkillHubCatalogClient | None`, optional):
                Backend-only SkillHub client used for resolving/downloading
                SkillHub skill packages.
            cache_dir (`str | os.PathLike[str] | None`, optional):
                Local cache root for downloaded SkillHub skill packages.
        """
        self._skillhub_client = skillhub_client
        self._cache_dir = Path(
            cache_dir
            if cache_dir is not None
            else Path(tempfile.gettempdir()) / "agentscope-skillhub-skills",
        )

    async def materialize_agent_bindings(
        self,
        *,
        user_id: str,
        agent_record: AgentRecord,
        workspace: WorkspaceBase,
        skillhub_token: str | None = None,
    ) -> AgentBindingMaterializationResult:
        """Materialise enabled Agent bindings into ``workspace``."""
        result = AgentBindingMaterializationResult()
        existing_skill_names = await self._existing_skill_names(workspace)
        existing_mcps = await self._existing_mcps(workspace)
        existing_mcp_names = {mcp.name for mcp in existing_mcps}
        mcp_names_to_refresh = self._skillhub_mcp_names_missing_auth(
            agent_record,
            existing_mcps,
            skillhub_token,
        )
        resolved = await self._resolve_agent_bindings(
            user_id=user_id,
            agent_record=agent_record,
            skillhub_token=skillhub_token,
            existing_skill_names=existing_skill_names,
            existing_mcp_names=existing_mcp_names - mcp_names_to_refresh,
        )
        if resolved.errors:
            result.errors.extend(resolved.errors)
            return result
        for name in sorted(mcp_names_to_refresh):
            try:
                await workspace.remove_mcp(name)
            except Exception as exc:  # pylint: disable=broad-except
                message = (
                    f"Refreshing MCP binding {name} failed for agent "
                    f"{agent_record.id}: {exc}"
                )
                logger.warning(message)
                result.errors.append(message)
        if result.errors:
            return result
        await self._apply_resolved_bindings(
            workspace=workspace,
            resolved=resolved,
            result=result,
        )
        return result

    async def reconcile_agent_bindings(
        self,
        *,
        user_id: str,
        before_agent_record: AgentRecord,
        after_agent_record: AgentRecord,
        workspace: WorkspaceBase,
        skillhub_token: str | None = None,
    ) -> AgentBindingMaterializationResult:
        """Synchronise one workspace after an Agent binding edit."""
        result = AgentBindingMaterializationResult()
        before_skill_names = self._agent_skill_binding_names(before_agent_record)
        after_skill_names = self._agent_skill_binding_names(after_agent_record)
        existing_skill_names = await self._existing_skill_names(workspace)
        before_mcp_names = self._agent_mcp_binding_names(before_agent_record)
        after_mcp_names = self._agent_mcp_binding_names(after_agent_record)
        changed_mcp_names = self._changed_mcp_binding_names(
            before_agent_record,
            after_agent_record,
        )
        existing_mcps = await self._existing_mcps(workspace)
        existing_mcp_names = {mcp.name for mcp in existing_mcps}
        refresh_mcp_names = self._skillhub_mcp_names_missing_auth(
            after_agent_record,
            existing_mcps,
            skillhub_token,
        )
        mcp_names_to_remove = (
            (before_mcp_names - after_mcp_names)
            | changed_mcp_names
            | refresh_mcp_names
        ) & existing_mcp_names
        resolved = await self._resolve_agent_bindings(
            user_id=user_id,
            agent_record=after_agent_record,
            skillhub_token=skillhub_token,
            existing_skill_names=existing_skill_names - (
                (before_skill_names - after_skill_names) & existing_skill_names
            ),
            existing_mcp_names=existing_mcp_names - mcp_names_to_remove,
        )
        if resolved.errors:
            result.errors.extend(resolved.errors)
            return result

        skill_snapshots = await self._existing_skills(workspace)
        mcp_snapshots = await self._existing_mcps(workspace)
        before_workspace_skill_names = {skill.name for skill in skill_snapshots}
        before_workspace_mcp_names = {mcp.name for mcp in mcp_snapshots}
        try:
            for name in sorted((before_skill_names - after_skill_names) & existing_skill_names):
                try:
                    await workspace.remove_skill(name)
                except Exception as exc:  # pylint: disable=broad-except
                    message = (
                        f"Removing Skill binding {name} failed for agent "
                        f"{after_agent_record.id}: {exc}"
                    )
                    logger.warning(message)
                    result.errors.append(message)

            for name in sorted(mcp_names_to_remove):
                try:
                    await workspace.remove_mcp(name)
                except Exception as exc:  # pylint: disable=broad-except
                    message = (
                        f"Removing MCP binding {name} failed for agent "
                        f"{after_agent_record.id}: {exc}"
                    )
                    logger.warning(message)
                    result.errors.append(message)

            if not result.errors:
                await self._apply_resolved_bindings(
                    workspace=workspace,
                    resolved=resolved,
                    result=result,
                )
        except Exception as exc:  # pylint: disable=broad-except
            result.errors.append(
                f"Agent binding reconcile failed for agent "
                f"{after_agent_record.id}: {exc}",
            )
        if result.errors:
            await self._rollback_workspace_bindings(
                workspace=workspace,
                skill_snapshots=skill_snapshots,
                mcp_snapshots=mcp_snapshots,
                before_skill_names=before_workspace_skill_names,
                before_mcp_names=before_workspace_mcp_names,
            )
        return result

    async def _resolve_agent_bindings(
        self,
        *,
        user_id: str,
        agent_record: AgentRecord,
        skillhub_token: str | None,
        existing_skill_names: set[str],
        existing_mcp_names: set[str],
    ) -> _ResolvedAgentBindings:
        """Resolve all pending bindings without mutating the workspace."""
        resolved = _ResolvedAgentBindings()
        for binding in agent_record.data.skill_bindings:
            if not binding.enabled:
                continue
            if self._skill_binding_names(binding) & existing_skill_names:
                continue
            try:
                resolved.skills.append(
                    (
                        binding,
                        await self._resolve_skill_binding(
                            binding,
                            skillhub_token,
                        ),
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-except
                message = (
                    f"Skill binding {binding.namespace}/{binding.slug} "
                    f"failed for agent {agent_record.id}: {exc}"
                )
                logger.warning(message)
                resolved.errors.append(message)

        for binding in agent_record.data.mcp_bindings:
            if not binding.enabled:
                continue
            mcp_name = self._mcp_binding_name(binding)
            if mcp_name in existing_mcp_names:
                continue
            try:
                resolved.mcps.append(
                    (
                        binding,
                        self._resolve_mcp_binding(
                            binding,
                            skillhub_token,
                        ),
                    ),
                )
            except Exception as exc:  # pylint: disable=broad-except
                message = (
                    f"MCP binding {binding.name or binding.skillhub_server_id} "
                    f"failed for user {user_id} agent {agent_record.id}: {exc}"
                )
                logger.warning(message)
                resolved.errors.append(message)
        return resolved

    async def _apply_resolved_bindings(
        self,
        *,
        workspace: WorkspaceBase,
        resolved: _ResolvedAgentBindings,
        result: AgentBindingMaterializationResult,
    ) -> None:
        """Write resolved bindings into a workspace and rollback on failure."""
        skill_snapshots = await self._existing_skills(workspace)
        mcp_snapshots = await self._existing_mcps(workspace)
        before_skill_names = {skill.name for skill in skill_snapshots}
        before_mcp_names = {mcp.name for mcp in mcp_snapshots}
        try:
            for _binding, skill_path in resolved.skills:
                await workspace.add_skill(str(skill_path))
                result.skills_added += 1
            for _binding, mcp_client in resolved.mcps:
                await workspace.add_mcp(mcp_client)
                result.mcps_added += 1
        except Exception as exc:  # pylint: disable=broad-except
            result.errors.append(f"Agent binding materialize failed: {exc}")
            await self._rollback_workspace_bindings(
                workspace=workspace,
                skill_snapshots=skill_snapshots,
                mcp_snapshots=mcp_snapshots,
                before_skill_names=before_skill_names,
                before_mcp_names=before_mcp_names,
            )
            result.skills_added = 0
            result.mcps_added = 0

    async def _rollback_workspace_bindings(
        self,
        *,
        workspace: WorkspaceBase,
        skill_snapshots: list,
        mcp_snapshots: list[MCPClient],
        before_skill_names: set[str],
        before_mcp_names: set[str],
    ) -> None:
        """Best-effort restoration after a failed binding workspace write."""
        current_skill_names = await self._existing_skill_names(workspace)
        for name in sorted(current_skill_names - before_skill_names):
            try:
                await workspace.remove_skill(name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Rollback removing Skill %s failed: %s", name, exc)
        current_mcp_names = await self._existing_mcp_names(workspace)
        for name in sorted(current_mcp_names - before_mcp_names):
            try:
                await workspace.remove_mcp(name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Rollback removing MCP %s failed: %s", name, exc)
        restored_skill_names = await self._existing_skill_names(workspace)
        for skill in skill_snapshots:
            if skill.name in restored_skill_names:
                continue
            try:
                await workspace.add_skill(skill.dir)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Rollback restoring Skill %s failed: %s", skill.name, exc)
        restored_mcp_names = await self._existing_mcp_names(workspace)
        for mcp in mcp_snapshots:
            if mcp.name in restored_mcp_names:
                continue
            try:
                await workspace.add_mcp(mcp)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Rollback restoring MCP %s failed: %s", mcp.name, exc)

    async def _resolve_skill_binding(
        self,
        binding: AgentSkillBinding,
        skillhub_token: str | None,
    ) -> Path:
        """Resolve one skill binding into a local skill directory."""
        local_path = self._first_text(
            binding.config_json,
            binding.snapshot_json,
            keys=("skill_path", "localPath", "local_path", "installDir"),
        )
        if local_path:
            return Path(local_path)

        if binding.source != "skillhub":
            raise ValueError("custom skill bindings require config_json.skill_path")
        if self._skillhub_client is None:
            raise ValueError("SkillHub client is not configured")
        if not binding.namespace or not binding.slug:
            raise ValueError("SkillHub skill binding requires namespace and slug")

        resolved = await self._skillhub_client.resolve_skill(
            binding.namespace,
            binding.slug,
            binding.version,
            skillhub_token,
        )
        version = str(resolved.get("version") or binding.version or "latest")
        cache_path = (
            self._cache_dir
            / self._safe_segment(binding.namespace)
            / self._safe_segment(binding.slug)
            / self._safe_segment(version)
        )
        existing = self._find_single_skill_dir(cache_path)
        if existing is not None:
            return existing

        archive = await self._skillhub_client.download_skill(
            binding.namespace,
            binding.slug,
            version,
            skillhub_token,
        )
        if len(archive) > MAX_SKILL_ARCHIVE_BYTES:
            raise ValueError("downloaded package is too large")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{cache_path.name}.tmp-",
                dir=str(cache_path.parent),
            ),
        )
        try:
            self._extract_zip_safely(archive, temp_dir)
            skill_dir = self._find_single_skill_dir(temp_dir)
            if skill_dir is None:
                raise ValueError("downloaded package does not contain SKILL.md")
            if cache_path.exists():
                shutil.rmtree(cache_path)
            shutil.move(str(temp_dir), str(cache_path))
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

        skill_dir = self._find_single_skill_dir(cache_path)
        if skill_dir is None:
            raise ValueError("downloaded package does not contain SKILL.md")
        return skill_dir

    def _resolve_mcp_binding(
        self,
        binding: AgentMcpBinding,
        skillhub_token: str | None,
    ) -> MCPClient:
        """Resolve one MCP binding into an MCP client."""
        raw_config = self._first_record(
            binding.config_json,
            binding.snapshot_json,
            keys=("mcp_client", "mcpClient", "mcp_config", "mcpConfig"),
        )
        if raw_config is not None:
            raw_config = self._with_skillhub_auth_headers(
                binding,
                raw_config,
                skillhub_token,
            )
            if "mcp_config" in raw_config:
                return MCPClient.model_validate(raw_config)
            return MCPClient(
                name=self._mcp_binding_name(binding),
                is_stateful=bool(raw_config.get("is_stateful", False)),
                mcp_config=raw_config,
            )

        endpoint_url = (
            binding.endpoint_url
            or self._first_text(
                binding.config_json,
                binding.snapshot_json,
                keys=(
                    "endpoint_url",
                    "endpointUrl",
                    "streamableHttpUrl",
                    "sseUrl",
                    "url",
                ),
            )
        )
        if not endpoint_url:
            raise ValueError("MCP binding requires endpoint_url or mcp_config")
        return MCPClient(
            name=self._mcp_binding_name(binding),
            is_stateful=bool(binding.config_json.get("is_stateful", False)),
            mcp_config=HttpMCPConfig(
                url=endpoint_url,
                headers=self._skillhub_auth_headers(binding, skillhub_token),
                timeout=self._float_or_none(binding.config_json.get("timeout")),
            ),
            enable_tools=self._string_list(binding.config_json.get("enable_tools")),
            disable_tools=self._string_list(binding.config_json.get("disable_tools")),
            execution_timeout=self._float_or_none(
                binding.config_json.get("execution_timeout"),
            ),
        )

    @classmethod
    def _with_skillhub_auth_headers(
        cls,
        binding: AgentMcpBinding,
        raw_config: dict[str, Any],
        skillhub_token: str | None,
    ) -> dict[str, Any]:
        """Return a copy of an MCP config with SkillHub auth headers applied."""
        headers = cls._skillhub_auth_headers(binding, skillhub_token)
        if not headers:
            return raw_config

        next_config = dict(raw_config)
        nested = next_config.get("mcp_config")
        if isinstance(nested, dict):
            if not cls._is_http_mcp_config(nested):
                return next_config
            next_nested = dict(nested)
            existing_headers = next_nested.get("headers")
            next_nested["headers"] = cls._merge_headers(existing_headers, headers)
            next_config["mcp_config"] = next_nested
            return next_config

        if not cls._is_http_mcp_config(next_config):
            return next_config
        existing_headers = next_config.get("headers")
        next_config["headers"] = cls._merge_headers(existing_headers, headers)
        return next_config

    @classmethod
    def _skillhub_auth_headers(
        cls,
        binding: AgentMcpBinding,
        skillhub_token: str | None,
    ) -> dict[str, str] | None:
        """Build runtime-only auth headers for SkillHub-hosted MCP servers."""
        if binding.source != "skillhub" or not skillhub_token:
            return None
        normalized = skillhub_token.strip()
        if not normalized:
            return None
        return {
            "Authorization": f"Bearer {normalized}",
            "x-skillhub-token": normalized,
        }

    @staticmethod
    def _is_http_mcp_config(value: dict[str, Any]) -> bool:
        """Return whether an MCP config dictionary represents HTTP transport."""
        return value.get("type") == "http_mcp" or isinstance(value.get("url"), str)

    @staticmethod
    def _merge_headers(
        existing: Any,
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Merge only string-valued existing headers with required headers."""
        merged: dict[str, str] = {}
        if isinstance(existing, dict):
            merged.update(
                {
                    str(key): value
                    for key, value in existing.items()
                    if isinstance(value, str)
                },
            )
        merged.update(headers)
        return merged

    @staticmethod
    def _extract_zip_safely(archive: bytes, target_dir: Path) -> None:
        """Extract a zip archive without allowing path traversal."""
        root = target_dir.resolve()
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            entries = zf.infolist()
            if len(entries) > MAX_SKILL_ZIP_ENTRIES:
                raise ValueError("downloaded package contains too many files")
            total_size = 0
            for info in entries:
                target = (root / info.filename).resolve()
                if root not in (target, *target.parents):
                    raise ValueError(f"unsafe zip entry path: {info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if info.file_size > MAX_SKILL_ZIP_ENTRY_BYTES:
                    raise ValueError(
                        f"downloaded package file is too large: {info.filename}",
                    )
                total_size += info.file_size
                if total_size > MAX_SKILL_ZIP_TOTAL_BYTES:
                    raise ValueError("downloaded package expands to too much data")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    @staticmethod
    def _find_single_skill_dir(root: Path) -> Path | None:
        """Return the only directory containing SKILL.md under ``root``."""
        if not root.exists():
            return None
        matches = list(root.rglob("SKILL.md"))
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError("downloaded package contains multiple SKILL.md files")
        return matches[0].parent

    @staticmethod
    def _safe_segment(value: str) -> str:
        """Return a filesystem/MCP-name safe segment."""
        return _SAFE_NAME_RE.sub("_", value).strip("._") or "item"

    @staticmethod
    def _first_text(
        *records: dict[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        """Return the first non-empty string value from candidate records."""
        for record in records:
            for key in keys:
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    @staticmethod
    def _first_record(
        *records: dict[str, Any],
        keys: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Return the first nested object value from candidate records."""
        for record in records:
            for key in keys:
                value = record.get(key)
                if isinstance(value, dict):
                    return value
        return None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        """Coerce numeric config values to float."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            return float(value)
        return None

    @staticmethod
    def _string_list(value: Any) -> list[str] | None:
        """Coerce string-list config values."""
        if value is None:
            return None
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return value
        return None

    @staticmethod
    async def _existing_skill_names(workspace: WorkspaceBase) -> set[str]:
        """Best-effort set of currently materialised skill names."""
        try:
            return {skill.name for skill in await workspace.list_skills()}
        except Exception:  # pylint: disable=broad-except
            return set()

    @staticmethod
    async def _existing_skills(workspace: WorkspaceBase) -> list:
        """Best-effort list of currently materialised skills."""
        try:
            return list(await workspace.list_skills())
        except Exception:  # pylint: disable=broad-except
            return []

    @staticmethod
    async def _existing_mcp_names(workspace: WorkspaceBase) -> set[str]:
        """Best-effort set of currently materialised MCP names."""
        try:
            return {mcp.name for mcp in await workspace.list_mcps()}
        except Exception:  # pylint: disable=broad-except
            return set()

    @staticmethod
    async def _existing_mcps(workspace: WorkspaceBase) -> list[MCPClient]:
        """Best-effort list of currently materialised MCP clients."""
        try:
            return list(await workspace.list_mcps())
        except Exception:  # pylint: disable=broad-except
            return []

    @staticmethod
    def _skill_binding_names(binding: AgentSkillBinding) -> set[str]:
        """Possible workspace-facing names for a skill binding."""
        names = {
            value.strip()
            for value in (
                binding.display_name,
                binding.slug,
                binding.skillhub_skill_id,
            )
            if isinstance(value, str) and value.strip()
        }
        return names

    @classmethod
    def _agent_skill_binding_names(cls, record: AgentRecord) -> set[str]:
        """Workspace-facing names for enabled skill bindings on an Agent."""
        names: set[str] = set()
        for binding in record.data.skill_bindings:
            if binding.enabled:
                names.update(cls._skill_binding_names(binding))
        return names

    @classmethod
    def _agent_mcp_binding_names(cls, record: AgentRecord) -> set[str]:
        """Workspace-facing names for enabled MCP bindings on an Agent."""
        names: set[str] = set()
        for binding in record.data.mcp_bindings:
            if not binding.enabled:
                continue
            name = cls._mcp_binding_name(binding)
            if name:
                names.add(name)
        return names

    @classmethod
    def _mcp_binding_name(cls, binding: AgentMcpBinding) -> str:
        """Return the runtime workspace name for an MCP binding."""
        return binding.name or cls._safe_segment(
            binding.skillhub_server_id or "skillhub_mcp",
        )

    @classmethod
    def _changed_mcp_binding_names(
        cls,
        before: AgentRecord,
        after: AgentRecord,
    ) -> set[str]:
        """Names whose enabled MCP config changed without a name change."""
        before_fingerprints = cls._mcp_binding_fingerprints(before)
        after_fingerprints = cls._mcp_binding_fingerprints(after)
        return {
            name
            for name, fingerprint in after_fingerprints.items()
            if name in before_fingerprints
            and before_fingerprints[name] != fingerprint
        }

    @classmethod
    def _mcp_binding_fingerprints(cls, record: AgentRecord) -> dict[str, str]:
        """Stable comparable representation of enabled MCP bindings."""
        fingerprints: dict[str, str] = {}
        for binding in record.data.mcp_bindings:
            if not binding.enabled:
                continue
            name = cls._mcp_binding_name(binding)
            if name:
                fingerprints[name] = binding.model_dump_json()
        return fingerprints

    @classmethod
    def _skillhub_mcp_names_missing_auth(
        cls,
        record: AgentRecord,
        existing_mcps: list[MCPClient],
        skillhub_token: str | None,
    ) -> set[str]:
        """Return existing SkillHub MCP names that need token headers refreshed."""
        by_name = {mcp.name: mcp for mcp in existing_mcps}
        names: set[str] = set()
        for binding in record.data.mcp_bindings:
            if not binding.enabled or binding.source != "skillhub":
                continue
            expected_headers = cls._skillhub_auth_headers(binding, skillhub_token)
            if not expected_headers:
                continue
            name = cls._mcp_binding_name(binding)
            existing = by_name.get(name)
            if existing is None:
                continue
            config = getattr(existing, "mcp_config", None)
            if not isinstance(config, HttpMCPConfig):
                continue
            current_headers = config.headers or {}
            if any(
                current_headers.get(key) != value
                for key, value in expected_headers.items()
            ):
                names.add(name)
        return names
