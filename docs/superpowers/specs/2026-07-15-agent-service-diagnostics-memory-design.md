# Agent Service Diagnostics and Long-Term Memory UI Design

Date: 2026-07-15

## Goal

Extend `examples/agent_service` and `examples/web_ui` with two operator-facing capabilities:

1. Message-level diagnostics: every assistant reply can expose execution timing, token usage, status, error, and step/tool event details in the UI.
2. Long-term memory browsing: the UI can preview and edit the memory files currently used by the Agent Service long-term memory middleware.

The scope is deliberately narrow. `examples/web_ui` already has the main workspace-style pages for agents, knowledge, credentials, models, prompts/configuration, MCP tools, and skills. This work does not rebuild those pages or add a broad Settings/Admin surface.

## Current Repo Baseline

- `examples/web_ui/frontend/src/pages/chat/ChatViewport.tsx` owns the active `(agentId, sessionId)` chat surface and already renders a dockable right panel via `PanelDock`.
- `examples/web_ui/frontend/src/components/chat/MessageBubble.tsx` already shows assistant reply duration and token usage when `Msg.usage` is populated.
- `examples/web_ui/frontend/src/hooks/useMessages.ts` streams `AgentEvent` objects over `/sessions/{session_id}/stream` and builds live messages client-side.
- `src/agentscope/app/_service/_chat.py` is the backend hot path for chat runs. It loads session/agent/workspace state, creates the agent, invokes middleware/toolkit/model execution, publishes events, and persists messages.
- `src/agentscope/app/storage/_base.py` and `_redis_storage.py` define the storage contract used by the service. The local PG store added for the sample follows this storage shape.
- The long-term memory wiring in `examples/agent_service/main.py` writes memory under the example workspace path, scoped by user, agent, and session.

## Non-Goals

- No Open WebUI clone or broad navigation rewrite.
- No new user/account system. The sample UI still uses the existing `X-User-ID` boundary.
- No production-grade RBAC for memory editing beyond current user/session ownership checks and path sandboxing.
- No custom memory semantic editor for ReMe internals. ReMe files are framework-managed and should be read-only in v1.
- No replacement for OpenTelemetry tracing. The diagnostics record is a UI-readable operational summary; existing tracing middleware can continue to serve external observability.

## Backend: Execution Diagnostics

### Data Model

Add a persisted `ExecutionTraceRecord` model with these fields:

- `id`: generated trace id.
- `user_id`, `agent_id`, `session_id`.
- `reply_id`: assistant reply id once known; nullable while the run is starting.
- `input_message_id`: user message id or event id that triggered the run.
- `status`: `running`, `completed`, `interrupted`, `error`.
- `created_at`, `started_at`, `finished_at`.
- `duration_ms`.
- `usage`: `{input_tokens, output_tokens, total_tokens}` when available.
- `model`: best-effort `{type, model, credential_id}` from the session config at run start.
- `fallback_model`: same shape, nullable.
- `error`: nullable short error string.
- `stages`: ordered list of `{name, started_at, finished_at, duration_ms, status, metadata?, error?}`.
- `events`: bounded ordered list of lightweight event summaries. Store event `type`, `created_at`, `reply_id`, and safe metadata only; avoid duplicating full large data blocks.
- `tool_calls`: ordered list of `{id, name, state, started_at?, finished_at?, duration_ms?, input_preview?, result_preview?, error?}`.

The record is intentionally denormalized so the UI can load one reply's diagnostics without reconstructing from the live SSE replay log.

### Storage Contract

Extend `StorageBase` with:

- `upsert_execution_trace(user_id, record) -> ExecutionTraceRecord`
- `get_execution_trace(user_id, session_id, trace_id) -> ExecutionTraceRecord | None`
- `get_execution_trace_by_reply(user_id, session_id, reply_id) -> ExecutionTraceRecord | None`
- `list_execution_traces(user_id, session_id, offset=0, limit=50) -> list[ExecutionTraceRecord]`
- `delete_execution_traces(user_id, session_id) -> None` for session delete cascade.

Implement the contract in `RedisStorage`; `PostgresStorage` should inherit or implement the same behavior according to its adapter capabilities. Index records by session and by reply id:

- `agentscope:user:{user_id}:session:{session_id}:execution_traces`
- `agentscope:user:{user_id}:session:{session_id}:execution_trace:{trace_id}`
- `agentscope:user:{user_id}:session:{session_id}:reply_trace:{reply_id}`

### Capture Points

Add a small `ExecutionTraceRecorder` helper in the app service layer. It should support:

- `start_trace(...)`
- `stage(name)` async context manager
- `record_event(event)`
- `record_tool_blocks(msg_or_event)`
- `finish(status, usage=None, error=None)`

Instrument `ChatService._run_impl` around stable service-level phases:

