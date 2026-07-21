# -*- coding: utf-8 -*-
"""Shared-agent access policy for the example agent service."""

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app.storage import StorageBase


class SharedAgentPolicy(ResourceAccessPolicyBase):
    """Expose per-agent opted-in shares to other users as read-only."""

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        """List shared agents visible to ``viewer_id``."""
        if kind is not ResourceKind.AGENT:
            return []

        refs: list[ResourceRef] = []
        for record in await storage.list_shared_agents(viewer_id):
            refs.append(
                ResourceRef(
                    kind=ResourceKind.AGENT,
                    owner_id=record.user_id,
                    resource_id=record.id,
                    permission=ResourcePermission.READ,
                ),
            )
        return refs
