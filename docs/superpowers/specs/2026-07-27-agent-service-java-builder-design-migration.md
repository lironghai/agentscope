# Agent Service Java Builder Design Migration Spec

**Created:** 2026-07-27
**Ambiguity score:** 0.14
**Requirements:** 6 locked

## Goal

Migrate the mature AgentScope Java Builder service design into the Python Agent Service control plane used by the example service in staged, testable slices, while keeping AgentScope core runtime changes minimal.

## Background

The Python Agent Service has already moved part of Agent persistence and Skill/MCP binding storage away from Redis-compatible KV into PostgreSQL-oriented tables. It also now materializes selected Skill/MCP bindings into an Agent-level workspace during Agent create/update rather than during chat send.

The Java Builder example separates several concerns more cleanly:

- Agent definitions are lightweight service records with scalar columns plus bounded JSON config lists.
- Workspace files are per-Agent data, not source-code checkout directories.
- `tools.json` supports built-in tool allow/deny lists and optional `mcpServers`.
- Agent ACL is expressed as grants with ordered tiers such as `CLONE`, `RUN`, and `EDIT`.
- Subagent run metadata is tracked separately through a registry.
- PostgreSQL/JPA persistence uses dedicated tables for Agent records and Agent share grants.

The migration should borrow these service-layer patterns without porting Java classes directly and without embedding Java Builder assumptions into the Python core runtime. Changes to `src/agentscope/app` are allowed only as additive Agent Service control-plane hooks; changes to core `Agent`, `Toolkit`, `WorkspaceBase`, model provider, or workspace runtime internals require separate justification.

## Requirements

1. **Agent workspace norm**: Python Agent Service must expose a stable Agent-level workspace abstraction independent of session creation.
   - Current: Most workspace APIs are session-shaped, and the new Agent binding path uses an internal `__agent__` sentinel to reuse the existing workspace manager API.
   - Target: Agent Service has an explicit Agent workspace resolver/scaffolder that maps `(owner_id, agent_id)` to a predictable workspace root and standard directories.
   - Acceptance: Creating or editing an Agent can resolve and update its workspace without an existing session; new sessions point to or resolve through the Agent workspace; chat send does not perform Skill/MCP materialization.

2. **Workspace scaffold**: Fresh Agent workspaces must use a small standard layout inspired by Java Builder.
   - Current: Python local workspaces are runtime workspaces, and uploaded/installed Skill/MCP resources are not governed by a documented Agent workspace layout.
   - Target: Agent workspace contains `AGENTS.md`, `tools.json`, `skills/`, `subagents/`, and `memory/`, created with write-if-missing semantics.
   - Acceptance: Re-running scaffold on an existing workspace preserves user files; sessionless Agent workspace APIs resolve only Agent workspace content, not the service checkout or runtime jar/package artifacts.

3. **Tools/MCP config model**: Built-in tools and MCP server configuration must become Agent-level service data with a workspace `tools.json` projection.
   - Current: MCP binding tables exist, but built-in tool allow/deny settings and live workspace `tools.json` semantics are not unified with Agent create/edit.
   - Target: Agent service stores built-in tool config and MCP config in dedicated fields/tables, then projects them into workspace `tools.json`.
   - Acceptance: The UI can read/update Agent tool config without a session id; session creation or workspace resolution makes the Agent workspace projection available to existing chat/toolkit assembly without teaching chat paths about SkillHub.

4. **Share ACL model**: Sharing must evolve from boolean shared/read-only behavior toward tiered ACL grants.
   - Current: Python `ShareConfig.shared` and resource refs provide a basic shared-agent path with editable/read-only projection.
   - Target: Agent sharing is represented by grant rows with grantee type/id, tier, created metadata, and policy logic mapping tiers to list/run/edit/clone capabilities.
   - Acceptance: Owner access, direct user grants, workspace/global grants, read/run/edit boundaries, and shared view masking are covered by focused tests.

5. **Subagent registry**: Subagent/team-worker lifecycle should have a registry separate from ordinary Agent/session lists.
   - Current: Python has team-worker Agents and sessions, but they are filtered in list views and do not have a first-class registry for correlation/status.
   - Target: Subagent runs have a dedicated registry boundary with parent requester, child session/agent, status, timestamps, result summary, and error fields. A durable PostgreSQL table is a Python-side enhancement, not a direct Java requirement.
   - Acceptance: Creating team/subagent work records or exposes registry metadata; list/history/debug APIs can show child runs without polluting normal Agent/session lists.