- session load and input persistence
- workspace resolution
- agent/team resolution
- long-term memory / extra middleware construction if visible at this layer
- RAG middleware construction
- toolkit construction, including MCP/skill loading
- agent reply event loop
- final message persistence
- state persistence
- session event publish errors as stage/event errors when they occur

The first implementation should prefer accurate coarse timings over fragile deep hooks. Tool/model details can be enriched from emitted events and final `AssistantMsg` content blocks.

### API

Add diagnostics endpoints under the existing session router:

- `GET /sessions/{session_id}/diagnostics?agent_id=...&offset=0&limit=50`
- `GET /sessions/{session_id}/diagnostics/by-reply/{reply_id}?agent_id=...`
- `GET /sessions/{session_id}/diagnostics/{trace_id}?agent_id=...`

All endpoints must verify that `storage.get_session(user_id, agent_id, session_id)` exists before returning records.

## Backend: Long-Term Memory Browser

### Service Boundary

Add a small `LongTermMemoryBrowser` service that is optional at app construction time. It should not hardcode `examples/agent_service` paths in shared router code. The example app configures the browser with the same root convention used by the long-term memory middleware:

`workspaces/{user_id}/{agent_id}/{session_id}/long_term_memory`

### API

Add endpoints under a memory router:

- `GET /sessions/{session_id}/memory/tree?agent_id=...&backend=agentic|reme|mem0`
- `GET /sessions/{session_id}/memory/file?agent_id=...&backend=...&path=...`
- `PUT /sessions/{session_id}/memory/file?agent_id=...&backend=...&path=...`

Response semantics:

- `agentic`: editable Markdown/text files.
- `reme`: readable files, read-only by default.
- `mem0`: status only unless local files exist; remote mem0 memories are not edited through this file browser.

Safety rules:

- Validate session ownership through storage before filesystem access.
- Resolve paths with `Path.resolve()` and reject any path escaping the configured memory root.
- Allow only regular files for read/write.
- Enforce a conservative maximum file size for preview/edit, for example 1 MiB.
- Restrict write extensions to `.md`, `.txt`, `.json`, `.yaml`, `.yml` for v1.
- Return `404` when the memory browser is not configured or the backend directory does not exist.

## Frontend: Diagnostics

### API Client

Add diagnostics types and methods to `examples/web_ui/frontend/src/api`:

- `sessionApi.diagnostics(sessionId, agentId, offset?, limit?)`
- `sessionApi.diagnosticByReply(sessionId, agentId, replyId)`
- `ExecutionTraceRecord`, `ExecutionStage`, `ExecutionToolCall`.

### Message UI

Extend `MessageBubble` with an optional diagnostics action for assistant messages:

- Keep the existing duration/token badge.
- Add a compact icon button next to the badge when a trace can be requested.
- Open `ExecutionTraceDrawer` or a side sheet showing:
  - status, duration, token usage
  - model/fallback model
  - ordered stages with duration and error markers
  - tool calls with name/state/duration and compact input/result previews
  - raw safe event summaries in a collapsible section

The drawer fetches by `reply_id` on demand. Missing traces should render a quiet empty state because old messages created before the feature will not have diagnostics.

## Frontend: Long-Term Memory

### Panel Integration

Extend `PanelKey` with `memory` and add a dock item in `ChatViewport`.

Use a `Brain` or `Files` lucide icon. The panel body should be dense and operational:

- backend selector: Agentic, ReMe, Mem0
- file tree/list
- preview/editor area
- Save button only when the selected file is editable and dirty
- read-only badge for ReMe/Mem0
- status/empty state for disabled or missing memory backends

### Editing

Use a plain textarea for v1. Markdown preview is optional; correctness and safe save behavior are more important than rich editing.

Save flow:

1. Fetch file content and metadata.
2. Mark dirty on local edits.
3. PUT the full file content.
4. Refetch metadata or update local saved state.

## Tests

Backend:

- Storage tests for trace CRUD and reply-id lookup.
- Router tests for diagnostics ownership and missing trace behavior.
- Memory browser tests for tree, file read, file write, read-only backend rejection, and path traversal rejection.
- A focused chat-service test or fake storage test verifying a run writes a trace with start/end and usage when available.

Frontend:

- TypeScript build must pass.
- Add component or hook tests only if the repo already has a lightweight test path available. Otherwise rely on `pnpm build` plus manual browser verification.

## Acceptance Criteria

- A new assistant reply in `examples/web_ui` can open diagnostics showing total time, token usage, coarse execution stages, and tool/event summaries.
- Old assistant replies without diagnostics render a clear "no diagnostics available" state.
- The Memory panel can browse the current session's `agentic` memory files and edit allowed text files.
- ReMe memory files can be viewed but not edited in v1.
- All memory filesystem endpoints are session-owned and path-sandboxed.
- Existing chat, MCP, Skill, Knowledge, Permission, and Task panels keep working.
