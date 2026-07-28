import { client } from './client';
import type { AddSkillRequest, MCPClient, MCPClientStatus, Skill } from './types';

export const workspaceApi = {
	mcp: {
		list: (agentId: string, sessionId?: string | null) =>
			client.get<MCPClientStatus[]>(
				'/workspace/mcp',
				{
					agent_id: agentId,
					...(sessionId ? { session_id: sessionId } : {}),
				},
				{ silent: true },
			),

		add: (agentId: string, sessionId: string | null | undefined, mcp: MCPClient) =>
			client.post<void>('/workspace/mcp', mcp, {
				agent_id: agentId,
				...(sessionId ? { session_id: sessionId } : {}),
			}),

		remove: (mcpName: string, agentId: string, sessionId?: string | null) =>
			client.delete(`/workspace/mcp/${mcpName}`, {
				agent_id: agentId,
				...(sessionId ? { session_id: sessionId } : {}),
			}),
	},

	skill: {
		list: (agentId: string, sessionId?: string | null) =>
			client.get<Skill[]>(
				'/workspace/skill',
				{
					agent_id: agentId,
					...(sessionId ? { session_id: sessionId } : {}),
				},
				{ silent: true },
			),

		add: (agentId: string, sessionId: string | null | undefined, body: AddSkillRequest) =>
			client.post<void>('/workspace/skill', body, {
				agent_id: agentId,
				...(sessionId ? { session_id: sessionId } : {}),
			}),

		upload: (agentId: string, sessionId: string | null | undefined, file: File) => {
			const body = new FormData();
			body.append('file', file);
			return client.post<void>('/workspace/skill/upload', body, {
				agent_id: agentId,
				...(sessionId ? { session_id: sessionId } : {}),
			});
		},

		remove: (skillName: string, agentId: string, sessionId?: string | null) =>
			client.delete(`/workspace/skill/${skillName}`, {
				agent_id: agentId,
				...(sessionId ? { session_id: sessionId } : {}),
			}),
	},
};
