import { ChevronDown, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { agentApi } from '@/api';
import type { AgentToolCatalogItem } from '@/api';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';

interface Props {
	values: Record<string, SchemaFormValue>;
	onChange: (key: string, value: SchemaFormValue) => void;
}

function asStringArray(value: SchemaFormValue): string[] {
	if (!Array.isArray(value)) return [];
	return value
		.map((item) => (typeof item === 'string' ? item.trim() : ''))
		.filter(Boolean);
}

function unique(items: string[]): string[] {
	return Array.from(new Set(items));
}

function toggleName(items: string[], name: string, enabled: boolean): string[] {
	const set = new Set(items);
	if (enabled) {
		set.add(name);
	} else {
		set.delete(name);
	}
	return Array.from(set);
}

function defaultsText(value: SchemaFormValue): string {
	if (typeof value === 'string') return value;
	if (value && typeof value === 'object' && !Array.isArray(value)) {
		return JSON.stringify(value, null, 2);
	}
	return '';
}

function mergeConfiguredTools(
	catalog: AgentToolCatalogItem[],
	configured: string[],
): AgentToolCatalogItem[] {
	const seen = new Set(catalog.map((item) => item.name));
	const extras = configured
		.filter((name) => !seen.has(name))
		.map((name) => ({
			name,
			description: 'Saved in this Agent config but not present in the current tool catalog.',
			source: 'Configured',
		}));
	return [...catalog, ...extras];
}

export function AgentToolConfigEditor({ values, onChange }: Props) {
	const [catalog, setCatalog] = useState<AgentToolCatalogItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [defaultsOpen, setDefaultsOpen] = useState(false);

	const allow = useMemo(() => asStringArray(values.allow), [values.allow]);
	const deny = useMemo(() => asStringArray(values.deny), [values.deny]);
	const tools = useMemo(
		() => mergeConfiguredTools(catalog, unique([...allow, ...deny])),
		[catalog, allow, deny],
	);

	useEffect(() => {
		const controller = new AbortController();
		setLoading(true);
		setError(null);
		agentApi
			.listToolCatalog({ signal: controller.signal, silent: true })
			.then((items) => setCatalog(items))
			.catch((err: unknown) => {
				if (controller.signal.aborted) return;
				setError(err instanceof Error ? err.message : 'Failed to load tools.');
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, []);

	const updateAllow = (name: string, checked: boolean) => {
		const nextAllow = toggleName(allow, name, checked);
		const nextDeny = checked ? toggleName(deny, name, false) : deny;
		onChange('allow', nextAllow);
		onChange('deny', nextDeny);
	};

	const updateEffectiveAllowed = (name: string, checked: boolean) => {
		if (allow.length === 0) {
			onChange('deny', toggleName(deny, name, !checked));
			return;
		}
		updateAllow(name, checked);
	};

	const updateDeny = (name: string, checked: boolean) => {
		const nextDeny = toggleName(deny, name, checked);
		const nextAllow = checked ? toggleName(allow, name, false) : allow;
		onChange('deny', nextDeny);
		onChange('allow', nextAllow);
	};

	return (
		<FieldGroup>
			<Field>
				<div className="space-y-2">
					<div className="flex items-center justify-between gap-3">
						<FieldLabel>Built-in tools</FieldLabel>
						{loading ? (
							<span className="text-muted-foreground flex items-center gap-1 text-xs">
								<Loader2 className="size-3 animate-spin" />
								Loading
							</span>
						) : null}
					</div>
					<FieldDescription>
						Allowed controls the tools exposed to the Agent. Denied hides tools and
						takes precedence.
					</FieldDescription>
					{error ? <p className="text-destructive text-sm">{error}</p> : null}
					<div className="rounded-md border">
						<div className="text-muted-foreground grid grid-cols-[1fr_76px_76px] gap-3 border-b px-3 py-2 text-xs font-medium">
							<span>Tool</span>
							<span className="text-center">Allowed</span>
							<span className="text-center">Denied</span>
						</div>
						{tools.length === 0 && !loading ? (
							<div className="text-muted-foreground px-3 py-6 text-center text-sm">
								No built-in tools found.
							</div>
						) : null}
						{tools.map((tool) => {
							const denied = deny.includes(tool.name);
							const explicitlyAllowed = allow.includes(tool.name);
							const effectivelyAllowed =
								!denied && (allow.length === 0 || explicitlyAllowed);
							return (
								<div
									key={tool.name}
									className="grid grid-cols-[1fr_76px_76px] gap-3 border-b px-3 py-2 last:border-b-0"
								>
									<div className="min-w-0 space-y-1">
										<div className="flex min-w-0 items-center gap-2">
											<span className="truncate font-medium">{tool.name}</span>
											<Badge variant="outline" className="shrink-0">
												{tool.source}
											</Badge>
										</div>
										<p className="text-muted-foreground line-clamp-2 text-sm">
											{tool.description}
										</p>
									</div>
									<div className="flex items-center justify-center">
										<Checkbox
											aria-label={`Allow ${tool.name}`}
											checked={effectivelyAllowed}
											onCheckedChange={(checked) =>
												updateEffectiveAllowed(tool.name, checked === true)
											}
										/>
									</div>
									<div className="flex items-center justify-center">
										<Checkbox
											aria-label={`Deny ${tool.name}`}
											checked={denied}
											onCheckedChange={(checked) =>
												updateDeny(tool.name, checked === true)
											}
										/>
									</div>
								</div>
							);
						})}
					</div>
				</div>
			</Field>

			<Collapsible open={defaultsOpen} onOpenChange={setDefaultsOpen}>
				<CollapsibleTrigger asChild>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className="w-fit px-0 text-muted-foreground"
					>
						<ChevronDown
							className={`size-4 transition-transform ${defaultsOpen ? 'rotate-180' : ''}`}
						/>
						Advanced: Tool Defaults
					</Button>
				</CollapsibleTrigger>
				<CollapsibleContent>
					<Field className="mt-2">
						<FieldLabel htmlFor="agent-tool-defaults">Tool Defaults</FieldLabel>
						<Textarea
							id="agent-tool-defaults"
							rows={4}
							value={defaultsText(values.defaults)}
							onChange={(event) => {
								const raw = event.target.value.trim();
								if (!raw) {
									onChange('defaults', {});
									return;
								}
								try {
									onChange('defaults', JSON.parse(raw) as Record<string, unknown>);
								} catch {
									onChange('defaults', raw);
								}
							}}
							placeholder="Optional JSON defaults. Runtime application is not enabled yet."
						/>
						<FieldDescription>
							Stored in tools.json for future use. It is not applied by the runtime
							adapter yet.
						</FieldDescription>
					</Field>
				</CollapsibleContent>
			</Collapsible>
		</FieldGroup>
	);
}
