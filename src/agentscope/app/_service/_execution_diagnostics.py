# -*- coding: utf-8 -*-
"""Helpers for recording bounded backend execution diagnostics."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from ..storage import ChatModelConfig, ExecutionTraceRecord, StorageBase
from ...event import AgentEvent
from ...message import Msg

MAX_EVENT_SUMMARIES = 200
MAX_TOOL_CALLS = 100
MAX_STRING_CHARS = 160
MAX_ERROR_CHARS = 500


def _utc_like_now() -> str:
    """Return an ISO timestamp for trace summaries."""
    return datetime.now().isoformat()


def _truncate(value: str, limit: int = MAX_STRING_CHARS) -> tuple[str, bool]:
    """Return a bounded string and whether it was truncated."""
    if len(value) <= limit:
        return value, False
    return value[:limit] + "...", True


def summarize_error(error: BaseException) -> dict[str, Any]:
    """Build a bounded error summary."""
    message, truncated = _truncate(str(error), MAX_ERROR_CHARS)
    return {
        "type": type(error).__name__,
        "message": message,
        "message_truncated": truncated,
    }


def summarize_event(event: AgentEvent | BaseModel | dict[str, Any]) -> dict:
    """Build a bounded event summary suitable for durable traces."""
    if isinstance(event, BaseModel):
        payload = event.model_dump(mode="json")
    else:
        payload = dict(event)

    summary: dict[str, Any] = {}
    for key in (
        "type",
        "id",
        "created_at",
        "reply_id",
        "session_id",
        "block_id",
        "tool_call_id",
        "tool_call_name",
        "model_name",
        "finished_reason",
        "state",
        "name",
        "media_type",
        "input_tokens",
        "output_tokens",
    ):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                value, truncated = _truncate(value)
                summary[key] = value
                if truncated:
                    summary[f"{key}_truncated"] = True
            else:
                summary[key] = value

    if "data" in payload and isinstance(payload["data"], str):
        value, truncated = _truncate(payload["data"])
        summary["data"] = value
        summary["data_truncated"] = truncated

    if "delta" in payload and isinstance(payload["delta"], str):
        value, truncated = _truncate(payload["delta"])
        summary["delta"] = value
        if truncated:
            summary["delta_truncated"] = True

    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata:
        summary["metadata"] = {
            key: _truncate(value)[0] if isinstance(value, str) else value
            for key, value in metadata.items()
            if key in {"error", "message", "exception"}
        }

    if "tool_calls" in payload and isinstance(payload["tool_calls"], list):
        summary["tool_calls"] = [
            _summarize_tool_call(tool_call)
            for tool_call in payload["tool_calls"][:10]
        ]
        if len(payload["tool_calls"]) > 10:
            summary["tool_calls_truncated"] = True

    return summary


def input_message_id_from_input(
    input_msg: Msg | list[Msg] | object | None,
) -> str | None:
    """Return the first input message id for a chat run, when available."""
    if isinstance(input_msg, Msg):
        return input_msg.id
    if isinstance(input_msg, list):
        for item in input_msg:
            if isinstance(item, Msg):
                return item.id
    return None


def _summarize_tool_call(value: Any) -> dict[str, Any]:
    """Build a bounded tool-call summary from a model or mapping."""
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = value
    else:
        payload = {
            key: getattr(value, key)
            for key in ("id", "name", "state")
            if hasattr(value, key)
        }
    summary = {
        key: payload[key]
        for key in ("id", "name", "state", "type")
        if key in payload
    }
    if "input" in payload:
        preview, truncated = _truncate(str(payload["input"]))
        summary["input_preview"] = preview
        if truncated:
            summary["input_truncated"] = True
    return summary


def _model_summary(value: ChatModelConfig | dict[str, Any] | str | None) -> (
    dict[str, Any] | None
):
    """Return a stable bounded model summary."""
    if value is None:
        return None
    if isinstance(value, ChatModelConfig):
        return {
            "type": value.type,
            "model": value.model,
            "credential_id": value.credential_id,
        }
    if isinstance(value, dict):
        return {
            "type": value.get("type"),
            "model": value.get("model"),
            "credential_id": value.get("credential_id"),
        }
    return {"type": None, "model": value, "credential_id": None}


def _can_merge_text_block_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Return whether two event summaries are adjacent text deltas."""
    if (
        previous.get("type") != "TEXT_BLOCK_DELTA"
        or current.get("type") != "TEXT_BLOCK_DELTA"
    ):
        return False
    for key in ("reply_id", "block_id"):
        if previous.get(key) != current.get(key):
            return False
    return True


