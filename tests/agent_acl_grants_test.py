# -*- coding: utf-8 -*-
"""Tests for tiered Agent share grants."""

from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import HTTPException
from pydantic import ValidationError

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ResourceAccessService
from agentscope.app.access import (
    ResourceKind,
    ResourcePermission,
)
from agentscope.app.storage import (
    AgentData,
    AgentMcpBinding,
    AgentRecord,
    AgentShareGrant,
    AgentSkillBinding,
    ShareConfig,
)
from examples.agent_service.shared_agents import SharedAgentPolicy


class _GrantStorage:
    """Minimal storage double for Agent ACL grant behavior."""

    def __init__(self) -> None:
        self.agents: dict[tuple[str, str], AgentRecord] = {}

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """List owned user agents."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id == user_id and record.source == "user"
        ]

    async def list_shared_agents(self, viewer_id: str) -> list[AgentRecord]:
        """List cross-owner agents with legacy or explicit grants."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id != viewer_id
            and record.source == "user"
            and (
                record.data.share_config.shared
                or any(
                    grant.matches_viewer(viewer_id)
                    for grant in record.data.share_grants
                )
            )
        ]

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Get one agent by owner and id."""
        return self.agents.get((user_id, agent_id))


def _agent(
    owner_id: str,
    agent_id: str,
    *,
    shared: bool = False,
    grants: list[AgentShareGrant] | None = None,
) -> AgentRecord:
    """Build a minimal Agent record with sensitive binding config."""
    return AgentRecord(
        id=agent_id,
        user_id=owner_id,
        data=AgentData(
            name=agent_id,
            system_prompt="Owner prompt",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            share_config=ShareConfig(shared=shared),
            share_grants=grants or [],
            skill_bindings=[
                AgentSkillBinding(
                    binding_id="skill-1",
                    slug="private-skill",
                    config_json={"api_key": "skill-secret"},
                    snapshot_json={"internal": "skill-snapshot"},
                ),
            ],
            mcp_bindings=[
                AgentMcpBinding(
                    binding_id="mcp-1",
                    name="private-mcp",
                    config_json={"token": "mcp-secret"},
                    snapshot_json={"internal": "mcp-snapshot"},
                ),
            ],
        ),
    )


class AgentAclGrantsTest(IsolatedAsyncioTestCase):
    """Validate tiered Agent ACL grant behavior."""

    async def asyncSetUp(self) -> None:
        """Prepare storage and access service."""
        self.storage = _GrantStorage()
        self.policy = SharedAgentPolicy()
        self.service = ResourceAccessService(self.storage, self.policy)

    def test_agent_data_schema_includes_share_grants(self) -> None:
        """AgentData should expose explicit ACL grants."""
        schema = AgentData.model_json_schema()

        self.assertIn("share_grants", schema["properties"])

    async def test_owner_has_edit_tier(self) -> None:
        """Owners should always resolve their own agents for edit."""
        self.storage.agents[("alice", "alice-agent")] = _agent(
            "alice",
            "alice-agent",
        )

        owner_id, record = await self.service.resolve_for_edit(
            "alice",
            ResourceKind.AGENT,
            "alice-agent",
        )

        self.assertEqual(owner_id, "alice")
        self.assertEqual(record.id, "alice-agent")

    async def test_direct_run_grant_allows_runtime_but_not_edit(self) -> None:
        """A user run grant should allow runtime resolution only."""
        self.storage.agents[("bob", "bob-run")] = _agent(
            "bob",
            "bob-run",
            grants=[
                AgentShareGrant(
                    grantee_type="user",
                    grantee_id="alice",
                    tier="run",
                    created_by="bob",
                ),
            ],
        )

        refs = await self.policy.list_accessible(
            "alice",
            ResourceKind.AGENT,
            self.storage,
        )
        self.assertEqual(refs[0].permission, ResourcePermission.RUN)

        resolved = await self.service.resolve_agent("alice", "bob-run")
        self.assertEqual(resolved.user_id, "bob")

        view = await self.service.get_resource(
            "alice",
            ResourceKind.AGENT,
            "bob-run",
        )
        self.assertFalse(view.editable)

        with self.assertRaises(HTTPException) as caught:
            await self.service.resolve_for_edit(
                "alice",
                ResourceKind.AGENT,
                "bob-run",
            )
        self.assertEqual(caught.exception.status_code, 403)

    async def test_read_and_clone_grants_do_not_allow_runtime(self) -> None:
        """Read/clone grants should not resolve raw runtime Agents."""
        for tier in ("read", "clone"):
            with self.subTest(tier=tier):
                agent_id = f"bob-{tier}"
                self.storage.agents[("bob", agent_id)] = _agent(
                    "bob",
                    agent_id,
                    grants=[
                        AgentShareGrant(
                            grantee_type="user",
                            grantee_id="alice",
                            tier=tier,
                            created_by="bob",
                        ),
                    ],
                )

                view = await self.service.get_resource(
                    "alice",
                    ResourceKind.AGENT,
                    agent_id,
                )
                self.assertEqual(view.id, agent_id)

                with self.assertRaises(HTTPException) as caught:
                    await self.service.resolve_agent("alice", agent_id)
                self.assertEqual(caught.exception.status_code, 404)

    async def test_direct_edit_grant_allows_mutation(self) -> None:
        """A user edit grant should resolve to the owning record."""
        self.storage.agents[("bob", "bob-edit")] = _agent(
            "bob",
            "bob-edit",
            grants=[
                AgentShareGrant(
                    grantee_type="user",
                    grantee_id="alice",
                    tier="edit",
                    created_by="bob",
                ),
            ],
        )

        view = await self.service.get_resource(
            "alice",
            ResourceKind.AGENT,
            "bob-edit",
        )
        owner_id, record = await self.service.resolve_for_edit(
            "alice",
            ResourceKind.AGENT,
            "bob-edit",
        )

        self.assertTrue(view.editable)
        self.assertEqual(owner_id, "bob")
        self.assertEqual(record.id, "bob-edit")

    async def test_workspace_grant_applies_to_logged_in_users(self) -> None:
        """A workspace wildcard grant should be visible to any other user."""
        self.storage.agents[("bob", "bob-workspace")] = _agent(
            "bob",
            "bob-workspace",
            grants=[
                AgentShareGrant(
                    grantee_type="workspace",
                    grantee_id="*",
                    tier="clone",
                    created_by="bob",
                ),
            ],
        )

        records = await self.service.list_resource(
            "charlie",
            ResourceKind.AGENT,
        )

        self.assertEqual([record.id for record in records], ["bob-workspace"])
        self.assertFalse(records[0].editable)

    async def test_legacy_shared_toggle_maps_to_workspace_read_grant(
        self,
    ) -> None:
        """The old shared toggle should still expose read-only agents."""
        self.storage.agents[("bob", "bob-legacy")] = _agent(
            "bob",
            "bob-legacy",
            shared=True,
        )

        refs = await self.policy.list_accessible(
            "alice",
            ResourceKind.AGENT,
            self.storage,
        )

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].permission, ResourcePermission.READ)

        with self.assertRaises(HTTPException) as caught:
            await self.service.resolve_agent("alice", "bob-legacy")
        self.assertEqual(caught.exception.status_code, 404)

    def test_duplicate_share_grants_are_rejected(self) -> None:
        """One grantee should not have multiple conflicting rows."""
        with self.assertRaises(ValidationError):
            _agent(
                "bob",
                "bob-duplicate",
                grants=[
                    AgentShareGrant(
                        grantee_type="user",
                        grantee_id="alice",
                        tier="run",
                        created_by="bob",
                    ),
                    AgentShareGrant(
                        grantee_type="user",
                        grantee_id="alice",
                        tier="edit",
                        created_by="bob",
                    ),
                ],
            )

    async def test_shared_agent_view_masks_binding_owner_fields(self) -> None:
        """Shared views should not expose binding config snapshots."""
        self.storage.agents[("bob", "bob-run")] = _agent(
            "bob",
            "bob-run",
            grants=[
                AgentShareGrant(
                    grantee_type="user",
                    grantee_id="alice",
                    tier="run",
                    created_by="bob",
                ),
            ],
        )

        view = await self.service.get_resource(
            "alice",
            ResourceKind.AGENT,
            "bob-run",
        )

        self.assertEqual(view.data.skill_bindings[0].config_json, {})
        self.assertEqual(view.data.skill_bindings[0].snapshot_json, {})
        self.assertEqual(view.data.mcp_bindings[0].config_json, {})
        self.assertEqual(view.data.mcp_bindings[0].snapshot_json, {})

    async def test_non_granted_user_does_not_see_private_agent(self) -> None:
        """Private agents should remain invisible cross-owner."""
        self.storage.agents[("bob", "bob-private")] = _agent(
            "bob",
            "bob-private",
        )

        records = await self.service.list_resource(
            "alice",
            ResourceKind.AGENT,
        )
        self.assertEqual(records, [])

        with self.assertRaises(HTTPException) as caught:
            await self.service.get_resource(
                "alice",
                ResourceKind.AGENT,
                "bob-private",
            )
        self.assertEqual(caught.exception.status_code, 404)