6. **PostgreSQL hot-path completion**: Remaining hot service reads must be audited and moved to queryable PostgreSQL columns/tables only where the current implementation still depends on large Redis-style values or `record_json`.
   - Current: Agent/session/binding tables already exist for several hot paths, while legacy KV/list/set tables remain as compatibility infrastructure and some endpoints may still rely on `record_json`.
   - Target: Identified gaps for tool config, share ACL, subagent registry, workspace metadata, and any still-slow list views have dedicated tables/indexes. `record_json` remains as compatibility snapshot where needed.
   - Acceptance: Documented hot list paths use narrow rows where necessary and do not perform avoidable full state/context JSON parsing.

## Boundaries

**In scope:**

- Primary: Python `examples/agent_service` wiring and `examples/web_ui/frontend` pages/dialogs that call the example service.
- Allowed when required: additive Python `src/agentscope/app` service/router/storage/access hooks that make the example Agent Service capable of the target behavior.
- PostgreSQL schema additions in `PostgresStorage`, with Redis-compatible fallback behavior kept where needed.
- Focused tests for storage, ACL, workspace, tool/MCP config, and subagent registry behavior.

**Out of scope:**

- Direct Java class ports or Java runtime dependency from the Python service.
- Rewriting AgentScope core `Agent`, `Toolkit`, `WorkspaceBase`, workspace runtime implementations, or model provider internals unless a small adapter hook is unavoidable and documented.
- Replacing SkillHub as the catalog source of truth.
- Full enterprise SSO/organization membership implementation in the first pass.
- Removing legacy KV/list/set PostgreSQL compatibility tables before all current storage callers are migrated.

## Constraints

- Upstream merge safety: prefer additive service-layer modules and schema-compatible fields over invasive runtime refactors.
- Package-source safety: every phase that touches `src/agentscope/app` must state why `examples/agent_service` alone cannot carry that change.
- Backward compatibility: existing Agent/session records must continue to load from `record_json` during migration.
- Performance: list endpoints must avoid loading full session state/context or full Agent records when only display fields are needed.
- Security: SkillHub tokens and credentials stay backend-side; shared credentials/Agent internals must remain masked on shared views.
- Workspace safety: workspace writes must be scoped to the resolved Agent workspace root and use write-if-missing or explicit replace semantics.
- Rollout safety: each phase must have focused tests before code is considered complete.

## Proposed Target Shape

### Agent Workspace

Stable workspace identity:

- Owner: `user_id`
- Agent: `agent_id`
- Scope: `agent`
- Workspace id: deterministic from `(user_id, agent_id)` using the existing `PER_AGENT` workspace identity semantics.

Standard layout:

```text
<workspace-root>/<user-or-owner>/<agent-id>/
  AGENTS.md
  tools.json
  skills/
  subagents/
  memory/
```

The current `__agent__` sentinel remains a temporary compatibility bridge until `AgentWorkspaceService` owns this directly.

Session relationship:

- Agent create/update prepares the Agent workspace.
- Session creation stores the same Agent workspace id on ordinary sessions. This is the Phase 1 strategy; do not defer to an implicit resolver mapping unless a later phase documents why persisted session workspace ids cannot be reused.
- Chat send and toolkit assembly continue to use the already-resolved workspace and must not call SkillHub or re-install bindings.

### Tools/MCP Config

Service data should distinguish:

- Built-in tool allow/deny list.
- MCP server bindings selected from SkillHub.
- Custom MCP server definitions.
- Service projection to workspace `tools.json`.

Suggested tables:

- `agentscope_agent_tool_configs`
- `agentscope_agent_mcp_bindings` (already exists; extend/normalize)

### Share ACL

Suggested grant model:

- `owner_id`
- `agent_id`
- `grant_id`
- `grantee_type`: `user` or `workspace`
- `grantee_id`
- `tier`: `clone`, `run`, `edit`
- `created_at`
- `created_by`

Initial mapping:

- Owner: `edit`
- `edit`: update/delete/configure/run/clone
- `run`: list/get/run but no mutation
- `clone`: visible as clone source only
- Global/workspace-wide access is represented as `grantee_type="workspace"` with `grantee_id="*"`.
- Existing `share_config.shared=true`: migrate to or read as `grantee_type="workspace"`, `grantee_id="*"`, tier `run` unless Phase 3 deliberately chooses a stricter read-compatible tier and documents the compatibility effect.

### Subagent Registry

Java Builder proves the value of a separate registry boundary, but its own registry is in-process. Python may choose a durable table because the example service already exposes persistent sessions and PostgreSQL-backed state. Treat the table below as a Python design decision:

- `run_id`
- `owner_id`
- `requester_agent_id`
- `requester_session_id`
- `child_agent_id`
- `child_session_id`
- `status`: `pending`, `running`, `completed`, `failed`
- `created_at`
- `started_at`
- `completed_at`
- `result_summary`
- `error`
- `metadata_json`

