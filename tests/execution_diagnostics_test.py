# -*- coding: utf-8 -*-
"""Focused tests for backend execution diagnostics."""
from __future__ import annotations

import asyncio
from datetime import datetime
import sys
import types
import uuid
from unittest.mock import patch
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    shortuuid.uuid = lambda: uuid.uuid4().hex
    sys.modules["shortuuid"] = shortuuid

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._router import session_router
from agentscope.app.deps import get_storage
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    ExecutionTraceRecord,
    RedisStorage,
    SessionConfig,
)
from agentscope.app._service._chat import ChatService
from agentscope.app._service._execution_diagnostics import (
    ExecutionTraceRecorder,
    summarize_event,
)
from agentscope.event import (
    ConfirmResult,
    DataBlockDeltaEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    RequireUserConfirmEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultState,
)


class _FakeWorkspace:
    """Small workspace handle for ChatService diagnostics tests."""

    workdir = "D:/tmp/workspace"


class _FakeWorkspaceManager:
    """Workspace manager stub."""

    async def get_workspace(self, *_args: object, **_kwargs: object) -> object:
        return _FakeWorkspace()


class _FakeAccess:
    """Resource access stub for resolving the test agent."""

    def __init__(self, agent: AgentRecord) -> None:
        self.agent = agent

    async def resolve_agent(self, *_args: object, **_kwargs: object) -> AgentRecord:
        return self.agent


class _FakeAgent:
    """Agent stub that emits a deterministic event stream."""

    def __init__(self, name: str, state: object, **_kwargs: object) -> None:
        self.name = name
        self.state = state

    async def reply_stream(self, inputs: object = None):  # noqa: ANN202
        del inputs
        yield ReplyStartEvent(
            session_id="session-1",
            reply_id="reply-1",
            name=self.name,
        )
        yield ModelCallStartEvent(reply_id="reply-1", model_name="gpt-4")
        yield ModelCallEndEvent(
            reply_id="reply-1",
            input_tokens=3,
            output_tokens=5,
        )
        yield TextBlockStartEvent(reply_id="reply-1", block_id="text-1")
        yield TextBlockDeltaEvent(
            reply_id="reply-1",
            block_id="text-1",
            delta="done",
        )
        yield TextBlockEndEvent(reply_id="reply-1", block_id="text-1")
        yield ToolCallStartEvent(
            reply_id="reply-1",
            tool_call_id="tool-1",
            tool_call_name="Search",
        )
        yield ToolCallDeltaEvent(
            reply_id="reply-1",
            tool_call_id="tool-1",
            delta='{"query":"abc"}',
        )
        yield ToolCallEndEvent(reply_id="reply-1", tool_call_id="tool-1")
        yield ToolResultStartEvent(
            reply_id="reply-1",
            tool_call_id="tool-1",
            tool_call_name="Search",
        )
        yield ToolResultTextDeltaEvent(
            reply_id="reply-1",
            tool_call_id="tool-1",
            delta="result text",
        )
        yield ToolResultEndEvent(
            reply_id="reply-1",
            tool_call_id="tool-1",
            state=ToolResultState.SUCCESS,
        )
        yield ReplyEndEvent(session_id="session-1", reply_id="reply-1")


class _FailingAgent(_FakeAgent):
    """Agent stub that fails after the trace starts."""

    async def reply_stream(self, inputs: object = None):  # noqa: ANN202
        del inputs
        yield ReplyStartEvent(
            session_id="session-1",
            reply_id="reply-1",
            name=self.name,
        )
        raise RuntimeError("boom")


class _CancelledAgent(_FakeAgent):
    """Agent stub that is cancelled after the trace starts."""

    async def reply_stream(self, inputs: object = None):  # noqa: ANN202
        del inputs
        yield ReplyStartEvent(
            session_id="session-1",
            reply_id="reply-1",
            name=self.name,
        )
        raise asyncio.CancelledError()


