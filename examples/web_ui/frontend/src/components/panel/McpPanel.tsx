import { ChevronDown, PlusCircle, Search, SearchX, Trash, Unplug } from 'lucide-react';
import { useState } from 'react';

import type { AgentMcpBinding, MCPClient, MCPClientStatus, ToolInfo } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { CreateMCPDialog } from '@/components/dialog/MCPDialog.tsx';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { useTranslation } from '@/i18n/useI18n.ts';

function normalized(value: unknown): string | null {
	if (typeof value !== 'string' && typeof value !== 'number') return null;
	const text = String(value).trim().toLowerCase();
	return text || null;
}

function addKey(keys: Set<string>, value: unknown) {
	const key = normalized(value);
	if (key) keys.add(key);
}

function safeSegment(value: string): string {
	return value.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'item';
}

function materializedMcpName(binding: AgentMcpBinding): string | null {
	const name = normalized(binding.name);
	if (name) return name;
	const id = normalized(binding.skillhub_server_id);
	return id ? safeSegment(id) : 'skillhub_mcp';
}

function McpToolsDisclosure({ tools }: { tools: ToolInfo[] }) {
	const { t } = useTranslation();
	if (tools.length === 0) {
		return (
			<KbdGroup>
				<Kbd>{t('panel.mcp.tools', { count: 0 })}</Kbd>
			</KbdGroup>
		);
	}

	return (
		<Collapsible>
			<CollapsibleTrigger asChild>
				<Button
					type="button"
					variant="outline"
					size="xs"
					className="h-6 gap-1 rounded-md px-2 text-xs font-normal text-muted-foreground [&[data-state=open]>svg]:rotate-180"
				>
					{t('panel.mcp.tools', { count: tools.length })}
					<ChevronDown className="size-3 transition-transform" />
				</Button>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<div className="mt-2 max-h-64 space-y-1.5 overflow-y-auto rounded-md border bg-muted/20 p-2">
					{tools.map((tool) => (
						<div key={tool.name} className="rounded-sm px-1 py-0.5">
							<div className="truncate text-xs font-medium text-foreground">
								{tool.name}
							</div>
							{tool.description ? (
								<div className="mt-0.5 line-clamp-3 text-xs text-muted-foreground">
									{tool.description}
								</div>
							) : null}
						</div>
					))}
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

interface McpPanelProps {
	/** The MCP servers equipped in the workspace. */
	mcps: MCPClientStatus[];
	/** Agent-level MCP bindings configured before a session exists. */
	configuredMcps?: AgentMcpBinding[];
	/** Whether the MCP list is still loading. */
	loading?: boolean;
	/**
	 * Add one or more MCP servers to the workspace.
	 *
	 * @param mcps - The MCP client configs to add.
	 */
	onAdd: (mcps: MCPClient[]) => Promise<void>;
	/**
	 * Remove an MCP server by name.
	 *
	 * @param name - The MCP server name to remove.
	 */
	onRemove: (name: string) => Promise<void>;
}

/**
 * Pure content body for the MCP dock panel: a search box, the list of
 * equipped MCP servers, and an "Add MCP" action. Holds only local UI
 * state (search text, delete confirmation target); all data arrives
 * via props so it owns no data fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param mcps - The MCP servers to list.
 * @param loading - Whether the list is loading.
 * @param onAdd - Add-MCP callback.
 * @param onRemove - Remove-MCP callback.
 * @returns The MCP panel body.
 */
export function McpPanel({
	mcps,
	configuredMcps = [],
	loading = false,
	onAdd,
	onRemove,
}: McpPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
	const workspaceKeys = new Set<string>();
	for (const mcp of mcps) {
		addKey(workspaceKeys, mcp.name);
	}

	const filtered = search
		? mcps.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;
	const configuredFiltered = configuredMcps
		.filter((binding) => binding.enabled !== false)
		.filter((binding) => !workspaceKeys.has(materializedMcpName(binding) ?? ''))
		.filter((binding) => {
			if (!search) return true;
			const text = [
				binding.name,
				binding.provider,
				binding.transport,
				binding.auth_type,
				binding.skillhub_server_id,
			]
				.filter(Boolean)
				.join(' ')
				.toLowerCase();
			return text.includes(search.toLowerCase());
		});

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">{t('panel.mcp.description')}</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.mcp.searchPlaceholder')}
					value={search}
					onChange={(e) => setSearch(e.target.value)}
				/>
				<InputGroupAddon align="inline-end">
					<Search />
				</InputGroupAddon>
			</InputGroup>

			{loading ? (
				<div className="flex flex-1 items-center justify-center">
					<p className="text-muted-foreground text-sm">{t('panel.loading')}</p>
				</div>
			) : filtered.length === 0 && configuredFiltered.length === 0 ? (
				<PanelEmpty
					icon={search ? SearchX : Unplug}
					title={search ? t('panel.search.emptyTitle') : t('panel.mcp.emptyTitle')}
					description={
						search
							? t('panel.search.emptyDescription', { query: search })
							: t('panel.mcp.emptyDescription')
					}
				/>
			) : (
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto gap-y-2">
					{filtered.map((mcp) => (
						<Item key={mcp.name} variant="outline">
							<ItemContent>
								<ItemTitle className="flex items-center gap-x-2">
									<span
										className={`size-2 shrink-0 rounded-full ${mcp.is_healthy ? 'bg-green-500' : 'bg-red-500'}`}
									/>
									{mcp.name}
									</ItemTitle>
								<div className="flex flex-wrap items-center gap-1.5 text-muted-foreground">
									<KbdGroup>
										<Kbd>
											{mcp.mcp_config.type === 'stdio_mcp' ? 'STDIO' : 'HTTP'}
										</Kbd>
									</KbdGroup>
									<McpToolsDisclosure tools={mcp.tools} />
								</div>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => {
										setDeleteTarget(mcp.name);
										setDeleteOpen(true);
									}}
								>
									<Trash />
								</Button>
							</ItemActions>
						</Item>
					))}
					{configuredFiltered.map((binding) => {
						const name =
							binding.name ||
							binding.skillhub_server_id ||
							t('panel.mcp.unknownConfiguredMcp');
						return (
							<Item
								key={binding.binding_id || name}
								variant="outline"
							>
								<ItemContent>
									<ItemTitle className="flex items-center gap-x-2">
										<span>{name}</span>
										<Badge variant="secondary">
											{t('panel.mcp.configuredBadge')}
										</Badge>
									</ItemTitle>
									<ItemDescription>
										<KbdGroup>
											{binding.transport ? (
												<Kbd>{binding.transport.toUpperCase()}</Kbd>
											) : null}
											{binding.provider ? <Kbd>{binding.provider}</Kbd> : null}
											{binding.auth_type ? <Kbd>{binding.auth_type}</Kbd> : null}
										</KbdGroup>
									</ItemDescription>
								</ItemContent>
							</Item>
						);
					})}
				</div>
			)}

			<CreateMCPDialog onAdd={onAdd}>
				<Button variant="default">
					<PlusCircle />
					{t('panel.mcp.add')}
				</Button>
			</CreateMCPDialog>

			<DeleteDialog
				open={deleteOpen}
				onOpenChange={setDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.entity'),
					name: deleteTarget ?? '',
				})}
				description={t('common.deleteDescription')}
				onConfirm={async () => {
					if (deleteTarget) await onRemove(deleteTarget);
				}}
			/>
		</div>
	);
}
