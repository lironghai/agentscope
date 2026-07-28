# Agent Service Java Builder Design Migration Plan

> **For agentic workers:** execute one phase at a time. Each phase must add or update focused tests before production code. Prefer `examples/agent_service` and frontend changes first; touch `src/agentscope/app` only for additive Agent Service control-plane hooks, and avoid AgentScope core runtime changes unless explicitly called out.

**Goal:** Incrementally migrate selected mature Java Builder service patterns into the Python Agent Service control plane used by the example: Agent workspace norm, tools/MCP config model, share ACL model, subagent registry, and PostgreSQL hot-path table completion.

**Source spec:** `docs/superpowers/specs/2026-07-27-agent-service-java-builder-design-migration.md`

**Architecture:** Python Agent Service remains the orchestration boundary. Java Builder is used as design evidence, not as a runtime dependency. PostgreSQL becomes the durable service store for audited hot Agent/session/config paths; filesystem workspace remains the materialization target. Chat sends must consume prepared workspace state and must not call SkillHub or install bindings.

---

## Phase 1: Agent Workspace Service and Scaffold

**Goal:** Replace the temporary `__agent__` sentinel usage with an explicit Agent workspace service while keeping current behavior compatible.

**Files likely touched:**

- `src/agentscope/app/_service/_agent_workspace.py`
- `src/agentscope/app/_router/_agent.py`
- `src/agentscope/app/_router/_workspace.py`
- `examples/agent_service/main.py`
- `tests/agent_workspace_service_test.py`
- `tests/agent_binding_runtime_resolution_test.py`

**Why `src/agentscope/app` is needed:** Agent workspace resolution, Agent create/update materialization, session creation workspace ids, and workspace routers are implemented in the reusable Agent Service control plane rather than in `examples/agent_service/main.py`. Phase 1 must add service hooks there so the example service can wire them without custom route forks.

**Tasks:**

- [x] Add `AgentWorkspaceService` that resolves `(owner_id, agent_id)` to an Agent workspace.
- [x] Add write-if-missing scaffold for `AGENTS.md`, `tools.json`, `skills/`, `subagents/`, and `memory/`.
- [x] Move Agent create/update binding materialization to use `AgentWorkspaceService`.
- [x] Implement the single session/workspace strategy: session creation stores the deterministic Agent workspace id on ordinary sessions.
- [x] Add sessionless workspace listing endpoints for Agent workspace files.
- [x] Keep session workspace endpoints working for existing sessions.
- [x] Add tests proving create/update can materialize without a session and chat send does not materialize.

**Acceptance:**

- [x] New Agent workspace can be created and listed immediately after Agent creation.
- [x] Existing session workspace APIs still pass.
- [x] Sessionless Agent workspace APIs do not resolve to the service source checkout/runtime package/Codex task files.

**Verification completed:**

- Targeted TDD and HTTP smoke: `tests/agent_workspace_service_test.py`, `tests/agent_binding_runtime_resolution_test.py`.
- Legacy workspace endpoint coverage: `tests/workspace_skill_upload_router_test.py`.
- Chat workspace-owner coverage: `tests/execution_diagnostics_test.py::TestChatServiceExecutionDiagnostics::test_chat_service_uses_session_workspace_owner`.
- Storage compatibility: `tests/postgres_agent_service_wiring_test.py`, `tests/storage_redis_test.py`.

---

## Phase 2: Tools/MCP Config Model

**Goal:** Make Agent-level built-in tool and MCP configuration first-class service data and project it to workspace `tools.json`.

**Files likely touched:**

- `src/agentscope/app/storage/_model/_agent.py`
- `src/agentscope/app/storage/_base.py`
- `src/agentscope/app/storage/_postgres_storage.py`
- `src/agentscope/app/_router/_agent_tools.py` or `src/agentscope/app/_router/_workspace.py`
- `src/agentscope/app/_service/_agent_binding.py`
- `examples/web_ui/frontend/src/components/dialog/EditAgentDialog.tsx`
- `tests/agent_tool_config_test.py`
- `tests/postgres_agent_service_wiring_test.py`

**Why `src/agentscope/app` is needed:** Agent tool/MCP data models, storage contracts, binding materialization, and API endpoints live in the Agent Service control plane. `examples/agent_service` can only configure the app; it cannot persist or expose these fields without additive app-layer hooks.

