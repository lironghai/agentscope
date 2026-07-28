# SkillHub Global Skill/MCP Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only global Skill and MCP catalog pages backed by SkillHub, without changing Agent runtime binding behavior.

**Architecture:** AgentScope backend exposes a small `/global` catalog proxy that calls SkillHub with a backend-only token and unwraps the SkillHub response envelope. The frontend adds `/skills` and `/mcps` pages plus sidebar entries under Knowledge, using the AgentScope API only. Agent selection persistence remains a later phase.

**Tech Stack:** FastAPI, httpx, Pydantic, React, TypeScript, Vite, lucide-react, existing shadcn UI components.

---

### Task 1: Backend Tests for SkillHub Catalog Proxy

**Files:**
- Create: `tests/skillhub_catalog_router_test.py`

- [x] Write tests proving:
  - `/global/skills` forwards `q`, `namespace`, `label`, `sort`, `page`, and `size`.
  - `/global/mcps/servers` forwards `search`, `category`, `auth_type`, `provider`, `tags`, `page`, and `size`.
  - `/global/mcps/internal-servers` forwards `search`, `page`, and `size`.
  - The backend adds `Authorization: Bearer <token>` when configured.
  - Missing SkillHub configuration returns HTTP 503.
  - SkillHub non-2xx responses map to HTTP 502 with a readable detail.

- [x] Run:

```powershell
$env:PYTHONPATH='.;src;tests'; uv run pytest tests/skillhub_catalog_router_test.py -q
```

Expected: fail because the router/client do not exist yet.

### Task 2: Backend SkillHub Catalog Client and Router

**Files:**
- Create: `src/agentscope/app/_service/_skillhub_catalog.py`
- Create: `src/agentscope/app/_router/_skillhub_catalog.py`
- Create: `src/agentscope/app/_router/_schema/_skillhub_catalog.py`
- Modify: `src/agentscope/app/_router/_schema/__init__.py`
- Modify: `src/agentscope/app/_router/__init__.py`
- Modify: `src/agentscope/app/_app.py`
- Modify: `examples/agent_service/main.py`

- [x] Add `SkillHubCatalogClient` with:
  - `base_url`
  - optional `service_token`
  - request timeout
  - `list_skills(params)`
  - `list_mcp_servers(params)`
  - `list_internal_mcp_servers(params)`

- [x] Add response schemas:
  - `SkillHubCatalogResponse`
  - `SkillHubCatalogConfigResponse`

- [x] Add `/global` router methods.

- [x] Add `skillhub_catalog_client` to app state through `create_app`.

- [x] Wire `examples/agent_service/main.py` from:
  - `SKILLHUB_BASE_URL`
  - `SKILLHUB_SERVICE_TOKEN`
  - `SKILLHUB_TIMEOUT`

- [x] Run the focused backend test again.

Expected: pass.

### Task 3: Frontend API and Types

**Files:**
- Modify: `examples/web_ui/frontend/src/api/types.ts`
- Create: `examples/web_ui/frontend/src/api/globalCatalog.ts`
- Modify: `examples/web_ui/frontend/src/api/index.ts`

- [x] Add catalog response and item types that preserve raw SkillHub fields.

- [x] Add API methods:
  - `globalCatalogApi.listSkills`
  - `globalCatalogApi.listMcpServers`
  - `globalCatalogApi.listInternalMcpServers`

### Task 4: Sidebar and Routes

**Files:**
- Modify: `examples/web_ui/frontend/src/components/layout/AppSidebar.tsx`
- Modify: `examples/web_ui/frontend/src/App.tsx`

- [x] Add `Skill` sidebar entry below Knowledge.
- [x] Add `MCP` sidebar entry below Skill.
- [x] Add routes:
  - `/skills`
  - `/mcps`

### Task 5: Global Catalog Pages

**Files:**
- Create: `examples/web_ui/frontend/src/pages/skills/index.tsx`
- Create: `examples/web_ui/frontend/src/pages/mcps/index.tsx`

- [x] Add searchable Skill list with loading, empty, and error states.
- [x] Add searchable MCP list with tabs for public catalog and internal servers.
- [x] Do not add selection/binding actions in phase 1.

### Task 6: i18n

**Files:**
- Inspect and modify current frontend i18n resources under `examples/web_ui/frontend/src/i18n`.

- [x] Add Chinese and English labels for:
  - Skill
  - MCP
  - Search
  - Empty catalog
  - SkillHub disabled/error states

### Task 7: Verification

**Files:**
- Test: `tests/skillhub_catalog_router_test.py`
- Build: `examples/web_ui/frontend`

- [x] Run:

```powershell
$env:PYTHONPATH='.;src;tests'; uv run pytest tests/skillhub_catalog_router_test.py -q
```

Expected: pass.

- [x] Run:

```powershell
npm run build
```

from `examples/web_ui/frontend`.

Expected: TypeScript and Vite build pass.

- [x] Start backend and frontend only after build/test pass.
