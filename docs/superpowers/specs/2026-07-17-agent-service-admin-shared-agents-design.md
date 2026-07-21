# Agent Service Per-Agent Shared Agents Design

## Problem

`examples/agent_service` needs a user-facing share model. A user should be able
to mark an individual agent as shared, have that choice persist with the agent
record, and let other users see and run it read-only. The previous admin-owner
whitelist was only a bootstrap shortcut and did not model actual product
behavior.

## Goals

- Keep the sharing decision on the agent itself.
- Persist the share toggle with `AgentData`.
- Expose shared agents to other users as read-only.
- Keep owner agents editable for their owner.
- Keep team workers hidden from cross-owner sharing.
- Avoid env-driven ownership whitelists.

## Non-Goals

- No organization or group permission system.
- No shared credentials or shared knowledge bases in this change.
- No direct permission editing UI beyond the agent-level share toggle.

## Architecture

Add a `ShareConfig` sub-model to `AgentData` with a single `shared: bool`
field. The create/update agent request schemas accept the same sub-model so the
frontend can submit the toggle without special-case wiring.

Storage maintains a global shared-agent index keyed by `(user_id, agent_id)`.
When a user saves an agent with `share_config.shared=true`, storage writes that
pair into the index; when the flag is turned off or the agent is deleted, the
entry is removed.

`examples/agent_service/shared_agents.py` implements `ResourceAccessPolicyBase`
by reading that shared-agent index and converting each shared record into a
read-only `ResourceRef`. `ResourceAccessService` then merges the viewer's own
agents with the shared refs and marks cross-owner entries as `editable=false`.

## Data Flow

1. User creates or updates an agent with `share_config.shared=true`.
2. The router persists the `AgentData` payload.
3. Storage updates the shared-agent index for that `(user_id, agent_id)`.
4. Another user requests `GET /agent/`.
5. `ResourceAccessService` loads the viewer's own agents and the shared refs.
6. Shared entries render read-only; owner entries remain editable.
7. Shared agents can be used to open sessions because session resolution also
   goes through `ResourceAccessService`.

## Permission Rules

- Owner of the agent: editable.
- Non-owner and `share_config.shared=false`: invisible.
- Non-owner and `share_config.shared=true`: visible, read-only.
- Team workers are never shared cross-owner.
- Deleting an agent removes it from the shared index.

## Testing Requirements

- Verify `AgentData` defaults `share_config.shared` to `false`.
- Verify create/update request models accept `share_config`.
- Verify storage adds/removes agents from the shared index.
- Verify the policy returns read-only refs for shared agents only.
- Verify `ResourceAccessService` merges own and shared agents correctly.
- Verify syntax with `py_compile`.
- Verify the focused unittest files and `pytest` run.

## Risks

The shared-agent index is intentionally simple and local to the example-service
storage backend. For a larger deployment, the same policy could be backed by a
dedicated share table or an external authorization service.
