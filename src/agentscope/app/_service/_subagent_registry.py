# -*- coding: utf-8 -*-
"""Derived registry for team-created and invited subagents."""
from __future__ import annotations

from datetime import datetime

from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from ..storage._model import (
    AgentRecord,
    SessionRecord,
    SubagentRunRecord,
    SubagentRunStatus,
    TeamMember,
    TeamRecord,
)
from ..storage._utils import _ensure_team_members
from ._session import SessionService


class SubagentRegistryService:
    """Expose subagent/team-worker runs without adding another write path."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
    ) -> None:
        """Bind dependencies."""
        self._storage = storage
        self._bus = message_bus

    async def list_runs(
        self,
        user_id: str,
        *,
        parent_agent_id: str | None = None,
        parent_session_id: str | None = None,
        team_id: str | None = None,
    ) -> list[SubagentRunRecord]:
        """List subagent runs visible to ``user_id``.

        Rows are derived from the durable team roster and the child
        sessions. This avoids a second registry table that could drift from
        the already-authoritative team/session graph.
        """
        records: list[SubagentRunRecord] = []
        teams = await self._storage.list_teams(user_id)
        for team in teams:
            if team_id is not None and team.id != team_id:
                continue

            leader_session = await self._storage.get_session(
                user_id,
                "",
                team.session_id,
            )
            resolved_parent_agent_id = (
                leader_session.agent_id if leader_session is not None else None
            )
            if (
                parent_session_id is not None
                and team.session_id != parent_session_id
            ):
                continue
            if (
                parent_agent_id is not None
                and resolved_parent_agent_id != parent_agent_id
            ):
                continue

            members = await _ensure_team_members(self._storage, user_id, team)
            for member in members:
                records.append(
                    await self._build_record(
                        team=team,
                        parent_agent_id=resolved_parent_agent_id,
                        member=member,
                    ),
                )

        return sorted(
            records,
            key=lambda record: (
                record.created_at or datetime.min,
                record.child_session_id,
            ),
            reverse=True,
        )

    async def get_run(
        self,
        user_id: str,
        child_session_id: str,
    ) -> SubagentRunRecord | None:
        """Return one subagent registry row by child session id."""
        for record in await self.list_runs(user_id):
            if record.child_session_id == child_session_id:
                return record
        return None

    async def _build_record(
        self,
        *,
        team: TeamRecord,
        parent_agent_id: str | None,
        member: TeamMember,
    ) -> SubagentRunRecord:
        """Build one registry row from team/member/session state."""
        if member.owner_id != team.user_id:
            return SubagentRunRecord(
                run_id=f"{team.id}:{member.session_id}",
                owner_id=team.user_id,
                team_id=team.id,
                team_name=team.data.name,
                parent_agent_id=parent_agent_id,
                parent_session_id=team.session_id,
                child_owner_id=member.owner_id,
                child_agent_id=member.agent_id,
                child_agent_name=None,
                child_session_id=member.session_id,
                role=member.role,
                status="missing",
                created_at=team.created_at,
                updated_at=team.updated_at,
                error=(
                    "Cross-owner subagent registry resolution is not "
                    "enabled for this member."
                ),
            )

        child_agent = await self._storage.get_agent(
            member.owner_id,
            member.agent_id,
        )
        child_session = await self._storage.get_session(
            member.owner_id,
            member.agent_id,
            member.session_id,
        )
        status, error = await self._derive_status(
            member=member,
            child_agent=child_agent,
            child_session=child_session,
        )
        timestamp_source = child_session or child_agent or team

        return SubagentRunRecord(
            run_id=f"{team.id}:{member.session_id}",
            owner_id=team.user_id,
            team_id=team.id,
            team_name=team.data.name,
            parent_agent_id=parent_agent_id,
            parent_session_id=team.session_id,
            child_owner_id=member.owner_id,
            child_agent_id=member.agent_id,
            child_agent_name=(
                child_agent.data.name if child_agent is not None else None
            ),
            child_session_id=member.session_id,
            role=member.role,
            status=status,
            created_at=timestamp_source.created_at,
            updated_at=timestamp_source.updated_at,
            error=error,
        )

    async def _derive_status(
        self,
        *,
        member: TeamMember,
        child_agent: AgentRecord | None,
        child_session: SessionRecord | None,
    ) -> tuple[SubagentRunStatus, str | None]:
        """Derive registry status and any consistency error."""
        errors: list[str] = []
        if child_agent is None:
            errors.append(f"Child agent {member.agent_id} missing.")
        if child_session is None:
            errors.append(f"Child session {member.session_id} missing.")
            return "missing", " ".join(errors)

        if await self._bus.is_locked(
            MessageBusKeys.session_lock(member.session_id),
        ):
            return "running", " ".join(errors) or None

        status = SessionService._derive_parked_status(
            child_session.state.context,
        )
        return status.value, " ".join(errors) or None
