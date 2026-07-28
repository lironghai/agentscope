import { Loader2, Plug, RefreshCw, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { globalCatalogApi } from '@/api';
import type {
	AgentMcpBinding,
	AgentSkillBinding,
	SkillHubCatalogItem,
} from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
	FieldDescription,
	FieldGroup,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';

interface Props {
	skillBindings: AgentSkillBinding[];
	mcpBindings: AgentMcpBinding[];
	onSkillBindingsChange: (bindings: AgentSkillBinding[]) => void;
	onMcpBindingsChange: (bindings: AgentMcpBinding[]) => void;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
	if (!value || typeof value !== 'object' || Array.isArray(value)) {
		return undefined;
	}
	return value as Record<string, unknown>;
}

function readString(
	item: Record<string, unknown> | undefined,
	...keys: string[]
): string | undefined {
	if (!item) return undefined;
	for (const key of keys) {
		const value = item[key];
		if (typeof value === 'string' && value.trim()) return value;
		if (typeof value === 'number') return String(value);
	}
	return undefined;
}

function itemId(item: SkillHubCatalogItem): string {
	const row = item as Record<string, unknown>;
	return (
		readString(row, 'id', 'skillId', 'serverId', 'name', 'slug') ??
		JSON.stringify(item)
	);
}

function skillVersion(item: Record<string, unknown>): string | undefined {
	return (
		readString(item, 'version', 'latestVersion') ??
		readString(asRecord(item.publishedVersion), 'version') ??
		readString(asRecord(item.latestPublishedVersion), 'version')
	);
}

function skillTitle(item: Record<string, unknown>): string {
	return readString(item, 'displayName', 'display_name', 'name', 'slug') ?? '';
}

function skillBindingFromItem(item: SkillHubCatalogItem): AgentSkillBinding {
	const row = item as Record<string, unknown>;
	return {
		source: 'skillhub',
		skillhub_skill_id: readString(row, 'id', 'skillId') ?? null,
		namespace: readString(row, 'namespace', 'namespaceName', 'owner') ?? '',
		slug: readString(row, 'slug', 'name') ?? '',
		version: skillVersion(row) ?? null,
		display_name: skillTitle(row),
		description: readString(row, 'description', 'summary') ?? null,
		enabled: true,
		config_json: {},
		snapshot_json: item,
	};
}

function mcpBindingFromItem(item: SkillHubCatalogItem): AgentMcpBinding {
	const row = item as Record<string, unknown>;
	return {
		source: 'skillhub',
		skillhub_server_id: readString(row, 'id', 'serverId') ?? null,
		name: readString(row, 'name', 'displayName', 'display_name') ?? '',
		provider: readString(row, 'provider', 'providerName') ?? null,
		auth_type: readString(row, 'authType', 'auth_type') ?? null,
		transport: readString(row, 'transport', 'transportType') ?? null,
		endpoint_url: readString(row, 'endpointUrl', 'endpoint_url', 'url') ?? null,
		enabled: true,
		config_json: {},
		snapshot_json: item,
	};
}

function skillBindingKey(binding: AgentSkillBinding): string {
	return (
		binding.skillhub_skill_id ??
		`${binding.namespace ?? ''}/${binding.slug ?? ''}/${binding.version ?? ''}`
	);
}

function mcpBindingKey(binding: AgentMcpBinding): string {
	return binding.skillhub_server_id ?? binding.name ?? '';
}

function CatalogRow({
	checked,
	title,
	meta,
	description,
	onCheckedChange,
}: {
	checked: boolean;
	title: string;
	meta?: string;
	description?: string;
	onCheckedChange: (checked: boolean) => void;
}) {
	return (
		<label className="hover:bg-muted/40 flex cursor-pointer gap-3 rounded-md border px-3 py-2">
			<Checkbox checked={checked} onCheckedChange={(value) => onCheckedChange(value === true)} />
			<span className="min-w-0 flex-1">
				<span className="flex min-w-0 items-center gap-2">
					<span className="truncate text-sm font-medium">{title}</span>
					{meta ? (
						<Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
							{meta}
						</Badge>
					) : null}
				</span>
				{description ? (
					<span className="text-muted-foreground mt-1 line-clamp-2 block text-xs">
						{description}
					</span>
				) : null}
			</span>
		</label>
	);
}

export function AgentCatalogBindings({
	skillBindings,
	mcpBindings,
	onSkillBindingsChange,
	onMcpBindingsChange,
}: Props) {
	const { t } = useTranslation();
	const [skills, setSkills] = useState<SkillHubCatalogItem[]>([]);
	const [mcps, setMcps] = useState<SkillHubCatalogItem[]>([]);
	const [loading, setLoading] = useState(false);
	const [skillError, setSkillError] = useState('');
	const [mcpError, setMcpError] = useState('');

	const skillKeys = useMemo(
		() => new Set(skillBindings.map(skillBindingKey)),
		[skillBindings],
	);
	const mcpKeys = useMemo(
		() => new Set(mcpBindings.map(mcpBindingKey)),
		[mcpBindings],
	);

	const loadCatalog = useCallback(async () => {
		setLoading(true);
		setSkillError('');
		setMcpError('');
		const [skillResult, catalogMcpResult, internalMcpResult] =
			await Promise.allSettled([
				globalCatalogApi.listSkills({ sort: 'newest', page: 0, size: 100 }),
				globalCatalogApi.listMcpServers({ page: 0, size: 100 }),
				globalCatalogApi.listInternalMcpServers({ page: 0, size: 100 }),
			]);

		if (skillResult.status === 'fulfilled') {
			setSkills(skillResult.value.items ?? []);
		} else {
			setSkills([]);
			setSkillError(
				skillResult.reason instanceof Error
					? skillResult.reason.message
					: t('agent-bindings.loadError'),
			);
		}

		const mcpItems: SkillHubCatalogItem[] = [];
		if (catalogMcpResult.status === 'fulfilled') {
			mcpItems.push(...(catalogMcpResult.value.items ?? []));
		}
		if (internalMcpResult.status === 'fulfilled') {
			mcpItems.push(...(internalMcpResult.value.items ?? []));
		}
		setMcps(mcpItems);
		if (
			catalogMcpResult.status === 'rejected' &&
			internalMcpResult.status === 'rejected'
		) {
			setMcpError(
				catalogMcpResult.reason instanceof Error
					? catalogMcpResult.reason.message
					: t('agent-bindings.loadError'),
			);
		}
		setLoading(false);
	}, [t]);

	useEffect(() => {
		void loadCatalog();
	}, [loadCatalog]);

	const toggleSkill = (item: SkillHubCatalogItem, checked: boolean) => {
		const binding = skillBindingFromItem(item);
		const key = skillBindingKey(binding);
		if (checked) {
			if (!skillKeys.has(key)) onSkillBindingsChange([...skillBindings, binding]);
			return;
		}
		onSkillBindingsChange(
			skillBindings.filter((entry) => skillBindingKey(entry) !== key),
		);
	};

	const toggleMcp = (item: SkillHubCatalogItem, checked: boolean) => {
		const binding = mcpBindingFromItem(item);
		const key = mcpBindingKey(binding);
		if (checked) {
			if (!mcpKeys.has(key)) onMcpBindingsChange([...mcpBindings, binding]);
			return;
		}
		onMcpBindingsChange(
			mcpBindings.filter((entry) => mcpBindingKey(entry) !== key),
		);
	};

	return (
		<>
			<FieldSeparator className="my-0" />
			<FieldSet>
				<div className="flex items-start justify-between gap-3">
					<div>
						<FieldLegend>{t('agent-bindings.legend')}</FieldLegend>
						<FieldDescription>{t('agent-bindings.description')}</FieldDescription>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="icon-sm"
						tooltip={t('agent-bindings.refresh')}
						onClick={() => void loadCatalog()}
						disabled={loading}
					>
						{loading ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<RefreshCw className="size-3.5" />
						)}
					</Button>
				</div>

				<FieldGroup>
					<section className="space-y-2">
						<div className="flex items-center gap-2 text-sm font-medium">
							<Sparkles className="size-3.5" />
							<span>{t('agent-bindings.skills')}</span>
							<Badge variant="outline">{skillBindings.length}</Badge>
						</div>
						{skillError ? (
							<p className="text-destructive text-xs">{skillError}</p>
						) : (
							<div className="max-h-44 space-y-2 overflow-y-auto pr-1">
								{skills.length === 0 && !loading ? (
									<p className="text-muted-foreground text-xs">
										{t('agent-bindings.emptySkills')}
									</p>
								) : null}
								{skills.map((item) => {
									const row = item as Record<string, unknown>;
									const binding = skillBindingFromItem(item);
									const key = skillBindingKey(binding);
									return (
										<CatalogRow
											key={itemId(item)}
											checked={skillKeys.has(key)}
											title={skillTitle(row) || key}
											meta={binding.version ?? binding.namespace}
											description={binding.description ?? undefined}
											onCheckedChange={(checked) => toggleSkill(item, checked)}
										/>
									);
								})}
							</div>
						)}
					</section>

					<section className="space-y-2">
						<div className="flex items-center gap-2 text-sm font-medium">
							<Plug className="size-3.5" />
							<span>{t('agent-bindings.mcps')}</span>
							<Badge variant="outline">{mcpBindings.length}</Badge>
						</div>
						{mcpError ? (
							<p className="text-destructive text-xs">{mcpError}</p>
						) : (
							<div className="max-h-44 space-y-2 overflow-y-auto pr-1">
								{mcps.length === 0 && !loading ? (
									<p className="text-muted-foreground text-xs">
										{t('agent-bindings.emptyMcps')}
									</p>
								) : null}
								{mcps.map((item) => {
									const row = item as Record<string, unknown>;
									const binding = mcpBindingFromItem(item);
									const key = mcpBindingKey(binding);
									return (
										<CatalogRow
											key={itemId(item)}
											checked={mcpKeys.has(key)}
											title={binding.name || key}
											meta={binding.transport ?? binding.auth_type ?? undefined}
											description={
												readString(row, 'description', 'summary') ?? undefined
											}
											onCheckedChange={(checked) => toggleMcp(item, checked)}
										/>
									);
								})}
							</div>
						)}
					</section>
				</FieldGroup>
			</FieldSet>
		</>
	);
}
