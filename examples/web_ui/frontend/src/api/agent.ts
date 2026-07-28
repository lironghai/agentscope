import { client } from './client';
import type {
	AgentListResponse,
	AgentToolCatalogItem,
	AgentToolsConfigResponse,
	AgentView,
	AgentSchemaV2Response,
	CreateAgentRequest,
	CreateAgentResponse,
	UpdateAgentToolsRequest,
	UpdateAgentRequest,
} from './types';

function skillHubHeaders(): Record<string, string> | undefined {
	const token = localStorage.getItem('skillhub_token')?.trim();
	if (!token) return undefined;
	return { 'X-SkillHub-Token': token };
}

export const agentApi = {
	list: () => client.get<AgentListResponse>('/agent/'),

	get: (agentId: string, options?: { silent?: boolean }) =>
		client.get<AgentView>(`/agent/${agentId}`, undefined, options),

	getSchema: () => client.get<AgentSchemaV2Response>('/agent/schema/v2'),

	listToolCatalog: (options?: { signal?: AbortSignal; silent?: boolean }) =>
		client.get<AgentToolCatalogItem[]>('/agent/tools/catalog', undefined, options),

	create: (body: CreateAgentRequest, options?: { silent?: boolean }) =>
		client.post<CreateAgentResponse>('/agent/', body, undefined, {
			...options,
			headers: skillHubHeaders(),
		}),

	update: (agentId: string, body: UpdateAgentRequest, options?: { silent?: boolean }) =>
		client.patch<AgentView>(`/agent/${agentId}`, body, undefined, {
			...options,
			headers: skillHubHeaders(),
		}),

	getToolsConfig: (agentId: string, options?: { silent?: boolean }) =>
		client.get<AgentToolsConfigResponse>(
			`/agent/${agentId}/tools/config`,
			undefined,
			options,
		),

	updateToolsConfig: (
		agentId: string,
		body: UpdateAgentToolsRequest,
		options?: { silent?: boolean },
	) =>
		client.put<AgentView>(`/agent/${agentId}/tools/config`, body, undefined, {
			...options,
			headers: skillHubHeaders(),
		}),

	delete: (agentId: string) => client.delete(`/agent/${agentId}`),
};
