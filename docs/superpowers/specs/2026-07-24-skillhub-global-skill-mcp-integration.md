# SkillHub Global Skill/MCP Integration Spec

## Problem

Agent Service currently exposes Skill and MCP management only through
workspace-level APIs tied to an agent/session runtime workspace. The Web UI has
no global Skill or MCP catalog, so users cannot browse centrally governed
SkillHub resources before configuring an agent. Agent-specific Skill/MCP
selection also lacks a dedicated persistence model.

## Goals

- Add global `Skill` and `MCP` entries under the Web UI knowledge area.
- Display SkillHub Skill and MCP catalog data in AgentScope without storing
  SkillHub catalog resources as AgentScope primary data.
- Keep SkillHub as the source of truth for catalog metadata, visibility, and
  governance.
- Keep AgentScope PostgreSQL as the source of truth for which Skill/MCP
  resources an Agent has selected.
- Avoid runtime chat latency caused by live SkillHub lookups on every toolkit
  assembly.
- Keep SkillHub credentials on the AgentScope backend only.

## Non-Goals

- Do not implement Skill/MCP execution in the global catalog pages.
- Do not replace the existing workspace Skill/MCP runtime APIs in this phase.
- Do not copy all SkillHub catalog records into AgentScope tables.
- Do not expose SkillHub API tokens to the browser.
- Do not implement SkillHub publish, review, runner, or sandbox workflows.

## Current Architecture Anchors

- The Web UI sidebar currently exposes chat, schedule, credential, knowledge,
  and setup routes.
- The Agent Service workspace router already manages runtime workspace Skill
  and MCP resources under `/workspace/skill` and `/workspace/mcp`.
- Toolkit assembly already consumes workspace Skill/MCP state. Agent-level
  catalog bindings should therefore resolve into the existing workspace/toolkit
  path instead of creating a parallel runtime path.
- SkillHub exposes read-oriented catalog APIs:
  - `GET /api/web/skills`
  - `GET /api/web/mcp/servers`
  - `GET /api/web/mcp/internal-servers`

## Proposed Architecture

AgentScope adds a read-only SkillHub catalog adapter:

1. The frontend calls AgentScope endpoints only.
2. AgentScope backend calls SkillHub with a configured server-side token.
3. AgentScope returns a stable catalog response envelope and preserves original
   SkillHub item fields for display.
4. Agent-specific selections are stored later in dedicated PostgreSQL binding
   tables.
5. Runtime session/toolkit setup resolves enabled bindings and materializes
   them into the existing workspace Skill/MCP model.

## SkillHub Authentication

The recommended first-stage authentication mode is a backend service token:

- Configure `SKILLHUB_BASE_URL` on the AgentScope backend.
- Configure `SKILLHUB_SERVICE_TOKEN` on the AgentScope backend.
- Send `Authorization: Bearer <token>` only from AgentScope backend to SkillHub.
- Prefer a read-only token scope for catalog reads.
- The Web UI may store a user's personal SkillHub token in local browser
  settings for the current user. Catalog requests send it to AgentScope as
  `X-SkillHub-Token`; AgentScope then uses it only for the upstream SkillHub
  catalog request.
- Per-user token precedence is: `X-SkillHub-Token` first, then
  `SKILLHUB_SERVICE_TOKEN`.
- Do not send SkillHub tokens on unrelated Agent/session/chat requests.

This is reasonable for phase 1 because the feature only displays a catalog and
does not execute resources. For production-grade secret handling, a later phase
should move personal SkillHub tokens into encrypted backend user settings or use
SSO/user identity propagation.

## Phase 1: Global Catalog Pages

### Backend

Add a SkillHub catalog router:

- `GET /global/skills`
- `GET /global/mcps/servers`
- `GET /global/mcps/internal-servers`

The router forwards query parameters to SkillHub, unwraps SkillHub
`ApiResponse.data` when present, maps upstream failures to clear HTTP errors,
and returns catalog data without mutating AgentScope storage.

### Frontend

Add:

- Sidebar buttons for `Skill` and `MCP` below `Knowledge`.
- Routes `/skills` and `/mcps`.
- Searchable, read-only list pages.
- Loading, empty, and error states.

### Acceptance Criteria

- Browser requests never include `SKILLHUB_SERVICE_TOKEN`.
- `/skills` displays SkillHub Skill results.
- `/mcps` displays SkillHub MCP server results.
- Missing `SKILLHUB_BASE_URL` returns a clear disabled-state error.
- SkillHub network/auth errors render as inline page errors.
- Homepage agent/session list loading is not coupled to SkillHub requests.

## Phase 2: Agent Binding Storage

Add dedicated PostgreSQL tables for Agent selections. Prefer separate tables:

### `agent_skill_bindings`

- `user_id`
- `agent_id`
- `binding_id`
- `source`
- `skillhub_skill_id`
- `namespace`
- `slug`
- `version`
- `display_name`
- `description`
- `enabled`
- `config_json`
- `snapshot_json`
- `created_at`
- `updated_at`

### `agent_mcp_bindings`

- `user_id`
- `agent_id`
- `binding_id`
- `source`
- `skillhub_server_id`
- `name`
- `provider`
- `auth_type`
- `transport`
- `endpoint_url`
- `enabled`
- `config_json`
- `snapshot_json`
- `created_at`
- `updated_at`

AgentScope stores the selected relationship and a display snapshot. SkillHub
remains the catalog source of truth.

## Phase 3: Runtime Resolution

When a session starts or a toolkit is assembled:

1. Load enabled Agent Skill/MCP bindings from PostgreSQL.
2. Resolve SkillHub references using pinned versions where possible.
3. Materialize resolved resources into the existing workspace Skill/MCP model.
4. Continue using the current toolkit assembly path.
5. Record resolution errors without blocking unrelated enabled resources.

## Risks

- A service token can show the robot account's visibility, not the end user's
  SkillHub visibility. Limit phase 1 to public/global curated resources or add
  user identity propagation before exposing private catalogs.
- Dynamic SkillHub calls during chat would add latency and availability
  coupling. Keep catalog browsing and runtime materialization separated.
- MCP auth configuration may require per-user secrets. Store those as AgentScope
  credentials, not inside binding snapshots.
