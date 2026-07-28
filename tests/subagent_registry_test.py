# -*- coding: utf-8 -*-
"""Tests for derived subagent/team-worker registry views."""

from collections import defaultdict
from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._router import subagent_router
from agentscope.app.deps import get_current_user_id, get_message_bus, get_storage
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    StorageBase,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState


class _FakeStorage:
    """Small storage double for subagent registry tests."""

    def __init__(self) -> None:
        self.agents: dict[tuple[str, str], AgentRecord] = {}
        self.sessions: dict[tuple[str, str], SessionRecord] = {}
        self.teams: dict[tuple[str, str], TeamRecord] = {}

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Get one agent."""
        return self.agents.get((user_id, agent_id))

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """List user-created agents only."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id == user_id and record.source == "user"
        ]

    async def get_session(
        self,
        user_id: str,
        _agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Get one session; agent_id is not part of the storage key."""
        return self.sessions.get((user_id, session_id))

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """List sessions for one agent."""
        return [
            record
            for (owner_id, _), record in self.sessions.items()
            if owner_id == user_id and record.agent_id == agent_id
        ]

    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Persist a team after legacy roster migration."""
        self.teams[(user_id, record.id)] = record
        return record

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Get one team."""
        return self.teams.get((user_id, team_id))

    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """List teams for a user."""
        return [
            record
            for (owner_id, _), record in self.teams.items()
            if owner_id == user_id
        ]


class _FakeBus:
    """Message bus double with only lock lookup implemented."""

    def __init__(self) -> None:
        self.locked: set[str] = set()

    async def is_locked(self, key: str) -> bool:
        """Return whether ``key`` is locked."""
        return key in self.locked


def _agent(
    user_id: str,
    agent_id: str,
    name: str,
    *,
    source: str = "user",
) -> AgentRecord:
    """Create an agent record."""
    return AgentRecord(
        id=agent_id,
        user_id=user_id,
        source=source,
        data=AgentData(
            id=f"data-{agent_id}",
            name=name,
            system_prompt="prompt",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _session(
    user_id: str,
    agent_id: str,
    session_id: str,
    *,
    team_id: str | None = None,
    state: AgentState | None = None,
) -> SessionRecord:
    """Create a session record."""
    return SessionRecord(
        id=session_id,
        user_id=user_id,
        agent_id=agent_id,
        team_id=team_id,
        config=SessionConfig(workspace_id=f"ws-{session_id}"),
        state=state or AgentState(),
    )


def _awaiting_permission_state() -> AgentState:
    """Build an AgentState parked on a permission request."""
    return AgentState(
        context=[
            Msg(
                name="assistant",
                role="assistant",
                content=[
                    ToolCallBlock(
                        id="tool-call-1",
                        name="NeedsApproval",
                        input="{}",
                        state=ToolCallState.ASKING,
                    ),
                ],
            ),
        ],
    )


def _fixture() -> tuple[_FakeStorage, _FakeBus]:
    """Build a registry fixture with created, invited, and missing workers."""
    storage = _FakeStorage()
    bus = _FakeBus()
    storage.agents[("user-1", "leader-agent")] = _agent(
        "user-1",
        "leader-agent",
        "Leader",
    )
    storage.agents[("user-1", "created-agent")] = _agent(
        "user-1",
        "created-agent",
        "Created Worker",
        source="team",
    )
    storage.agents[("user-1", "invited-agent")] = _agent(
        "user-1",
        "invited-agent",
        "Invited Worker",
    )
    storage.agents[("user-1", "hidden-worker")] = _agent(
        "user-1",
        "hidden-worker",
        "Hidden Worker",
        source="team",
    )
    leader_session = _session(
        "user-1",
        "leader-agent",
        "leader-session",
        team_id="team-1",
    )
    created_session = _session(
        "user-1",
        "created-agent",
        "created-session",
        team_id="team-1",
    )
    invited_session = _session(
        "user-1",
        "invited-agent",
        "invited-session",
        team_id="team-1",
        state=_awaiting_permission_state(),
    )
    storage.sessions[("user-1", "leader-session")] = leader_session
    storage.sessions[("user-1", "created-session")] = created_session
    storage.sessions[("user-1", "invited-session")] = invited_session
    storage.teams[("user-1", "team-1")] = TeamRecord(
        id="team-1",
        user_id="user-1",
        session_id="leader-session",
        data=TeamData(
            name="Research Team",
            description="Explore",
            members=[
                TeamMember(
                    owner_id="user-1",
                    agent_id="created-agent",
                    session_id="created-session",
                    role="created",
                ),
                TeamMember(
                    owner_id="user-1",
                    agent_id="invited-agent",
                    session_id="invited-session",
                    role="invited",
                ),
                TeamMember(
                    owner_id="user-1",
                    agent_id="missing-agent",
                    session_id="missing-session",
                    role="created",
                ),
            ],
        ),
    )
    bus.locked.add(MessageBusKeys.session_lock("created-session"))
    return storage, bus


class SubagentRegistryServiceTest(IsolatedAsyncioTestCase):
    """Validate the derived subagent registry service."""

    async def test_registry_lists_parent_child_status_and_errors(self) -> None:
        """Registry rows should expose correlation metadata."""
        from agentscope.app._service import SubagentRegistryService

        storage, bus = _fixture()
        service = SubagentRegistryService(
            storage=storage,
            message_bus=bus,
        )

        records = await service.list_runs("user-1")

        by_session = {record.child_session_id: record for record in records}
        self.assertEqual(
            set(by_session),
            {"created-session", "invited-session", "missing-session"},
        )
        self.assertEqual(by_session["created-session"].status, "running")
        self.assertEqual(
            by_session["invited-session"].status,
            "awaiting_permission",
        )
        self.assertEqual(by_session["missing-session"].status, "missing")
        self.assertEqual(
            by_session["created-session"].parent_agent_id,
            "leader-agent",
        )
        self.assertEqual(by_session["created-session"].team_name, "Research Team")
        self.assertEqual(by_session["created-session"].role, "created")
        self.assertEqual(by_session["invited-session"].role, "invited")
        self.assertIn("missing", by_session["missing-session"].error or "")

    async def test_registry_filters_by_parent_session_and_team(self) -> None:
        """Registry list should support parent correlation filters."""
        from agentscope.app._service import SubagentRegistryService

        storage, bus = _fixture()
        service = SubagentRegistryService(storage=storage, message_bus=bus)

        by_parent = await service.list_runs(
            "user-1",
            parent_session_id="leader-session",
        )
        by_team = await service.list_runs("user-1", team_id="team-1")
        wrong_parent = await service.list_runs(
            "user-1",
            parent_session_id="other-session",
        )

        self.assertEqual(len(by_parent), 3)
        self.assertEqual(len(by_team), 3)
        self.assertEqual(wrong_parent, [])

    async def test_registry_detail_resolves_by_child_session(self) -> None:
        """Registry detail should return one child run by session id."""
        from agentscope.app._service import SubagentRegistryService

        storage, bus = _fixture()
        service = SubagentRegistryService(storage=storage, message_bus=bus)

        record = await service.get_run("user-1", "invited-session")
        missing = await service.get_run("user-1", "no-session")

        self.assertIsNotNone(record)
        self.assertEqual(record.child_agent_id, "invited-agent")
        self.assertIsNone(missing)

    async def test_registry_does_not_dereference_foreign_owner_members(
        self,
    ) -> None:
        """Cross-owner team members should not leak agent/session metadata."""
        from agentscope.app._service import SubagentRegistryService

        storage, bus = _fixture()
        storage.agents[("user-2", "foreign-agent")] = _agent(
            "user-2",
            "foreign-agent",
            "Foreign Secret",
        )
        storage.sessions[("user-2", "foreign-session")] = _session(
            "user-2",
            "foreign-agent",
            "foreign-session",
            team_id="team-1",
        )
        storage.teams[("user-1", "team-1")].data.members.append(
            TeamMember(
                owner_id="user-2",
                agent_id="foreign-agent",
                session_id="foreign-session",
                role="invited",
            ),
        )
        bus.locked.add(MessageBusKeys.session_lock("foreign-session"))
        service = SubagentRegistryService(storage=storage, message_bus=bus)

        records = await service.list_runs("user-1")

        foreign = {
            record.child_session_id: record for record in records
        }["foreign-session"]
        self.assertEqual(foreign.child_agent_id, "foreign-agent")
        self.assertIsNone(foreign.child_agent_name)
        self.assertEqual(foreign.status, "missing")
        self.assertIn("Cross-owner", foreign.error or "")

    async def test_ordinary_agent_listing_keeps_team_workers_hidden(
        self,
    ) -> None:
        """Normal Agent lists should not include team-created workers."""
        storage, _bus = _fixture()

        records = await storage.list_agents("user-1")

        self.assertEqual(
            {record.id for record in records},
            {"leader-agent", "invited-agent"},
        )


class SubagentRegistryRouterTest(IsolatedAsyncioTestCase):
    """Validate subagent registry HTTP endpoints."""

    async def test_router_lists_and_gets_subagent_runs(self) -> None:
        """Router should expose list/detail registry endpoints."""
        storage, bus = _fixture()
        app = FastAPI()
        app.include_router(subagent_router)
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_message_bus] = lambda: bus

        client = TestClient(app)
        list_response = client.get("/subagents/?parent_session_id=leader-session")
        detail_response = client.get("/subagents/invited-session")
        missing_response = client.get("/subagents/no-session")

        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(
            {item["child_session_id"] for item in payload["runs"]},
            {"created-session", "invited-session", "missing-session"},
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.json()["child_agent_id"],
            "invited-agent",
        )
        self.assertEqual(missing_response.status_code, 404)
