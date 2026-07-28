import { useState, useEffect, useCallback, useMemo, useRef } from 'react';

import { sessionApi } from '../api';
import type { SessionView, CreateSessionRequest, UpdateSessionRequest } from '../api';

/**
 * Manages session views for a given agent.
 *
 * Each entry is a `SessionView` (record + is_running + optional team
 * detail) — the same shape the backend returns. The hook clears and
 * re-fetches whenever agentId changes.
 *
 * @param agentId - The agent whose sessions to load. Pass null to skip fetching.
 * @returns Object with the loaded `sessions` array plus `loading` /
 *   `error` flags and `refetch` / `create` / `update` / `remove`
 *   helpers that all keep the local list in sync.
 */
export function useSessions(agentId: string | null) {
	const [sessions, setSessions] = useState<SessionView[]>([]);
	const [loadedAgentId, setLoadedAgentId] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	const currentAgentRef = useRef(agentId);

	useEffect(() => {
		currentAgentRef.current = agentId;
	}, [agentId]);

	const refetch = useCallback(async () => {
		if (!agentId) {
			setSessions([]);
			setLoadedAgentId(null);
			setLoading(false);
			return;
		}
		const requestAgentId = agentId;
		setSessions([]);
		setLoadedAgentId(null);
		setLoading(true);
		setError(null);
		try {
			const res = await sessionApi.list(requestAgentId);
			if (currentAgentRef.current === requestAgentId) {
				setSessions(res.sessions);
				setLoadedAgentId(requestAgentId);
			}
		} catch (e) {
			if (currentAgentRef.current === requestAgentId) {
				setSessions([]);
				setError(e as Error);
				setLoadedAgentId(null);
			}
		} finally {
			if (currentAgentRef.current === requestAgentId) {
				setLoading(false);
			}
		}
	}, [agentId]);

	useEffect(() => {
		refetch();
	}, [refetch]);

	/** Creates a new session and refreshes the list. */
	const create = useCallback(
		async (body: CreateSessionRequest) => {
			const res = await sessionApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Updates a session's model config and refreshes the list. */
	const update = useCallback(
		async (sessionId: string, body: UpdateSessionRequest) => {
			if (!agentId) throw new Error('No agent selected');
			const res = await sessionApi.update(sessionId, agentId, body);
			await refetch();
			return res;
		},
		[agentId, refetch],
	);

	/** Deletes a session and refreshes the list. */
	const remove = useCallback(
		async (sessionId: string) => {
			if (!agentId) throw new Error('No agent selected');
			await sessionApi.delete(sessionId, agentId);
			await refetch();
		},
		[agentId, refetch],
	);

	const visibleSessions = useMemo(
		() => (loadedAgentId === agentId ? sessions : []),
		[agentId, loadedAgentId, sessions],
	);

	return { sessions: visibleSessions, loading, error, refetch, create, update, remove };
}
