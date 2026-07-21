# Agent Service Per-Agent Shared Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each agent opt into sharing via `share_config.shared`, with storage-backed visibility for other users.

**Architecture:** Persist the share toggle in `AgentData`, keep a global shared-agent index in storage, and have the example-service access policy read that index to return read-only cross-owner refs.

**Tech Stack:** Python, Pydantic, AgentScope storage/access service, unittest, pytest.

---

## File Structure

- Modify `src/agentscope/app/storage/_model/_agent.py`: add `ShareConfig` and wire it into `AgentData`.
- Modify `src/agentscope/app/storage/_base.py`: add `list_shared_agents`.
- Modify `src/agentscope/app/storage/_redis_storage.py`: maintain the shared-agent index.
- Modify `src/agentscope/app/storage/__init__.py` and `src/agentscope/app/storage/_model/__init__.py`: export `ShareConfig`.
- Modify `src/agentscope/app/_router/_schema/_agent.py` and `src/agentscope/app/_router/_agent.py`: accept `share_config` in create/update payloads.
- Modify `examples/agent_service/shared_agents.py` and `examples/agent_service/main.py`: inject the new policy without env flags.
- Create `tests/agent_router_share_config_test.py`: schema-level request coverage.
- Update `tests/agent_service_shared_agents_test.py` and `tests/storage_redis_test.py`: behavior coverage.

### Task 1: Add the share toggle to the storage model

**Files:**
- Modify: `src/agentscope/app/storage/_model/_agent.py`
- Modify: `src/agentscope/app/storage/_model/__init__.py`
- Modify: `src/agentscope/app/storage/__init__.py`

- [x] **Step 1: Add the failing test coverage**

```python
data = AgentData(
    name="Private by default",
    system_prompt="You are a helpful assistant.",
    context_config=ContextConfig(),
    react_config=ReActConfig(),
)
assert data.share_config.shared is False
```

- [x] **Step 2: Implement `ShareConfig` and export it**

```python
class ShareConfig(BaseModel):
    shared: bool = Field(default=False, title="Shared")


class AgentData(BaseModel):
    ...
    share_config: ShareConfig = Field(
        default_factory=ShareConfig,
        description="The sharing config for the agent.",
        title="Share Config",
    )
```

- [x] **Step 3: Verify syntax**

Run: `.\.venv\Scripts\python.exe -m py_compile src\agentscope\app\storage\_model\_agent.py`

Expected: exit code 0.

### Task 2: Persist shared agents in storage

**Files:**
- Modify: `src/agentscope/app/storage/_base.py`
- Modify: `src/agentscope/app/storage/_redis_storage.py`

- [x] **Step 1: Add the storage contract**

```python
@abstractmethod
async def list_shared_agents(self, viewer_id: str) -> list[AgentRecord]:
    ...
```

- [x] **Step 2: Maintain a shared-agent index in Redis storage**

```python
member = json.dumps({"user_id": user_id, "agent_id": agent_record.id})
if agent_record.source == "user" and agent_record.data.share_config.shared:
    await self._client.sadd(self.key_config.shared_agent_index, member)
else:
    await self._client.srem(self.key_config.shared_agent_index, member)
```

- [x] **Step 3: Verify storage behavior**

Run: `.\.venv\Scripts\python.exe -m unittest tests.storage_redis_test.TestAgentSource.test_shared_agent_index_tracks_shared_agents`

Expected: PASS.

### Task 3: Accept the share toggle in the agent router

**Files:**
- Modify: `src/agentscope/app/_router/_schema/_agent.py`
- Modify: `src/agentscope/app/_router/_agent.py`

- [x] **Step 1: Add request schema coverage**

```python
request = CreateAgentRequest(name="Shared agent")
assert request.share_config.shared is False

request = UpdateAgentRequest(share_config=ShareConfig(shared=True))
assert request.share_config.shared is True
```

- [x] **Step 2: Pass `share_config` into `AgentData` on create**

```python
data = AgentData(
    name=body.name,
    system_prompt=body.system_prompt,
    context_config=body.context_config,
    react_config=body.react_config,
    invite_config=body.invite_config,
    share_config=body.share_config,
)
```

- [x] **Step 3: Verify schema and router compilation**

Run: `.\.venv\Scripts\python.exe -m py_compile src\agentscope\app\_router\_schema\_agent.py src\agentscope\app\_router\_agent.py`

Expected: exit code 0.

### Task 4: Expose shared agents through the example service

**Files:**
- Modify: `examples/agent_service/shared_agents.py`
- Modify: `examples/agent_service/main.py`

- [x] **Step 1: Replace the old whitelist policy**

```python
class SharedAgentPolicy(ResourceAccessPolicyBase):
    async def list_accessible(...):
        if kind is not ResourceKind.AGENT:
            return []
        return [
            ResourceRef(
                kind=ResourceKind.AGENT,
                owner_id=record.user_id,
                resource_id=record.id,
                permission=ResourcePermission.READ,
            )
            for record in await storage.list_shared_agents(viewer_id)
        ]
```

- [x] **Step 2: Inject the policy without env flags**

```python
shared_agent_policy = SharedAgentPolicy()
app = create_app(..., resource_access_policy=shared_agent_policy, ...)
```

- [x] **Step 3: Verify the focused policy test**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p agent_service_shared_agents_test.py`

Expected: PASS.

### Task 5: Final verification

**Files:**
- Verify all modified files

- [x] **Step 1: Run schema and storage tests**

Run:
`.\.venv\Scripts\python.exe -m unittest discover -s tests -p agent_router_share_config_test.py`
`.\.venv\Scripts\python.exe -m unittest discover -s tests -p agent_service_shared_agents_test.py`
`.\.venv\Scripts\python.exe -m unittest tests.storage_redis_test.TestAgentSource.test_shared_agent_index_tracks_shared_agents`

Expected: all pass.

- [x] **Step 2: Run `pytest` and diff checks**

Run:
`.\.venv\Scripts\python.exe -m pytest tests\agent_router_share_config_test.py tests\agent_service_shared_agents_test.py -q`
`git diff --check`

Expected: pytest passes; diff check exits 0.
