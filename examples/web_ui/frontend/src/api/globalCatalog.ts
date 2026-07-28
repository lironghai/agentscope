import { client } from './client';
import type { SkillHubCatalogResponse, SkillHubMcpQuery, SkillHubSkillQuery } from './types';

function skillHubHeaders(): Record<string, string> | undefined {
	const token = localStorage.getItem('skillhub_token')?.trim();
	if (!token) return undefined;
	return { 'X-SkillHub-Token': token };
}

function toParams(query: object): Record<string, string> {
	const params: Record<string, string> = {};
	Object.entries(query as Record<string, unknown>).forEach(([key, value]) => {
		if (value === undefined || value === null || value === '') return;
		if (Array.isArray(value)) {
			if (value.length > 0) params[key] = value.join(',');
			return;
		}
		params[key] = String(value);
	});
	return params;
}

export const globalCatalogApi = {
	listSkills: (query: SkillHubSkillQuery = {}) =>
		client.get<SkillHubCatalogResponse>('/global/skills', toParams(query), {
			headers: skillHubHeaders(),
			silent: true,
		}),

	listMcpServers: (query: SkillHubMcpQuery = {}) =>
		client.get<SkillHubCatalogResponse>('/global/mcps/servers', toParams(query), {
			headers: skillHubHeaders(),
			silent: true,
		}),

	listInternalMcpServers: (query: Pick<SkillHubMcpQuery, 'search' | 'page' | 'size'> = {}) =>
		client.get<SkillHubCatalogResponse>(
			'/global/mcps/internal-servers',
			toParams(query),
			{ headers: skillHubHeaders(), silent: true },
		),
};
