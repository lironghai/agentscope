import { FileX, PlusCircle, Search, SearchX, Trash } from 'lucide-react';
import { useState } from 'react';

import type { Skill } from '@/api';
import type { AgentSkillBinding } from '@/api';
import { AddSkillDialog } from '@/components/dialog/AddSkillDialog.tsx';
import { DeleteDialog } from '@/components/dialog/DeleteDialog.tsx';
import { Badge } from '@/components/ui/badge';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Button } from '@/components/ui/button';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
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

function materializedSkillNames(binding: AgentSkillBinding): Set<string> {
	const keys = new Set<string>();
	addKey(keys, binding.display_name);
	addKey(keys, binding.slug);
	addKey(keys, binding.skillhub_skill_id);
	return keys;
}

interface SkillPanelProps {
	/** The skills equipped in the workspace. */
	skills: Skill[];
	/** Agent-level SkillHub bindings configured before a session exists. */
	configuredSkills?: AgentSkillBinding[];
	/** Whether the skill list is still loading. */
	loading?: boolean;
	/**
	 * Add a skill to the workspace.
	 *
	 * @param skillPath - Path of the skill to add.
	 */
	onAdd: (skillPath: string) => Promise<void>;
	/** Upload a zipped skill package to the workspace. */
	onUpload?: (file: File) => Promise<void>;
	/**
	 * Remove a skill by name.
	 *
	 * @param name - The skill name to remove.
	 */
	onRemove: (name: string) => Promise<void>;
}

/**
 * Pure content body for the Skill dock panel: a search box, the list
 * of equipped skills, and an "Add Skill" action. Holds only local UI
 * state (search text, delete confirmation target); all data arrives
 * via props so it owns no data fetching.
 *
 * Renders without its own header/border — the surrounding `Panel`
 * chrome (from `PanelDock`) provides those.
 *
 * @param skills - The skills to list.
 * @param loading - Whether the list is loading.
 * @param onAdd - Add-skill callback.
 * @param onRemove - Remove-skill callback.
 * @returns The skill panel body.
 */
export function SkillPanel({
	skills,
	configuredSkills = [],
	loading = false,
	onAdd,
	onUpload,
	onRemove,
}: SkillPanelProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

	const filtered = search
		? skills.filter((s) => s.name.toLowerCase().includes(search.toLowerCase()))
		: skills;
	const workspaceSkillKeys = new Set<string>();
	for (const skill of skills) {
		addKey(workspaceSkillKeys, skill.name);
	}
	const configuredFiltered = configuredSkills
		.filter((binding) => binding.enabled !== false)
		.filter((binding) => {
			for (const name of materializedSkillNames(binding)) {
				if (workspaceSkillKeys.has(name)) return false;
			}
			return true;
		})
		.filter((binding) => {
			if (!search) return true;
			const text = [
				binding.display_name,
				binding.slug,
				binding.namespace,
				binding.description,
			]
				.filter(Boolean)
				.join(' ')
				.toLowerCase();
			return text.includes(search.toLowerCase());
		});

	return (
		<div className="flex flex-col flex-1 min-h-0 gap-y-2">
			<span className="text-muted-foreground text-sm">{t('panel.skill.description')}</span>
			<InputGroup>
				<InputGroupInput
					placeholder={t('panel.skill.searchPlaceholder')}
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
					icon={search ? SearchX : FileX}
					title={search ? t('panel.search.emptyTitle') : t('panel.skill.emptyTitle')}
					description={
						search
							? t('panel.search.emptyDescription', { query: search })
							: t('panel.skill.emptyDescription')
					}
				/>
			) : (
				<div className="flex flex-col flex-1 min-h-0 overflow-y-auto gap-y-2">
					{filtered.map((skill) => (
						<Item key={skill.name} variant="outline">
							<ItemContent>
								<ItemTitle>{skill.name}</ItemTitle>
								<ItemDescription>{skill.description}</ItemDescription>
							</ItemContent>
							<ItemActions>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => {
										setDeleteTarget(skill.name);
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
							binding.display_name ||
							binding.slug ||
							binding.skillhub_skill_id ||
							t('panel.skill.unknownConfiguredSkill');
						return (
							<Item
								key={
									binding.binding_id ||
									`${binding.namespace ?? ''}/${binding.slug ?? name}`
								}
								variant="outline"
							>
								<ItemContent>
									<ItemTitle className="flex items-center gap-x-2">
										<span>{name}</span>
										<Badge variant="secondary">
											{t('panel.skill.configuredBadge')}
										</Badge>
									</ItemTitle>
									<ItemDescription>
										{binding.description ||
											[binding.namespace, binding.slug]
												.filter(Boolean)
												.join('/')}
									</ItemDescription>
								</ItemContent>
							</Item>
						);
					})}
				</div>
			)}

			<AddSkillDialog onAdd={onAdd} onUpload={onUpload}>
				<Button variant="default">
					<PlusCircle />
					{t('panel.skill.add')}
				</Button>
			</AddSkillDialog>

			<DeleteDialog
				open={deleteOpen}
				onOpenChange={setDeleteOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-mcp-delete.skillEntity'),
					name: deleteTarget ?? '',
				})}
				description={t('dialog-mcp-delete.skillDescription')}
				onConfirm={async () => {
					if (deleteTarget) await onRemove(deleteTarget);
				}}
			/>
		</div>
	);
}
