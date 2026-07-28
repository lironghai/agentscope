# -*- coding: utf-8 -*-
"""Shared-resource access policy for the example agent service."""

from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app.storage import StorageBase, effective_agent_share_tier


_AGENT_TIER_TO_PERMISSION = {
    "read": ResourcePermission.READ,
    "clone": ResourcePermission.CLONE,
    "run": ResourcePermission.RUN,
    "edit": ResourcePermission.EDIT,
}


class SharedAgentPolicy(ResourceAccessPolicyBase):
    """Expose opted-in agent and credential shares."""

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        """List shared resources visible to ``viewer_id``."""
        if kind is ResourceKind.CREDENTIAL:
            refs: list[ResourceRef] = []
            for record in await storage.list_shared_credentials(viewer_id):
                refs.append(
                    ResourceRef(
                        kind=ResourceKind.CREDENTIAL,
                        owner_id=record.user_id,
                        resource_id=record.id,
                        permission=ResourcePermission.READ,
                    ),
                )
            return refs

        if kind is not ResourceKind.AGENT:
            return []

        refs: list[ResourceRef] = []
        for record in await storage.list_shared_agents(viewer_id):
            tier = effective_agent_share_tier(record.data, viewer_id)
            if tier is None:
                continue
            refs.append(
                ResourceRef(
                    kind=ResourceKind.AGENT,
                    owner_id=record.user_id,
                    resource_id=record.id,
                    permission=_AGENT_TIER_TO_PERMISSION[tier],
                ),
            )
        return refs