**Tasks:**

- [x] Add Agent tool config model: built-in allow/deny and optional defaults.
- [x] Normalize `agent_mcp_bindings` into the same config projection used by workspace `tools.json`.
- [x] Add sessionless APIs for Agent tool/MCP config read/update.
- [x] Materialize tool/MCP config to `tools.json` during Agent create/update/config change.
- [x] Invalidate or refresh service-layer workspace/toolkit caches after config changes where current service supports it; do not modify `Toolkit` internals.
- [x] Add only the minimum frontend wiring needed for Agent edit/create dialogs to call the Agent-level APIs; leave panel consolidation to Phase 6.

**Acceptance:**

- [x] Built-in tool allow/deny updates persist and are reflected in `tools.json`.
- [x] SkillHub and custom MCP selections persist and are reflected in `tools.json`.
- [x] Existing chat/toolkit assembly sees the prepared workspace config through session workspace resolution, without calling SkillHub.

**Verification completed:**

- TDD coverage for Agent tool config projection/API/runtime filtering: `tests/agent_tool_config_test.py`.
- Same-name MCP update refreshes workspace runtime truth: `tests/agent_binding_runtime_resolution_test.py::test_binding_resolver_replaces_changed_mcp_with_same_name`.
- Binding resolution preflights all pending bindings and rolls back workspace writes on materialization/reconcile failures.
- Agent create/update writes `tools.json` before storage upsert so projection failures do not persist ahead of the workspace.
- Read-only shared viewers receive masked MCP bindings and an empty `tools_json.mcpServers`.
- Workspace scaffold/projection compatibility: `tests/agent_workspace_service_test.py`.
- Toolkit assembly regression with allow/deny service-layer filtering: `tests/service_toolkit_test.py`.
- PostgreSQL dedicated `tool_config_json` column/list reconstruction: `tests/postgres_agent_service_wiring_test.py`.
- Frontend type/build validation for Agent create/edit tool config wiring: `npm run build` in `examples/web_ui/frontend`.

**Boundary notes:**

- Python runtime MCP truth remains the workspace `.mcp` materialized by `AgentBindingResolver`; `tools.json.mcpServers` is the Agent-level projection for UI/config parity.
- `tools.json` uses a strict MCP config allow-list and omits inline secret-bearing fields. Runtime credentials must stay in server-side binding resolution or future credential references.
- `tool_config.defaults` is persisted and projected metadata in this phase; runtime enforcement is limited to allow/deny filtering.
- Chat send still does not call SkillHub. If a workspace is manually wiped after Agent creation/update, a later recovery/version-stamp refresh is deferred to the workspace hardening workstream.

---

## Phase 3: Share ACL Grants

**Goal:** Replace boolean-only Agent sharing behavior with a tiered ACL grant model while preserving existing shared-agent behavior.

**Files likely touched:**

- `src/agentscope/app/access/_policy.py`
- `src/agentscope/app/_service/_access.py`
- `src/agentscope/app/storage/_model/_agent.py`
- `src/agentscope/app/storage/_base.py`
- `src/agentscope/app/storage/_postgres_storage.py`
- `src/agentscope/app/_router/_agent.py`
- `examples/web_ui/frontend/src/components/dialog/EditAgentDialog.tsx`
- `tests/agent_router_share_config_test.py`
- `tests/agent_service_shared_agents_test.py`
- `tests/agent_acl_grants_test.py`

**Why `src/agentscope/app` is needed:** Resource visibility, edit resolution, storage records, and Agent routers already live in the Agent Service access/storage layer. Phase 3 extends that control-plane policy while preserving existing example-service wiring.

**Tasks:**

- [x] Add `AgentShareGrant` model with `grantee_type`, `grantee_id`, `tier`, `created_at`, and `created_by`.
- [x] Add PostgreSQL `agent_share_grants` table with indexes by grantee and agent.
- [x] Map owner to `edit`.
- [x] Map legacy `share_config.shared=true` to a compatibility workspace-wide grant (`grantee_type="workspace"`, `grantee_id="*"`) during reads or migration.
- [x] Extend resource policy from read/edit toward clone/run/edit semantics for Agent resources.
- [x] Keep credentials and knowledge-base sharing behavior compatible unless explicitly migrated later.

**Acceptance:**

