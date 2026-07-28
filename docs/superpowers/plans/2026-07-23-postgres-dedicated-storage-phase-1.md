# PostgreSQL Dedicated Storage Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Agent Service agent/session list reads off the Redis-compatible PostgreSQL KV adapter and onto dedicated PostgreSQL tables.

**Architecture:** Keep the existing KV/list/set adapter as the compatibility source for full records, messages, credentials, schedules, teams, and knowledge bases. Add dedicated `agents` and `sessions` tables maintained by `PostgresStorage` writes/deletes, then override `list_agents`, `list_sessions`, and `list_sessions_by_schedule` to read rows directly and deserialize the stored JSON record. This is intentionally phase 1: it reduces list round-trips without changing router DTOs or runtime behavior.

**Tech Stack:** Python, asyncpg, Pydantic model JSON, existing `RedisStorage` compatibility methods, pytest.

---

### Task 1: Add Focused Tests

**Files:**
- Modify: `tests/postgres_agent_service_wiring_test.py`

- [x] Add tests with fake asyncpg pool/connection proving:
  - `PostgresStorage.upsert_agent()` inserts/updates a dedicated agent row.
  - `PostgresStorage.list_agents()` reads the dedicated agent rows and still filters `source="team"`.
  - `PostgresStorage.upsert_session()` inserts/updates a dedicated session row.
  - `PostgresStorage.list_sessions()` reads rows sorted newest first.
  - `PostgresStorage.delete_agent()` and `delete_session()` remove dedicated rows.

- [x] Run the focused tests and verify they fail before implementation:

```powershell
$env:PYTHONPATH='.;src;tests'; uv run pytest tests/postgres_agent_service_wiring_test.py -q
```

Expected: new tests fail because the dedicated table adapter/methods do not exist yet.

### Task 2: Add Dedicated Table Adapter

**Files:**
- Modify: `src/agentscope/app/storage/_postgres_storage.py`

- [x] Extend schema initialization with tables:
  - `<prefix>_agents(user_id, agent_id, source, record_json, updated_at, PRIMARY KEY(user_id, agent_id))`
  - `<prefix>_sessions(user_id, agent_id, session_id, source_schedule_id, created_at, record_json, updated_at, PRIMARY KEY(user_id, session_id))`
  - indexes on `(user_id, source)`, `(user_id, agent_id, created_at DESC)`, and `(user_id, source_schedule_id)`.

- [x] Add adapter helpers:
  - `upsert_agent_record(user_id, record)`
  - `list_agent_records(user_id)`
  - `delete_agent_record(user_id, agent_id)`
  - `upsert_session_record(user_id, record)`
  - `list_session_records(user_id, agent_id)`
  - `list_session_records_by_schedule(user_id, schedule_id)`
  - `delete_session_record(user_id, session_id)`

### Task 3: Override PostgreSQL Storage Methods

**Files:**
- Modify: `src/agentscope/app/storage/_postgres_storage.py`

- [x] Override agent/session write/list/delete methods:
  - Call `super().upsert_agent()` then sync the dedicated agent row.
  - Read `list_agents()` directly from dedicated rows after startup backfill.
  - Call `super().delete_agent()` then delete dedicated agent rows and session rows for the agent.
  - Call `super().upsert_session()` then sync the dedicated session row.
  - Read `list_sessions()` and `list_sessions_by_schedule()` from dedicated rows.
  - Call `super().delete_session()` then delete the dedicated session row.
  - Backfill dedicated rows from legacy KV records during `PostgresStorage` startup.

- [x] Preserve existing full-record JSON shape so routers and services require no changes.

### Task 4: Verify

**Files:**
- Test: `tests/postgres_agent_service_wiring_test.py`
- Test: `tests/storage_redis_test.py`

- [x] Run:

```powershell
$env:PYTHONPATH='.;src;tests'; uv run pytest tests/postgres_agent_service_wiring_test.py -q
```

Expected: pass.

- [x] Run:

```powershell
$env:PYTHONPATH='.;src;tests'; uv run pytest tests/storage_redis_test.py -q
```

Expected: pass, proving the base Redis-compatible storage behavior was not changed.

### Task 5: Split Common Display Columns

**Files:**
- Modify: `src/agentscope/app/storage/_postgres_storage.py`
- Modify: `tests/postgres_agent_service_wiring_test.py`

- [x] Add agent display columns:
  - `agent_name`
  - `description`
  - `share_config_json`
  - `shared`

- [x] Add session display columns:
  - `workspace_id`
  - `session_name`
  - `model_type`
  - `model_name`
  - `team_id`
  - `status`
  - `state_json`

- [x] Keep `record_json` as the full compatibility payload.

- [x] Backfill and normal writes use the same upsert helpers, so upgraded rows
  are refreshed into the new columns during `PostgresStorage` startup.
