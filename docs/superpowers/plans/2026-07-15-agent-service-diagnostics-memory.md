# Agent Service Diagnostics and Memory UI Implementation Plan

Date: 2026-07-15

Spec: `docs/superpowers/specs/2026-07-15-agent-service-diagnostics-memory-design.md`

## Constraints

- Keep scope to Diagnostics and Long-term Memory UI.
- Preserve existing user changes and the existing PG/Redis/long-term-memory work already present in this worktree.
- Do not redesign existing Workspace pages.
- Use existing FastAPI router/dependency patterns and existing React/shadcn/lucide/PanelDock patterns.
- Run verification after each implementation block where practical.

## Task 1: Backend Execution Diagnostics

Implement persisted execution trace recording for chat runs.

Files to inspect first:

- `src/agentscope/app/_service/_chat.py`
- `src/agentscope/app/_service/_toolkit.py`
- `src/agentscope/app/storage/_base.py`
- `src/agentscope/app/storage/_redis_storage.py`
- `src/agentscope/app/storage/_model/`
- `src/agentscope/app/_router/_session.py`
- `src/agentscope/app/_router/_schema/`
- `tests/`

Required changes:

1. Add execution trace pydantic/storage models.
2. Extend `StorageBase` with trace CRUD methods.
3. Implement trace CRUD in `RedisStorage`; make sure the PG sample store can use the same contract.
4. Cascade-delete a session's traces when the session is deleted.
5. Add an `ExecutionTraceRecorder` helper that records coarse stages, event summaries, tool summaries, status, error, and usage.
6. Instrument `ChatService._run_impl` with stable service-level stages.
7. Add session diagnostics endpoints:
   - `GET /sessions/{session_id}/diagnostics`
   - `GET /sessions/{session_id}/diagnostics/by-reply/{reply_id}`
   - `GET /sessions/{session_id}/diagnostics/{trace_id}`
8. Add focused tests for storage CRUD and route ownership/missing-record behavior.

Verification:

- Run focused backend tests for the new diagnostics storage/router.
- Run `python -m py_compile` on changed Python modules through `uv run` with relevant extras.

## Task 2: Backend Long-Term Memory Browser API

Implement a safe optional file-browser API for the long-term memory directory used by the example Agent Service.

Files to inspect first:

- `examples/agent_service/main.py`
- `src/agentscope/app/_router/`
- `src/agentscope/app/deps.py`
- `src/agentscope/app/_app.py` or app creation entrypoints
- long-term memory middleware classes under `src/agentscope/memory` or `src/agentscope/middleware`

Required changes:

1. Add a `LongTermMemoryBrowser` service with a configured root resolver.
2. Add a dependency that returns the browser when configured, otherwise returns `None` or raises a controlled 404.
3. Add memory endpoints under session scope:
   - `GET /sessions/{session_id}/memory/tree`
   - `GET /sessions/{session_id}/memory/file`
   - `PUT /sessions/{session_id}/memory/file`
4. Validate session ownership before filesystem access.
5. Sandbox all paths under the configured backend directory.
6. Make `agentic` editable for allowed text files.
7. Make `reme` and `mem0` read-only in v1.
8. Configure `examples/agent_service/main.py` to expose the browser using the same `workspaces/{user}/{agent}/{session}/long_term_memory` root convention as the middleware.
9. Add tests for traversal rejection, missing backend, read-only write rejection, and successful Agentic memory edit.

Verification:

- Run focused backend memory API tests.
- Manually exercise against a local `examples/agent_service/workspaces/.../long_term_memory` fixture if available.

## Task 3: Frontend Diagnostics and Memory Panel

Add UI surfaces in `examples/web_ui` for diagnostics and memory browsing/editing.

Files to inspect first:

- `examples/web_ui/frontend/src/api/`
- `examples/web_ui/frontend/src/pages/chat/ChatViewport.tsx`
- `examples/web_ui/frontend/src/components/chat/MessageBubble.tsx`
- `examples/web_ui/frontend/src/components/chat/ChatContent.tsx`
- `examples/web_ui/frontend/src/components/panel/PanelDock.tsx`
- `examples/web_ui/frontend/src/components/ui/`
- `examples/web_ui/frontend/src/i18n/locales/en.json`
- `examples/web_ui/frontend/src/i18n/locales/zh.json`

Required changes:

1. Add frontend API types and client functions for diagnostics and memory endpoints.
2. Add a diagnostics trigger to assistant message footers.
3. Add an `ExecutionTraceDrawer` or equivalent dialog/sheet with summary, stages, tool calls, and raw event summaries.
4. Extend `PanelKey` and `ChatViewport` with a Memory dock panel.
5. Add `LongTermMemoryPanel` with backend selector, file tree/list, preview/editor, read-only state, dirty state, and save.
6. Add English/Chinese labels for new UI text.
7. Keep all controls compact and consistent with the current operational UI.

Verification:

- Run frontend lint/typecheck/build according to the repo scripts.
- Start the UI if needed and manually check:
  - Diagnostics drawer opens from an assistant message.
  - Missing diagnostics state is understandable.
  - Memory panel opens, loads tree, reads file, and saves editable Agentic file.
  - ReMe files show read-only state.

## Task 4: Integration Verification and Review

Run end-to-end verification after Tasks 1-3.

Required checks:

1. Backend focused tests.
2. Frontend build/typecheck.
3. Start or reuse local Agent Service and Web UI.
4. Create or reuse an agent/session.
5. Send a message.
6. Confirm the message diagnostics API returns a trace for the assistant reply.
7. Confirm the UI opens diagnostics from the assistant message.
8. Confirm the Memory panel can browse current session memory files.
9. Review code for scope creep, path traversal risk, oversized event payloads, and broken existing panels.

Subagent workflow:

- Dispatch one implementer subagent per task.
- After each implementer returns, dispatch a spec-compliance reviewer.
- Only after spec compliance passes, dispatch a code-quality reviewer.
- If a reviewer finds issues, send the task back for fixes and re-review.
- The controller remains responsible for final architecture decisions, verification, and user-facing summary.