- [x] Owner can edit.
- [x] Direct user grant can run or edit according to tier.
- [x] Workspace-wide grant (`grantee_type="workspace"`, `grantee_id="*"`) applies to logged-in users.
- [x] Non-granted users receive 404/403 consistently with existing router behavior.
- [x] Shared views do not leak hidden credential or owner-only fields.

---

## Phase 4: Subagent Registry

**Goal:** Track subagent/team-worker runs separately from normal Agent/session lists. Java Builder proves the separate registry boundary; PostgreSQL durability is a Python service enhancement and should be justified by current observability/API needs.

**Files likely touched:**

- `src/agentscope/app/storage/_model/_subagent.py`
- `src/agentscope/app/storage/_base.py`
- `src/agentscope/app/storage/_postgres_storage.py`
- `src/agentscope/app/_tool/_agent_create.py` or current team/subagent tool modules
- `src/agentscope/app/_router/_subagent.py`
- `src/agentscope/app/_router/__init__.py`
- `examples/web_ui/frontend/src/components/panel/SubagentPanel.tsx`
- `tests/subagent_registry_test.py`

**Why `src/agentscope/app` is needed:** Team/subagent creation, session records, and normal Agent/session filtering are implemented in the Agent Service tools/storage/router layer. Phase 4 must first audit those paths and then add only the minimum service hook required for registry visibility.

**Tasks:**

- [x] Audit current team-worker creation and session filtering paths before adding new persistence.
- [x] Add a `SubagentRunRecord` service model if the audit shows derived session/team data is not enough.
- [x] Evaluate PostgreSQL `subagent_runs` and defer it because current observability needs are satisfied by the existing Team/session graph.
- [x] Skip registry write hooks in this phase because the durable table path was not selected.
- [x] Update status on start/completion/failure where hooks exist, or document why a read-only derived view is the safer first step.
- [x] Add list/detail endpoints for parent session/agent correlation.
- [x] Keep ordinary Agent/session lists filtered from team workers.

**Acceptance:**

- [x] Creating a subagent/team worker exposes registry metadata through either derived session/team data or a durable registry row.
- [x] Registry list shows parent/child ids, status, timestamps, and errors where the selected source can supply them.
- [x] Normal Agent sidebar does not show team worker Agents.

**Implementation note:**

- Selected a read-only derived registry for the first landing step. Existing `TeamRecord.session_id`, `TeamData.members`, and `SessionRecord.team_id` already provide the durable parent/child correlation; live status is derived from the session run lock plus persisted parked tool-call state. A separate PostgreSQL `subagent_runs` table is deferred until there is a proven need for independent audit history beyond the current team/session graph, because writing both would introduce new synchronization hooks and drift risk.
- Cross-owner team members are deliberately fail-closed in the registry: the row keeps correlation ids but does not dereference or return the foreign owner Agent/session metadata until an explicit cross-owner access contract is added.

**Verification completed:**

- TDD coverage for derived registry service and HTTP list/detail endpoints: `tests/subagent_registry_test.py`.
- Cross-owner non-leak regression: `tests/subagent_registry_test.py::SubagentRegistryServiceTest::test_registry_does_not_dereference_foreign_owner_members`.
- Related regression: `tests/storage_redis_test.py`, `tests/postgres_agent_service_wiring_test.py`, `tests/agent_workspace_service_test.py`, `tests/agent_tool_config_test.py`, `tests/agent_acl_grants_test.py`, `tests/agent_binding_runtime_resolution_test.py`.
- Compile check: `python -m compileall -q src/agentscope/app tests/subagent_registry_test.py`.

---

## Phase 5: PostgreSQL Hot-Path Gap Closure

**Goal:** Audit and close specific remaining hot-path gaps instead of doing a blanket storage rewrite.

**Files likely touched:**

- `src/agentscope/app/storage/_postgres_storage.py`
- `src/agentscope/app/storage/_base.py`
- `tests/postgres_agent_service_wiring_test.py`
- `tests/storage_redis_test.py`
- `tests/storage_postgres_*`

**Why `src/agentscope/app` is needed:** PostgreSQL storage, record models, and storage contracts are implemented in `src/agentscope/app/storage`. This phase is explicitly limited to audited storage hot-path gaps, not a blanket package rewrite.

**Tasks:**

