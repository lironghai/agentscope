# -*- coding: utf-8 -*-
"""Tests for the agent router share-config request schema."""

import sys
import types
from unittest import TestCase

if "tree_sitter_bash" not in sys.modules:
    tsbash = types.ModuleType("tree_sitter_bash")
    tsbash.language = lambda: object()
    sys.modules["tree_sitter_bash"] = tsbash

if "tree_sitter" not in sys.modules:
    tree_sitter = types.ModuleType("tree_sitter")

    class _FakeLanguage:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeParser:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _FakeNode:
        type = ""
        children: list = []
        start_byte = 0
        end_byte = 0

    tree_sitter.Language = _FakeLanguage
    tree_sitter.Parser = _FakeParser
    tree_sitter.Node = _FakeNode
    sys.modules["tree_sitter"] = tree_sitter

if "frontmatter" not in sys.modules:
    frontmatter = types.ModuleType("frontmatter")

    class _FakeFrontmatterPost(dict):
        content = ""

    frontmatter.loads = lambda _text: _FakeFrontmatterPost()
    sys.modules["frontmatter"] = frontmatter

if "shortuuid" not in sys.modules:
    shortuuid = types.ModuleType("shortuuid")
    shortuuid.uuid = lambda: "test-shortuuid"
    sys.modules["shortuuid"] = shortuuid

from agentscope.app._router._schema import CreateAgentRequest, UpdateAgentRequest
from agentscope.app.storage import (
    AgentData,
    AgentMcpBinding,
    AgentShareGrant,
    AgentSkillBinding,
    ShareConfig,
)


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
        self.assertIn("share_grants", schema["properties"])

    def test_create_and_update_requests_accept_share_grants(self) -> None:
        """Create/update requests should carry explicit ACL grants."""
        grant = AgentShareGrant(
            grantee_type="user",
            grantee_id="alice",
            tier="run",
            created_by="bob",
        )

        create_request = CreateAgentRequest(
            name="Shared agent",
            share_grants=[grant],
        )
        update_request = UpdateAgentRequest(share_grants=[grant])

        self.assertEqual(create_request.share_grants[0].tier, "run")
        self.assertEqual(update_request.share_grants[0].grantee_id, "alice")

    def test_agent_data_schema_includes_skill_and_mcp_bindings(self) -> None:
        """The generated AgentData schema should include Agent bindings."""
        schema = AgentData.model_json_schema()

        self.assertIn("skill_bindings", schema["properties"])
        self.assertIn("mcp_bindings", schema["properties"])

    def test_create_request_accepts_skill_and_mcp_bindings(self) -> None:
        """Create requests should carry selected SkillHub resources."""
        request = CreateAgentRequest(
            name="Catalog agent",
            skill_bindings=[
                AgentSkillBinding(
                    skillhub_skill_id="skill-1",
                    namespace="global",
                    slug="summarizer",
                    display_name="Summarizer",
                ),
            ],
            mcp_bindings=[
                AgentMcpBinding(
                    skillhub_server_id="server-1",
                    name="search-mcp",
                    provider="skillhub",
                    auth_type="none",
                    transport="sse",
                    endpoint_url="https://example.test/mcp",
                ),
            ],
        )

        self.assertEqual(request.skill_bindings[0].slug, "summarizer")
        self.assertEqual(request.mcp_bindings[0].name, "search-mcp")

    def test_update_request_accepts_skill_and_mcp_bindings(self) -> None:
        """Patch requests should be able to replace selected resources."""
        request = UpdateAgentRequest(
            skill_bindings=[
                AgentSkillBinding(
                    skillhub_skill_id="skill-1",
                    namespace="global",
                    slug="summarizer",
                ),
            ],
            mcp_bindings=[],
        )

        self.assertEqual(len(request.skill_bindings), 1)
        self.assertEqual(request.mcp_bindings, [])

    def test_update_request_dump_keeps_empty_binding_lists(self) -> None:
        """Patch handlers must keep empty lists so edits can remove bindings."""
        request = UpdateAgentRequest(skill_bindings=[], mcp_bindings=[])

        payload = request.model_dump(exclude_none=True)

        self.assertEqual(payload["skill_bindings"], [])
        self.assertEqual(payload["mcp_bindings"], [])