class _ShouldNotRunAgent(_FakeAgent):
    """Agent stub that fails if a stale continuation reaches assembly."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("stale continuation should not assemble agent")


async def _failing_extra_middlewares(
    _user_id: str,
    _agent_id: str,
    _session_id: str,
) -> list:
    """Extra middleware factory that fails during setup."""
    raise RuntimeError("middleware setup failed")


class _FakeRedis:
    """Small async Redis stub for diagnostics storage tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                count += 1
            if key in self.sets:
                del self.sets[key]
                count += 1
            if key in self.lists:
                del self.lists[key]
                count += 1
        return count

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *values: str) -> None:
        current = self.sets.setdefault(key, set())
        for value in values:
            current.discard(value)

    async def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    async def lindex(self, key: str, index: int) -> str | None:
        values = self.lists.get(key, [])
        try:
            return values[index]
        except IndexError:
            return None

    async def lset(self, key: str, index: int, value: str) -> None:
        self.lists[key][index] = value

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        return values[start : end + 1]

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def expire(self, key: str, ttl: int) -> None:
        return None


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by an in-memory stub."""
    storage = RedisStorage.__new__(RedisStorage)
    # pylint: disable=protected-access
    storage._client = _FakeRedis()
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_session_config() -> SessionConfig:
    """Create a test SessionConfig with a chat model config."""
    return SessionConfig(
        workspace_id="ws-1",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


def make_agent_record(user_id: str = "user-1") -> AgentRecord:
    """Create a test AgentRecord."""
    return AgentRecord(
        id="agent-1",
        user_id=user_id,
        data=AgentData(
            name="assistant",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


class TestExecutionTraceStorage(IsolatedAsyncioTestCase):
    """Tests for Redis execution trace persistence."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            session_id="session-1",
        )

    async def test_trace_round_trips_and_is_indexed_by_reply(self) -> None:
        """Trace CRUD supports direct, by-reply, and per-session listing."""
        trace = ExecutionTraceRecord(
            id="trace-1",
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session.id,
            reply_id="reply-1",
            input_message_id="input-1",
            status="completed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            duration_ms=12,
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            model={
                "type": "openai",
                "model": "gpt-4",
                "credential_id": "cred-1",
            },
            fallback_model={
                "type": "openai",
                "model": "gpt-4o-mini",
                "credential_id": "cred-2",
            },
            stages=[{"name": "agent_reply_event_loop", "status": "completed"}],
            events=[{"type": "REPLY_START", "reply_id": "reply-1"}],
            tool_calls=[
                {
                    "id": "tool-1",
                    "name": "Search",
                    "input_preview": "{}",
                    "result_preview": "ok",
                },
            ],
        )

        await self.storage.upsert_execution_trace(
            self.user_id,
            self.session.id,
            trace,
        )

        direct = await self.storage.get_execution_trace(
            self.user_id,
            self.session.id,
            trace.id,
        )
        by_reply = await self.storage.get_execution_trace_by_reply(
            self.user_id,
            self.session.id,
            "reply-1",
        )
        listed = await self.storage.list_execution_traces(
            self.user_id,
            self.session.id,
        )

        self.assertEqual(direct, trace)
        self.assertEqual(by_reply, trace)
        self.assertEqual([item.id for item in listed], [trace.id])

    async def test_delete_session_cascades_execution_traces(self) -> None:
        """Deleting a session also removes trace and reply lookup keys."""
        trace = ExecutionTraceRecord(
            id="trace-1",
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session.id,
            reply_id="reply-1",
            input_message_id="input-1",
            status="running",
        )
        await self.storage.upsert_execution_trace(
            self.user_id,
            self.session.id,
            trace,
        )

        await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            self.session.id,
        )

        self.assertIsNone(
            await self.storage.get_execution_trace(
                self.user_id,
                self.session.id,
                trace.id,
            ),
        )
        self.assertIsNone(
            await self.storage.get_execution_trace_by_reply(
                self.user_id,
                self.session.id,
                "reply-1",
            ),
        )
        self.assertEqual(
            await self.storage.list_execution_traces(
                self.user_id,
                self.session.id,
            ),
            [],
        )