- [x] Audit remaining hot paths that still parse `record_json` or large state/context payloads for list pages.
- [x] Add only the missing scalar columns, tables, or indexes needed for Agent/session/workspace/tool/share/subagent list views that are still slow or blob-dependent.
- [x] Keep `record_json` as compatibility snapshot for full detail reconstruction.
- [x] Prefer dedicated table joins for list endpoints where the audit shows a concrete benefit.
- [x] Add migration/backfill behavior through `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN IF NOT EXISTS`.
- [x] Document remaining KV/list/set usage and whether each is compatibility, message history, or still-to-migrate.

**Acceptance:**

- [x] Audited Agent list gaps use scalar columns and binding/share summaries without avoidable full record JSON parsing.
- [x] Audited session list gaps use scalar columns and summary JSON without loading full context.
- [x] Skill/MCP binding lists use binding tables.
- [x] Compatibility tests prove old records still load.

**Implementation note:**

- No new PostgreSQL schema was required in this phase. The existing dedicated `agents`, `sessions`, `agent_skill_bindings`, `agent_mcp_bindings`, and `agent_share_grants` tables already covered the audited hot-path fields; the concrete gap was reconstruction: Agent list rows wrote Skill/MCP bindings to dedicated tables but did not attach those rows back to `AgentRecord.data.skill_bindings` / `mcp_bindings`.
- `list_agent_records()` and `list_shared_agent_records()` now batch-load binding rows by `(user_id, agent_id)` from the dedicated Skill/MCP binding tables, without reading `record_json`.
- Skill/MCP binding rows now carry an explicit `position` column and are read with `ORDER BY position ASC, binding_id ASC`, so list reconstruction preserves the Agent's persisted binding order instead of relying on timestamp tie ordering.
- `ensure_schema()` backfills `position` from the compatibility `agents.record_json` snapshot for rows created before the `position` column existed, so upgrades preserve existing multi-binding order.
- `record_json` remains a compatibility snapshot for full-detail recovery/backfill. Session list rows continue to use scalar config/model/team/status fields plus `state_summary_json`, not full `state_json` or `record_json`.

**Remaining KV/list/set usage after audit:**

- `*_kv`: compatibility/full-detail records for resources not in the audited hot path yet, plus startup backfill source for dedicated Agent/session rows.
- `*_sets`: Redis-compatible secondary indexes for legacy resources and compatibility paths.
- `*_lists`: message histories and ordered execution/event-style lists; this is intentionally not migrated in this phase because message pagination/history has different storage and retention behavior than Agent/session list pages.
- Team/subagent registry remains derived from existing Team/session graph per Phase 4. A durable `subagent_runs` table is still deferred until independent audit history is required.

**Verification completed:**

- TDD red/green for Agent list binding reconstruction: `tests/postgres_agent_service_wiring_test.py::test_postgres_adapter_lists_agent_bindings_from_dedicated_rows`.
- Shared Agent list binding reconstruction: `tests/postgres_agent_service_wiring_test.py::test_postgres_adapter_lists_shared_agent_bindings_from_dedicated_rows`.
- Binding order preservation: `tests/postgres_agent_service_wiring_test.py::test_postgres_adapter_preserves_agent_binding_order_from_tables` and `tests/postgres_agent_service_wiring_test.py::test_postgres_adapter_preserves_shared_agent_binding_order_from_tables`.
- Upgrade backfill for pre-position binding rows: `tests/postgres_agent_service_wiring_test.py::test_postgres_adapter_backfills_binding_positions_from_record_json`.
- PostgreSQL storage wiring suite: `tests/postgres_agent_service_wiring_test.py`.
- Redis compatibility and related Agent storage/runtime regressions: `tests/storage_redis_test.py`, `tests/agent_tool_config_test.py`, `tests/agent_acl_grants_test.py`, `tests/agent_binding_runtime_resolution_test.py`, `tests/subagent_registry_test.py`.
- Compile check: `python -m compileall -q src/agentscope/app tests/postgres_agent_service_wiring_test.py`.

---

## Phase 6: UI Consolidation and Verification

**Goal:** Make the frontend reflect the new Agent-level design instead of session-only workspace configuration.

**Files likely touched:**

