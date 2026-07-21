# -*- coding: utf-8 -*-
"""Tests for the agent router share-config request schema."""

from unittest import TestCase

from agentscope.app._router._schema import CreateAgentRequest, UpdateAgentRequest
from agentscope.app.storage import AgentData, ShareConfig


class AgentRouterShareConfigTest(TestCase):
    """Validate share-config request wiring."""

    def test_create_request_exposes_share_config(self) -> None:
        """Create requests should carry the per-agent share toggle."""
        request = CreateAgentRequest(name="Shared agent")

        self.assertFalse(request.share_config.shared)

    def test_update_request_accepts_share_config(self) -> None:
        """Update requests should accept the share toggle."""
        request = UpdateAgentRequest(share_config=ShareConfig(shared=True))

        self.assertTrue(request.share_config.shared)

    def test_agent_data_schema_includes_share_config(self) -> None:
        """The generated AgentData schema should include sharing settings."""
        schema = AgentData.model_json_schema()

        self.assertIn("share_config", schema["properties"])
