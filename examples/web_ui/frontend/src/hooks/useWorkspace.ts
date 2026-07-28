import { useState, useEffect, useCallback, useRef } from 'react';

import { workspaceApi } from '@/api';
import type { MCPClient, MCPClientStatus, Skill } from '@/api';

export function useWorkspace(agentId: string | null, sessionId: string | null) {
	const [mcps, setMcps] = useState<MCPClientStatus[]>([]);
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loadedMcpPair, setLoadedMcpPair] = useState<{
		agentId: string;
		sessionId: string | null;
	} | null>(null);
	const [loadedSkillPair, setLoadedSkillPair] = useState<{
		agentId: string;
		sessionId: string | null;
	} | null>(null);
	const [loading, setLoading] = useState(false);
	const [skillsLoading, setSkillsLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);
	const currentPairRef = useRef({ agentId, sessionId });

	useEffect(() => {
		currentPairRef.current = { agentId, sessionId };
	}, [agentId, sessionId]);

	const isCurrentPair = useCallback(
		(expectedAgentId: string, expectedSessionId: string | null) =>
			currentPairRef.current.agentId === expectedAgentId &&
			currentPairRef.current.sessionId === expectedSessionId,
		[],
	);

	const refetch = useCallback(async () => {
		if (!agentId) {
			setMcps([]);
			setLoadedMcpPair(null);
			setLoading(false);
			return;
		}
		const requestAgentId = agentId;
		const requestSessionId = sessionId;
		setMcps([]);
		setLoadedMcpPair(null);
		setLoading(true);
		setError(null);
		try {
			const nextMcps = await workspaceApi.mcp.list(requestAgentId, requestSessionId);
			if (isCurrentPair(requestAgentId, requestSessionId)) {
				setMcps(nextMcps);
				setLoadedMcpPair({ agentId: requestAgentId, sessionId: requestSessionId });
			}
		} catch (e) {
			if (isCurrentPair(requestAgentId, requestSessionId)) {
				setMcps([]);
				setLoadedMcpPair(null);
				setError(e as Error);
			}
		} finally {
			if (isCurrentPair(requestAgentId, requestSessionId)) setLoading(false);
		}
	}, [agentId, sessionId, isCurrentPair]);

	const refetchSkills = useCallback(async () => {
		if (!agentId) {
			setSkills([]);
			setLoadedSkillPair(null);
			setSkillsLoading(false);
			return;
		}
		const requestAgentId = agentId;
		const requestSessionId = sessionId;
		setSkills([]);
		setLoadedSkillPair(null);
		setSkillsLoading(true);
		try {
			const nextSkills = await workspaceApi.skill.list(requestAgentId, requestSessionId);
			if (isCurrentPair(requestAgentId, requestSessionId)) {
				setSkills(nextSkills);
				setLoadedSkillPair({ agentId: requestAgentId, sessionId: requestSessionId });
			}
		} catch (e) {
			if (isCurrentPair(requestAgentId, requestSessionId)) {
				setSkills([]);
				setLoadedSkillPair(null);
				setError(e as Error);
			}
		} finally {
			if (isCurrentPair(requestAgentId, requestSessionId)) setSkillsLoading(false);
		}
	}, [agentId, sessionId, isCurrentPair]);

	useEffect(() => {
		refetch();
	}, [refetch]);
	useEffect(() => {
		refetchSkills();
	}, [refetchSkills]);

	const addMcps = useCallback(
		async (clients: MCPClient[]) => {
			if (!agentId) throw new Error('No agent selected');
			const currentMcps =
				loadedMcpPair?.agentId === agentId && loadedMcpPair.sessionId === sessionId
					? mcps
					: [];
			const existingNames = new Set(currentMcps.map((m) => m.name));
			for (const mcp of clients) {
				if (existingNames.has(mcp.name)) {
					throw new Error(`MCP server "${mcp.name}" already exists in this workspace.`);
				}
			}
			const batchNames = new Set<string>();
			for (const mcp of clients) {
				if (batchNames.has(mcp.name)) {
					throw new Error(`Duplicate MCP server name "${mcp.name}" in configuration.`);
				}
				batchNames.add(mcp.name);
			}
			for (const mcp of clients) {
				await workspaceApi.mcp.add(agentId, sessionId, mcp);
			}
			await refetch();
		},
		[agentId, sessionId, loadedMcpPair, mcps, refetch],
	);

	const removeMcp = useCallback(
		async (mcpName: string) => {
			if (!agentId) throw new Error('No agent selected');
			await workspaceApi.mcp.remove(mcpName, agentId, sessionId);
			await refetch();
		},
		[agentId, sessionId, refetch],
	);

	const addSkill = useCallback(
		async (skillPath: string) => {
			if (!agentId) throw new Error('No agent selected');
			await workspaceApi.skill.add(agentId, sessionId, { skill_path: skillPath });
			await refetchSkills();
		},
		[agentId, sessionId, refetchSkills],
	);

	const uploadSkill = useCallback(
		async (file: File) => {
			if (!agentId) throw new Error('No agent selected');
			await workspaceApi.skill.upload(agentId, sessionId, file);
			await refetchSkills();
		},
		[agentId, sessionId, refetchSkills],
	);

	const removeSkill = useCallback(
		async (skillName: string) => {
			if (!agentId) throw new Error('No agent selected');
			await workspaceApi.skill.remove(skillName, agentId, sessionId);
			await refetchSkills();
		},
		[agentId, sessionId, refetchSkills],
	);

	const visibleMcps =
		loadedMcpPair?.agentId === agentId && loadedMcpPair.sessionId === sessionId ? mcps : [];
	const visibleSkills =
		loadedSkillPair?.agentId === agentId && loadedSkillPair.sessionId === sessionId
			? skills
			: [];

	return {
		mcps: visibleMcps,
		loading,
		error,
		refetch,
		refetchSkills,
		addMcps,
		removeMcp,
		skills: visibleSkills,
		skillsLoading,
		addSkill,
		uploadSkill,
		removeSkill,
	};
}