- `examples/web_ui/frontend/src/api/*`
- `examples/web_ui/frontend/src/components/dialog/AgentDialog.tsx`
- `examples/web_ui/frontend/src/components/dialog/EditAgentDialog.tsx`
- `examples/web_ui/frontend/src/components/panel/SkillPanel.tsx`
- `examples/web_ui/frontend/src/components/panel/McpPanel.tsx`
- `examples/web_ui/frontend/src/pages/chat/index.tsx`
- `examples/web_ui/frontend/src/pages/skills/index.tsx`
- `examples/web_ui/frontend/src/pages/mcps/index.tsx`

**Tasks:**

- [x] Make Agent edit dialog and right-side panels consistently show persisted Skill/MCP/tool config from Agent-level APIs.
- [x] Make right-side Skill/MCP panels render Agent workspace resources without duplication.
- [x] Keep global Skill/MCP catalog pages paginated and independent from Agent/session list loading.
- [x] Add visible errors for materialization failures without blocking unrelated resources.
- [x] Run frontend build and targeted browser checks.

**Acceptance:**

- [x] Creating Agent with selected Skill/MCP shows the same resources in the right panel.
- [x] Editing Agent add/remove syncs DB bindings and workspace files.
- [x] Switching Agents does not request stale sessions from the previous Agent.
- [x] Homepage/sidebar remains responsive with many Agents/sessions.

**Implementation note:**

- Right-side Skill/MCP panels now read the Agent workspace directly through sessionless workspace APIs. Session-specific workspace query parameters remain supported for compatibility, but write endpoints now enforce Agent edit permission in both sessionless and legacy `session_id` paths.
- Agent/session switches clear stale session and workspace panel state before fetching new data. Message history and chat trigger requests now accept abort signals so route changes can cancel old history/stream/send work instead of surfacing old-session errors.
- Workspace Skill/MCP hooks expose lists only when the loaded `(agent_id, session_id)` pair matches the current view, so a failed or slow request cannot show resources from the previous Agent.
- Panel de-duplication is limited to the runtime materialized resource names used by `AgentBindingResolver`, avoiding the earlier broad overlap match across display names, URLs, and snapshots.
- Skill zip upload and SkillHub downloaded packages now enforce archive size, entry count, per-entry size, total expanded bytes, and path traversal checks before copying into a workspace.

**Verification completed:**

- Backend permission/upload/sessionless workspace regression: `tests/agent_workspace_service_test.py`, `tests/workspace_skill_upload_router_test.py`.
- SkillHub package safety regression: `tests/agent_binding_runtime_resolution_test.py::test_binding_resolver_rejects_large_skillhub_download`.
- Phase backend regression suite: `tests/agent_workspace_service_test.py tests/workspace_skill_upload_router_test.py tests/agent_binding_runtime_resolution_test.py tests/agent_tool_config_test.py tests/agent_acl_grants_test.py tests/postgres_agent_service_wiring_test.py tests/subagent_registry_test.py --noconftest -q`.
- Compile check: `python -m compileall -q src/agentscope/app tests/agent_workspace_service_test.py tests/workspace_skill_upload_router_test.py`.
- Frontend production build: `npm run build` in `examples/web_ui/frontend`.
- Real HTTP business flow on local PostgreSQL/Redis service: create Agent with custom Skill/MCP, verify Agent workspace lists both, create Session, PATCH Agent to remove bindings, verify raw workspace Skill/MCP responses are `[]`.
- Real ACL flow: `run` shared user can create a Session but receives `403` when attempting legacy `session_id` workspace write.
- Real browser flow through Vite UI: switch from Agent A/session A to Agent B does not request old session under Agent B and shows no old-session error; sending then immediately switching also shows no old-session error; Skill/MCP right panels show materialized resources without duplicate "Agent config" entries.
- Real chat flow: create credential, Agent, and Session; trigger `/chat/`; poll persisted messages until assistant reply completes.

---

## Verification Matrix

- [x] Backend focused tests for each phase pass.
- [x] PostgreSQL storage tests pass with dedicated tables and compatibility snapshots.
- [x] Redis storage compatibility tests pass where Redis remains supported.
- [x] Frontend `npm run build` passes.
- [x] Manual smoke: create Agent, select Skill/MCP, edit add/remove, create session, send first message, switch Agent, inspect workspace panel.

## Rollout Notes

- Do not remove existing KV compatibility tables in the same phase that adds a dedicated table.
- Prefer additive API endpoints first, then migrate frontend callers.
- Treat Java Builder as design evidence only; if Python service needs a different shape for compatibility, document the divergence in the phase summary.