### PostgreSQL Table Direction

Keep:

- Dedicated Agent/session/binding rows for hot display/query paths.
- `record_json` as a compatibility snapshot while APIs still reconstruct full Pydantic records.

Reduce over time:

- Dependence on `{prefix}_kv`, `{prefix}_sets`, and `{prefix}_lists` for Agent/session hot paths.
- Full JSON parse on list endpoints.

Do not remove:

- Compatibility KV/list/set tables until all storage methods and tests prove no current feature depends on them.

## Acceptance Criteria

- [ ] Agent create/update materializes Skill/MCP and tool config to an Agent workspace without requiring a session.
- [ ] Session creation and chat send do not perform Agent binding installation.
- [ ] Sessionless Agent workspace APIs never resolve to the service source checkout, runtime jar/package, or Codex task artifacts.
- [ ] Agent-level tools/MCP APIs support sessionless read/update and project to `tools.json`.
- [ ] Share ACL grants support owner, user, and workspace grants with tiered permission tests.
- [ ] Subagent registry records and exposes child runs without showing team workers as ordinary Agents.
- [ ] PostgreSQL list queries for audited hot paths use dedicated columns/indexes where needed and avoid full state/context JSON.
- [ ] All changes stay in example service/UI wiring or additive `src/agentscope/app` control-plane hooks unless a core hook is explicitly documented and tested.

## Edge Coverage

| Category | Requirement | Status | Resolution / Reason |
|----------|-------------|--------|---------------------|
| Backward compatibility | R1, R6 | covered | Keep compatibility snapshot/fallback until migration tests pass. |
| Security | R4 | covered | ACL grants and masked shared views are explicit acceptance criteria. |
| Performance | R6 | covered | Hot list paths must use narrow indexed rows. |
| Workspace safety | R1, R2, R3 | covered | Writes are scoped to Agent workspace and avoid runtime chat materialization. |
| Multi-tenant behavior | R4, R5 | covered | Owner/grantee/subagent relations are explicit fields. |

## Prohibitions

| Prohibition | Requirement | Status | Verification / Reason |
|-------------|-------------|--------|------------------------|
| Must not port Java implementation classes into Python. | All | resolved | Borrow service contracts and schema ideas only. |
| Must not make chat send depend on SkillHub availability. | R1, R3 | resolved | Materialization remains create/update/config-time. |
| Must not expose SkillHub tokens or raw shared credentials to the browser. | R3, R4 | resolved | Backend-only secret handling and masking remain required. |
| Must not remove KV compatibility tables before proving all callers migrated. | R6 | resolved | Explicit out-of-scope for early phases. |
| Must not modify AgentScope core runtime for service-only persistence improvements. | All | resolved | Core changes require separate justification. |
| Must not teach ChatService or Toolkit to call SkillHub. | R1, R3 | resolved | Agent/session workspace resolution provides prepared files before chat runs. |

## Ambiguity Report

| Dimension | Score | Min | Status | Notes |
|-----------|-------|-----|--------|-------|
| Goal Clarity | 0.91 | 0.75 | met | Clear target: migrate Java Builder service patterns into Python Agent Service control plane. |
| Boundary Clarity | 0.90 | 0.70 | met | Scope limited to example wiring/UI and additive app service hooks; core runtime avoided. |
| Constraint Clarity | 0.82 | 0.65 | met | Merge safety, compatibility, performance, and security constraints specified. |
| Acceptance Criteria | 0.84 | 0.70 | met | Phase-level and global pass/fail checks included. |
| **Ambiguity** | 0.14 | <=0.20 | met | Ready for phased implementation planning. |

## Interview Log

| Round | Perspective | Question summary | Decision locked |
|-------|-------------|------------------|-----------------|
| 1 | Researcher | What Java design is worth borrowing? | Workspace norm, tools/MCP config, ACL grants, subagent registry, JPA table separation. |
| 2 | Simplifier | What is the safest migration boundary? | Prefer example service/UI; allow additive `src/agentscope/app` control-plane hooks when necessary; do not modify core runtime. |
| 3 | Boundary Keeper | What should not be migrated directly? | Do not port Java classes or depend on Java runtime. |
| 4 | Failure Analyst | What would make this harmful? | Chat-time SkillHub dependency, unsafe workspace paths, large JSON list queries, or breaking upstream merges. |

---

*Spec created: 2026-07-27*
*Next step: implement phases from `docs/superpowers/plans/2026-07-27-agent-service-java-builder-design-migration.md`.*