def _merge_text_block_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> None:
    """Merge a text delta summary into the previous adjacent event."""
    if isinstance(previous.get("delta"), str) and isinstance(
        current.get("delta"),
        str,
    ):
        merged_delta, truncated = _truncate(
            previous["delta"] + current["delta"],
        )
        previous["delta"] = merged_delta
        if previous.get("delta_truncated") or current.get("delta_truncated"):
            truncated = True
        if truncated:
            previous["delta_truncated"] = True
    previous["merged_count"] = int(previous.get("merged_count") or 1) + 1


class ExecutionTraceRecorder:
    """Mutable trace recorder used by :class:`ChatService`."""

    def __init__(
        self,
        storage: StorageBase,
        trace: ExecutionTraceRecord,
    ) -> None:
        self._storage = storage
        self.trace = trace
        self._started_perf = perf_counter()
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._saw_interrupted_reply_end = False

    async def start(self) -> None:
        """Persist the initial running trace."""
        now = datetime.now()
        self.trace.started_at = now
        self.trace.status = "running"
        await self.persist()

    async def persist(self) -> None:
        """Persist the current trace snapshot."""
        await self._storage.upsert_execution_trace(
            self.trace.user_id,
            self.trace.session_id,
            self.trace,
        )

    async def safe_persist(self, logger: Any) -> None:
        """Persist diagnostics without breaking the chat run."""
        try:
            await self.persist()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed to persist execution trace %s: %s",
                self.trace.id,
                str(exc),
            )

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[None]:
        """Record a timed execution stage."""
        entry = self.begin_stage(name)
        try:
            yield
        except Exception as exc:
            self.complete_stage(entry, "error", exc)
            self.finish("error", exc)
            try:
                await self.persist()
            except Exception:  # pylint: disable=broad-except
                pass
            raise
        else:
            self.complete_stage(entry)

    def begin_stage(self, name: str) -> dict[str, Any]:
        """Start a timed execution stage and return its mutable entry."""
        entry: dict[str, Any] = {
            "name": name,
            "status": "running",
            "started_at": _utc_like_now(),
            "_started_perf": perf_counter(),
        }
        self.trace.stages.append(entry)
        return entry

    def complete_stage(
        self,
        entry: dict[str, Any],
        status: str = "completed",
        error: BaseException | None = None,
    ) -> None:
        """Complete a timed execution stage."""
        started_perf = entry.pop("_started_perf", perf_counter())
        entry["status"] = status
        if error is not None:
            entry["error"] = summarize_error(error)
        entry["finished_at"] = _utc_like_now()
        entry["duration_ms"] = int((perf_counter() - started_perf) * 1000)

    def set_models(
        self,
        model: ChatModelConfig | dict[str, Any] | str | None,
        fallback_model: ChatModelConfig | dict[str, Any] | str | None,
    ) -> None:
        """Attach model summaries from session config."""
        self.trace.model = _model_summary(model)
        self.trace.fallback_model = _model_summary(fallback_model)

    def record_event(self, event: AgentEvent | BaseModel | dict[str, Any]) -> None:
        """Record a bounded event summary and aggregate useful fields."""
        summary = summarize_event(event)
        if self.trace.events and _can_merge_text_block_delta(
            self.trace.events[-1],
            summary,
        ):
            _merge_text_block_delta(self.trace.events[-1], summary)
        elif len(self.trace.events) < MAX_EVENT_SUMMARIES:
            self.trace.events.append(summary)
        elif len(self.trace.events) == MAX_EVENT_SUMMARIES:
            self.trace.events.append({"truncated": True})

        reply_id = summary.get("reply_id")
        if isinstance(reply_id, str):
            self.trace.reply_id = reply_id

        event_type = summary.get("type")
        if event_type == "MODEL_CALL_START" and summary.get("model_name"):
            current = self.trace.model or {}
            if not isinstance(current, dict):
                current = {}
            current.setdefault("type", None)
            current["model"] = summary["model_name"]
            current.setdefault("credential_id", None)
            self.trace.model = current
        elif event_type == "MODEL_CALL_END":
            usage = self.trace.usage or {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            usage["input_tokens"] += int(summary.get("input_tokens") or 0)
            usage["output_tokens"] += int(summary.get("output_tokens") or 0)
            usage["total_tokens"] = (
                usage["input_tokens"] + usage["output_tokens"]
            )
            self.trace.usage = usage
        elif event_type == "REPLY_END":
            self._saw_interrupted_reply_end = (
                summary.get("finished_reason") == "interrupted"
            )

        self._record_tool_call_summary(summary)

    def record_publish_error(self, error: BaseException) -> None:
        """Record an event publish/project error stage."""
        self.trace.stages.append(
            {
                "name": "event publish errors",
                "status": "error",
                "started_at": _utc_like_now(),
                "finished_at": _utc_like_now(),
                "duration_ms": 0,
                "error": summarize_error(error),
            },
        )

    def finish(
        self,
        status: str = "completed",
        error: BaseException | None = None,
    ) -> None:
        """Finalize the trace status and duration."""
        if status == "completed" and self._saw_interrupted_reply_end:
            status = "interrupted"
        stage_status = "completed" if status == "completed" else status
        for stage in self.trace.stages:
            if stage.get("status") == "running":
                self.complete_stage(stage, stage_status, error)
        self.trace.status = status  # type: ignore[assignment]
        self.trace.finished_at = datetime.now()
        self.trace.duration_ms = int((perf_counter() - self._started_perf) * 1000)
        if error is not None:
            self.trace.error = summarize_error(error)
        self.trace.tool_calls = list(self._tool_calls.values())

    def _record_tool_call_summary(self, summary: dict[str, Any]) -> None:
        """Update aggregate tool-call state from an event summary."""
        tool_call_id = summary.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            for tool_call in summary.get("tool_calls", []):
                if isinstance(tool_call, dict) and "id" in tool_call:
                    entry = self._tool_calls.setdefault(
                        tool_call["id"],
                        {"id": tool_call["id"]},
                    )
                    if tool_call.get("name"):
                        entry["name"] = tool_call["name"]
                    if tool_call.get("state"):
                        entry["state"] = tool_call["state"]
                    if tool_call.get("input"):
                        entry["input_preview"] = _truncate(
                            str(tool_call["input"]),
                        )[0]
            return
        if len(self._tool_calls) >= MAX_TOOL_CALLS:
            return
        entry = self._tool_calls.setdefault(tool_call_id, {"id": tool_call_id})
        event_type = summary.get("type")
        if summary.get("tool_call_name"):
            entry["name"] = summary["tool_call_name"]
        if summary.get("state"):
            entry["state"] = summary["state"]
        if event_type in {"TOOL_CALL_START", "TOOL_RESULT_START"}:
            entry.setdefault("started_at", summary.get("created_at"))
        if summary.get("delta"):
            target = (
                "result_preview"
                if event_type == "TOOL_RESULT_TEXT_DELTA"
                else "input_preview"
            )
            previous = entry.get(target, "")
            merged, truncated = _truncate(previous + summary["delta"])
            entry[target] = merged
            if truncated:
                entry[f"{target}_truncated"] = True
        if event_type == "TOOL_RESULT_END":
            entry["finished_at"] = summary.get("created_at")
            started_at = entry.get("started_at")
            finished_at = entry.get("finished_at")
            if isinstance(started_at, str) and isinstance(finished_at, str):
                try:
                    started = datetime.fromisoformat(started_at)
                    finished = datetime.fromisoformat(finished_at)
                    entry["duration_ms"] = int(
                        (finished - started).total_seconds() * 1000,
                    )
                except ValueError:
                    entry["duration_ms"] = None
            else:
                entry["duration_ms"] = None
            if summary.get("state") == "error":
                entry["error"] = summary.get("metadata") or {
                    "message": "Tool result ended with error state.",
                }
            else:
                entry.setdefault("error", None)
