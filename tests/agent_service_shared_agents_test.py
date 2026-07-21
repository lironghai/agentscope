# -*- coding: utf-8 -*-
"""Tests for per-agent shared access in the example service."""

from typing import cast
from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import HTTPException

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ResourceAccessService
from agentscope.app.access import (
    ResourceKind,
    ResourcePermission,
)
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ShareConfig,
    StorageBase,
)
from examples.agent_service.shared_agents import SharedAgentPolicy


class _FakeStorage:
    """Minimal storage double for shared-agent policy tests."""

    def __init__(self) -> None:
        """Initialize test agents."""
        self.agents: dict[tuple[str, str], AgentRecord] = {
            ("bob", "bob-shared"): AgentRecord(
                id="bob-shared",
                user_id="bob",
                data=_make_agent_data("Bob shared", shared=True),
            ),
            ("bob", "bob-private"): AgentRecord(
                id="bob-private",
                user_id="bob",
                data=_make_agent_data("Bob private", shared=False),
            ),
            ("bob", "bob-team"): AgentRecord(
                id="bob-team",
                user_id="bob",
                source="team",
                data=_make_agent_data("Bob team", shared=True),
            ),
            ("alice", "alice-agent"): AgentRecord(
                id="alice-agent",
                user_id="alice",
                data=_make_agent_data("Alice own", shared=False),
            ),
        }

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """List agents owned by ``user_id``."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id == user_id and record.source == "user"
        ]

    async def list_shared_agents(self, viewer_id: str) -> list[AgentRecord]:
        """List shared agents visible to ``viewer_id``."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id != viewer_id
            and record.source == "user"
            and record.data.share_config.shared
        ]

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Get one agent by owner and id."""
        return self.agents.get((user_id, agent_id))


def _make_agent_data(name: str, shared: bool) -> AgentData:
    """Create valid agent data for tests."""
    return AgentData(
        name=name,
        system_prompt="You are a helpful assistant.",
        context_config=ContextConfig(),
        react_config=ReActConfig(),
        share_config=ShareConfig(shared=shared),
    )


class SharedAgentPolicyTest(IsolatedAsyncioTestCase):
    """Validate per-agent shared access."""

    def test_share_config_defaults_to_private(self) -> None:
        """New agents should stay private unless the owner opts in."""
        data = AgentData(
            name="Private by default",
            system_prompt="You are a helpful assistant.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        )

        self.assertFalse(data.share_config.shared)

    async def test_non_owner_gets_only_shared_agents_as_read_refs(self) -> None:
        """Non-owners should see only explicitly shared user agents."""
        policy = SharedAgentPolicy()
        storage = cast(StorageBase, _FakeStorage())

        refs = await policy.list_accessible(
            "alice",
            ResourceKind.AGENT,
            storage,
        )

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].kind, ResourceKind.AGENT)
        self.assertEqual(refs[0].owner_id, "bob")
        self.assertEqual(refs[0].resource_id, "bob-shared")
        self.assertEqual(refs[0].permission, ResourcePermission.READ)
        self.assertFalse(
            await policy.can_edit(
                "alice",
                ResourceKind.AGENT,
                "bob",
                "bob-shared",
                storage,
            ),
        )

    async def test_access_service_lists_own_and_shared_agents(self) -> None:
        """Access service should mark cross-owner shared agents read-only."""
        storage = cast(StorageBase, _FakeStorage())
        service = ResourceAccessService(
            storage=storage,
            policy=SharedAgentPolicy(),
        )

        views = await service.list_resource("alice", ResourceKind.AGENT)
        editable_by_id = {view.id: view.editable for view in views}

        self.assertEqual(
            editable_by_id,
            {
                "alice-agent": True,
                "bob-shared": False,
            },
        )

        owner_id, record = await service.resolve_for_edit(
            "alice",
            ResourceKind.AGENT,
            "alice-agent",
        )
        self.assertEqual((owner_id, record.id), ("alice", "alice-agent"))

        with self.assertRaises(HTTPException) as ctx:
            await service.resolve_for_edit(
                "alice",
                ResourceKind.AGENT,
                "bob-shared",
            )
        self.assertEqual(ctx.exception.status_code, 403)