class TestExecutionDiagnosticsRouter(IsolatedAsyncioTestCase):
    """Tests for session diagnostics endpoint ownership checks."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            session_id="session-1",
        )
        await self.storage.upsert_execution_trace(
            self.user_id,
            self.session.id,
            ExecutionTraceRecord(
                id="trace-1",
                user_id=self.user_id,
                agent_id=self.agent_id,
                session_id=self.session.id,
                reply_id="reply-1",
                input_message_id="input-1",
                status="completed",
            ),
        )

        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_storage] = lambda: self.storage
        self.client = TestClient(app)

    async def test_list_diagnostics_returns_owned_session_traces(self) -> None:
        """Diagnostics list first verifies session ownership."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics",
            params={"agent_id": self.agent_id},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["traces"][0]["id"], "trace-1")

    async def test_diagnostics_rejects_wrong_agent(self) -> None:
        """A trace is not readable unless get_session validates ownership."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/trace-1",
            params={"agent_id": "wrong-agent"},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 404)

    async def test_list_diagnostics_rejects_empty_agent_id(self) -> None:
        """Diagnostics list rejects empty public agent ids."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics",
            params={"agent_id": ""},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 422)

    async def test_diagnostics_by_reply_returns_owned_trace(self) -> None:
        """Diagnostics can fetch a trace through its reply id."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/by-reply/reply-1",
            params={"agent_id": self.agent_id},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "trace-1")

    async def test_diagnostics_by_reply_rejects_empty_agent_id(self) -> None:
        """Diagnostics by reply rejects empty public agent ids."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/by-reply/reply-1",
            params={"agent_id": ""},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 422)

    async def test_diagnostics_by_trace_rejects_empty_agent_id(self) -> None:
        """Diagnostics by trace rejects empty public agent ids."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/trace-1",
            params={"agent_id": ""},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 422)

    async def test_diagnostics_missing_trace_id_returns_404(self) -> None:
        """Missing trace ids return 404 after ownership is validated."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/missing-trace",
            params={"agent_id": self.agent_id},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 404)

    async def test_diagnostics_missing_reply_id_returns_404(self) -> None:
        """Missing reply ids return 404 after ownership is validated."""
        response = self.client.get(
            f"/sessions/{self.session.id}/diagnostics/by-reply/missing-reply",
            params={"agent_id": self.agent_id},
            headers={"X-User-ID": self.user_id},
        )

        self.assertEqual(response.status_code, 404)


class TestExecutionEventSummary(IsolatedAsyncioTestCase):
    """Tests for bounded event summaries."""

    async def test_data_delta_summary_does_not_store_full_payload(self) -> None:
        """Large data blocks are truncated in trace event summaries."""
        summary = summarize_event(
            DataBlockDeltaEvent(
                reply_id="reply-1",
                block_id="block-1",
                media_type="image/png",
                data="x" * 5000,
            ),
        )

        self.assertEqual(summary["type"], "DATA_BLOCK_DELTA")
        self.assertLess(len(summary["data"]), 200)
        self.assertTrue(summary["data_truncated"])

    async def test_reply_start_summary_keeps_reply_id(self) -> None:
        """Reply start summaries preserve the reply id for lookup."""
        summary = summarize_event(
            ReplyStartEvent(
                session_id="session-1",
                reply_id="reply-1",
                name="assistant",
            ),
        )

        self.assertEqual(summary["reply_id"], "reply-1")

    async def test_hitl_tool_call_input_is_truncated_in_event_summary(self) -> None:
        """HITL summaries do not persist full raw tool arguments."""
        summary = summarize_event(
            RequireUserConfirmEvent(
                reply_id="reply-1",
                tool_calls=[
                    ToolCallBlock(
                        id="tool-1",
                        name="SensitiveTool",
                        input="x" * 5000,
                    ),
                ],
            ),
        )

        tool_call = summary["tool_calls"][0]
        self.assertNotIn("input", tool_call)
        self.assertLess(len(tool_call["input_preview"]), 200)
        self.assertTrue(tool_call["input_truncated"])


class TestExecutionTraceRecorder(IsolatedAsyncioTestCase):
    """Tests for aggregated execution trace fields."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.trace = ExecutionTraceRecord(
            id="trace-1",
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
        )
        self.recorder = ExecutionTraceRecorder(self.storage, self.trace)
        await self.recorder.start()

    async def test_model_usage_and_tool_summary_are_structured(self) -> None:
        """Recorder aggregates total tokens and detailed tool call previews."""
        self.recorder.set_models(
            ChatModelConfig(
                type="openai",
                credential_id="cred-1",
                model="gpt-4",
                parameters={},
            ),
            ChatModelConfig(
                type="openai",
                credential_id="cred-2",
                model="gpt-4o-mini",
                parameters={},
            ),
        )
        self.recorder.record_event(
            ModelCallEndEvent(
                reply_id="reply-1",
                input_tokens=4,
                output_tokens=6,
            ),
        )
        self.recorder.record_event(
            ToolCallStartEvent(
                reply_id="reply-1",
                tool_call_id="tool-1",
                tool_call_name="Search",
            ),
        )
        self.recorder.record_event(
            ToolCallDeltaEvent(
                reply_id="reply-1",
                tool_call_id="tool-1",
                delta='{"query":"abc"}',
            ),
        )
        self.recorder.record_event(
            ToolResultTextDeltaEvent(
                reply_id="reply-1",
                tool_call_id="tool-1",
                delta="found answer",
            ),
        )
        self.recorder.record_event(
            ToolResultEndEvent(
                reply_id="reply-1",
                tool_call_id="tool-1",
                state=ToolResultState.SUCCESS,
            ),
        )

        self.recorder.finish("completed")

        self.assertEqual(
            self.trace.model,
            {"type": "openai", "model": "gpt-4", "credential_id": "cred-1"},
        )
        self.assertEqual(self.trace.usage["total_tokens"], 10)
        tool_call = self.trace.tool_calls[0]
        self.assertEqual(tool_call["name"], "Search")
        self.assertIn("started_at", tool_call)
        self.assertIn("finished_at", tool_call)
        self.assertIn("duration_ms", tool_call)
        self.assertEqual(tool_call["input_preview"], '{"query":"abc"}')
        self.assertEqual(tool_call["result_preview"], "found answer")

    async def test_adjacent_text_block_deltas_are_merged(self) -> None:
        """Recorder stores adjacent text deltas as one compact event."""
        self.recorder.record_event(
            {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-1",
                "block_id": "block-1",
                "delta": "hello ",
            },
        )
        self.recorder.record_event(
            {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-1",
                "block_id": "block-1",
                "delta": "world",
            },
        )
        self.recorder.record_event({"type": "MODEL_CALL_END"})
        self.recorder.record_event(
            {
                "type": "TEXT_BLOCK_DELTA",
                "reply_id": "reply-1",
                "block_id": "block-1",
                "delta": " again",
            },
        )

        self.assertEqual(
            self.trace.events,
            [
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": "block-1",
                    "delta": "hello world",
                    "merged_count": 2,
                },
                {"type": "MODEL_CALL_END"},
                {
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": "block-1",
                    "delta": " again",
                },
            ],
        )


class TestChatServiceExecutionDiagnostics(IsolatedAsyncioTestCase):
    """Tests ChatService's real trace writes."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            session_id="session-1",
        )
        self.agent_record = make_agent_record(self.user_id)

    def make_service(
        self,
        agent_cls: type[_FakeAgent],
        *,
        extra_agent_middlewares=None,
    ) -> ChatService:
        """Create ChatService with fake dependencies."""
        return ChatService(
            storage=self.storage,
            workspace_manager=_FakeWorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_FakeAccess(self.agent_record),
            custom_agent_cls=agent_cls,
            extra_agent_middlewares=extra_agent_middlewares,
        )

    async def run_service(self, agent_cls: type[_FakeAgent]) -> ExecutionTraceRecord:
        """Run ChatService and return the persisted trace."""
        service = self.make_service(agent_cls)
        with (
            patch("agentscope.app._service._chat.get_model", AsyncMock()),
            patch("agentscope.app._service._chat.get_toolkit", AsyncMock()),
        ):
            await service.run(
                self.user_id,
                self.session.id,
                self.agent_id,
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="hello")],
                    id="input-1",
                ),
            )
        traces = await self.storage.list_execution_traces(
            self.user_id,
            self.session.id,
        )
        self.assertEqual(len(traces), 1)
        return traces[0]

    async def test_chat_service_writes_complete_execution_trace(self) -> None:
        """ChatService persists usage, stages, model summary, and tools."""
        trace = await self.run_service(_FakeAgent)

        self.assertEqual(trace.status, "completed")
        self.assertEqual(trace.reply_id, "reply-1")
        self.assertEqual(
            trace.model,
            {"type": "openai", "model": "gpt-4", "credential_id": "cred-1"},
        )
        self.assertEqual(trace.usage["total_tokens"], 8)
        stage_names = {stage["name"] for stage in trace.stages}
        self.assertIn("RAG/middleware setup", stage_names)
        self.assertIn("agent reply event loop", stage_names)
        self.assertTrue(
            all(stage.get("duration_ms") is not None for stage in trace.stages),
        )
        self.assertEqual(trace.tool_calls[0]["input_preview"], '{"query":"abc"}')
        self.assertEqual(trace.tool_calls[0]["result_preview"], "result text")

    async def test_chat_service_finalizes_trace_on_run_error(self) -> None:
        """A failed run still persists a non-running error trace."""
        trace = await self.run_service(_FailingAgent)

        self.assertEqual(trace.status, "error")
        self.assertEqual(trace.reply_id, "reply-1")
        self.assertEqual(trace.error["type"], "RuntimeError")

    async def test_failed_run_marks_partial_reply_finished(self) -> None:
        """A failed run must not leave the assistant reply spinning."""
        await self.run_service(_FailingAgent)

        reply = await self.storage.get_message(
            self.user_id,
            self.session.id,
            "reply-1",
        )

        self.assertIsNotNone(reply)
        self.assertIsNotNone(reply.finished_at)

    async def test_stale_user_confirmation_does_not_overwrite_trace(self) -> None:
        """A duplicate confirmation after completion is a no-op."""
        finished_call = ToolCallBlock(
            id="tool-1",
            name="Read",
            input='{"file_path":"MEMORY.md"}',
            state=ToolCallState.FINISHED,
        )
        reply = AssistantMsg(
            id="reply-1",
            name="assistant",
            content=[
                finished_call,
                TextBlock(text="done"),
            ],
            finished_at=datetime.now().isoformat(),
        )
        self.session.state.reply_id = "reply-1"
        self.session.state.context = [reply]
        await self.storage.update_session_state(
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session.id,
            state=self.session.state,
        )
        await self.storage.upsert_message(self.user_id, self.session.id, reply)
        original_trace = ExecutionTraceRecord(
            id="trace-original",
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session.id,
            reply_id="reply-1",
            status="completed",
            usage={
                "input_tokens": 10,
                "output_tokens": 3,
                "total_tokens": 13,
            },
            tool_calls=[
                {
                    "id": "tool-1",
                    "name": "Read",
                    "input_preview": finished_call.input,
                },
            ],
        )
        await self.storage.upsert_execution_trace(
            self.user_id,
            self.session.id,
            original_trace,
        )

        service = self.make_service(_ShouldNotRunAgent)
        with (
            patch("agentscope.app._service._chat.get_model", AsyncMock()),
            patch("agentscope.app._service._chat.get_toolkit", AsyncMock()),
        ):
            await service.run(
                self.user_id,
                self.session.id,
                self.agent_id,
                UserConfirmResultEvent(
                    reply_id="reply-1",
                    confirm_results=[
                        ConfirmResult(
                            confirmed=True,
                            tool_call=finished_call,
                        ),
                    ],
                ),
            )

        traces = await self.storage.list_execution_traces(
            self.user_id,
            self.session.id,
        )
        by_reply = await self.storage.get_execution_trace_by_reply(
            self.user_id,
            self.session.id,
            "reply-1",
        )
        self.assertEqual([trace.id for trace in traces], ["trace-original"])
        self.assertEqual(by_reply, original_trace)

    async def test_stale_confirmation_uses_session_state_not_finished_at(
        self,
    ) -> None:
        """A handled confirmation is stale even if reply persistence is partial."""
        finished_call = ToolCallBlock(
            id="tool-1",
            name="Read",
            input='{"file_path":"MEMORY.md"}',
            state=ToolCallState.FINISHED,
        )
        partial_reply = AssistantMsg(
            id="reply-1",
            name="assistant",
            content=[finished_call, TextBlock(text="done")],
        )
        self.session.state.reply_id = "reply-1"
        self.session.state.context = [partial_reply]
        await self.storage.update_session_state(
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session.id,
            state=self.session.state,
        )
        await self.storage.upsert_message(
            self.user_id,
            self.session.id,
            partial_reply,
        )

        service = self.make_service(_ShouldNotRunAgent)
        with (
            patch("agentscope.app._service._chat.get_model", AsyncMock()),
            patch("agentscope.app._service._chat.get_toolkit", AsyncMock()),
        ):
            await service.run(
                self.user_id,
                self.session.id,
                self.agent_id,
                UserConfirmResultEvent(
                    reply_id="reply-1",
                    confirm_results=[
                        ConfirmResult(
                            confirmed=True,
                            tool_call=finished_call,
                        ),
                    ],
                ),
            )

        self.assertEqual(
            await self.storage.list_execution_traces(
                self.user_id,
                self.session.id,
            ),
            [],
        )

    async def test_chat_service_finalizes_trace_on_cancelled_error(self) -> None:
        """A cancelled run does not leave a persisted running trace."""
        service = self.make_service(_CancelledAgent)
        with (
            patch("agentscope.app._service._chat.get_model", AsyncMock()),
            patch("agentscope.app._service._chat.get_toolkit", AsyncMock()),
            self.assertRaises(asyncio.CancelledError),
        ):
            await service.run(
                self.user_id,
                self.session.id,
                self.agent_id,
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="hello")],
                    id="input-1",
                ),
            )

        traces = await self.storage.list_execution_traces(
            self.user_id,
            self.session.id,
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, "interrupted")
        self.assertIsNotNone(traces[0].finished_at)
        self.assertIsNotNone(traces[0].duration_ms)

    async def test_middleware_setup_error_completes_stage(self) -> None:
        """Middleware setup failures close the setup stage with an error."""
        service = self.make_service(
            _FakeAgent,
            extra_agent_middlewares=_failing_extra_middlewares,
        )
        with (
            patch("agentscope.app._service._chat.get_model", AsyncMock()),
            patch("agentscope.app._service._chat.get_toolkit", AsyncMock()),
        ):
            await service.run(
                self.user_id,
                self.session.id,
                self.agent_id,
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="hello")],
                    id="input-1",
                ),
            )

        traces = await self.storage.list_execution_traces(
            self.user_id,
            self.session.id,
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, "error")
        setup_stage = next(
            stage
            for stage in traces[0].stages
            if stage["name"] == "RAG/middleware setup"
        )
        self.assertEqual(setup_stage["status"], "error")
        self.assertIsNotNone(setup_stage.get("finished_at"))
        self.assertIsNotNone(setup_stage.get("duration_ms"))
