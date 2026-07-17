import { AlertCircle, Brain, FileText, Files, Lock, RefreshCw, Save } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { sessionApi } from '@/api';
import { ApiError } from '@/api/client';
import type { MemoryBackend, MemoryFileResponse, MemoryTreeEntry } from '@/api/types';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface LongTermMemoryPanelProps {
	sessionId: string | null;
	agentId: string | null;
}

const BACKENDS: MemoryBackend[] = ['agentic', 'reme', 'mem0'];

function formatBytes(size: number | null): string {
	if (size === null) return '-';
	if (size < 1024) return `${size} B`;
	if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
	return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function errorText(err: unknown, fallback: string): string {
	if (err instanceof ApiError) return err.detail;
	if (err instanceof Error) return err.message;
	return fallback;
}

export function LongTermMemoryPanel({ sessionId, agentId }: LongTermMemoryPanelProps) {
	const { t } = useTranslation();
	const [backend, setBackend] = useState<MemoryBackend>('agentic');
	const [entries, setEntries] = useState<MemoryTreeEntry[]>([]);
	const [treeLoading, setTreeLoading] = useState(false);
	const [treeError, setTreeError] = useState<string | null>(null);
	const [treeUnavailable, setTreeUnavailable] = useState<string | null>(null);
	const [selectedPath, setSelectedPath] = useState<string | null>(null);
	const [file, setFile] = useState<MemoryFileResponse | null>(null);
	const [content, setContent] = useState('');
	const [fileLoading, setFileLoading] = useState(false);
	const [fileError, setFileError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);

	const selectedEntry = useMemo(
		() => entries.find((entry) => entry.path === selectedPath) ?? null,
		[entries, selectedPath],
	);
	const dirty = !!file && content !== file.content;
	const editable = !!file && file.editable && !file.readonly && backend === 'agentic';

	const loadTree = useCallback(async () => {
		if (!sessionId || !agentId) {
			setEntries([]);
			setSelectedPath(null);
			setFile(null);
			setContent('');
			setTreeError(null);
			setTreeUnavailable(null);
			return;
		}
		setTreeLoading(true);
		setTreeError(null);
		setTreeUnavailable(null);
		try {
			const tree = await sessionApi.memoryTree(sessionId, agentId, backend);
			setEntries(tree.entries);
			setSelectedPath(null);
			setFile(null);
			setContent('');
		} catch (err) {
			setEntries([]);
			if (err instanceof ApiError && (err.status === 404 || err.status === 503)) {
				setTreeUnavailable(errorText(err, t('panel.memory.unavailableDescription')));
			} else {
				setTreeError(errorText(err, t('common.error')));
			}
		} finally {
			setTreeLoading(false);
		}
	}, [agentId, backend, sessionId, t]);

	const loadFile = useCallback(
		async (path: string) => {
			if (!sessionId || !agentId) return;
			setSelectedPath(path);
			setFile(null);
			setContent('');
			setFileLoading(true);
			setFileError(null);
			try {
				const nextFile = await sessionApi.memoryFile(sessionId, agentId, backend, path);
				setFile(nextFile);
				setContent(nextFile.content);
			} catch (err) {
				setFileError(errorText(err, t('common.error')));
			} finally {
				setFileLoading(false);
			}
		},
		[agentId, backend, sessionId, t],
	);

	const saveFile = async () => {
		if (!sessionId || !agentId || !file || !editable || !dirty) return;
		setSaving(true);
		setFileError(null);
		try {
			const nextFile = await sessionApi.updateMemoryFile(sessionId, agentId, backend, file.path, {
				content,
			});
			setFile(nextFile);
			setContent(nextFile.content);
			setEntries((current) =>
				current.map((entry) =>
					entry.path === nextFile.path
						? {
								...entry,
								size: nextFile.size,
								editable: nextFile.editable,
								readonly: nextFile.readonly,
							}
						: entry,
				),
			);
			toast.success(t('panel.memory.saved'));
		} catch (err) {
			setFileError(errorText(err, t('common.error')));
		} finally {
			setSaving(false);
		}
	};

	useEffect(() => {
		loadTree();
	}, [loadTree]);

	return (
		<div className="flex min-h-0 flex-1 flex-col gap-2 text-sm">
			<div className="flex items-center gap-1.5">
				<Select
					value={backend}
					onValueChange={(value) => setBackend(value as MemoryBackend)}
					disabled={!sessionId}
				>
					<SelectTrigger size="sm" className="w-32">
						<SelectValue />
					</SelectTrigger>
					<SelectContent align="start">
						{BACKENDS.map((item) => (
							<SelectItem key={item} value={item}>
								{t(`panel.memory.backend.${item}`)}
							</SelectItem>
						))}
					</SelectContent>
				</Select>
				<Button
					type="button"
					variant="ghost"
					size="icon-sm"
					aria-label={t('panel.memory.refresh')}
					disabled={!sessionId || treeLoading}
					onClick={loadTree}
				>
					<RefreshCw className={cn(treeLoading && 'animate-spin')} />
				</Button>
				<div className="ml-auto flex items-center gap-1">
					{dirty ? <Badge variant="outline">{t('panel.memory.dirty')}</Badge> : null}
					{!editable && file ? (
						<Badge variant="secondary" className="gap-1">
							<Lock className="size-3" />
							{t('common.readOnly')}
						</Badge>
					) : null}
				</div>
			</div>

			{treeError ? (
				<div className="rounded-sm border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
					{treeError}
				</div>
			) : null}

			<div className="grid min-h-0 flex-1 grid-rows-[minmax(7rem,0.42fr)_minmax(10rem,1fr)] gap-2">
				<div className="min-h-0 overflow-auto rounded-sm border p-1">
					{treeLoading ? (
						<div className="flex h-full items-center justify-center text-xs text-muted-foreground">
							{t('common.loading')}
						</div>
					) : treeUnavailable ? (
						<PanelEmpty
							icon={AlertCircle}
							title={t('panel.memory.unavailableTitle')}
							description={treeUnavailable}
							className="min-h-32"
						/>
					) : entries.length === 0 ? (
						<PanelEmpty
							icon={Brain}
							title={t('panel.memory.emptyTitle')}
							description={t('panel.memory.emptyDescription')}
							className="min-h-32"
						/>
					) : (
						<div className="flex flex-col gap-1">
							{entries.map((entry) => {
								const isFile = entry.type === 'file';
								const selected = selectedPath === entry.path;
								const EntryIcon = isFile ? FileText : Files;
								return (
									<Item
										key={entry.path}
										variant="outline"
										data-selected={selected || undefined}
										className={cn(
											'min-h-0 gap-2 px-2 py-1.5',
											isFile && 'cursor-pointer',
											!isFile && 'opacity-70',
										)}
										onClick={() => {
											if (isFile) loadFile(entry.path);
										}}
									>
										<EntryIcon className="size-3.5 shrink-0 text-muted-foreground" />
										<ItemContent className="min-w-0 gap-0">
											<ItemTitle className="truncate text-xs">
												{entry.name}
											</ItemTitle>
											<ItemDescription className="text-[11px]">
												{entry.type} / {formatBytes(entry.size)}
											</ItemDescription>
										</ItemContent>
										{entry.editable ? (
											<Badge variant="outline" className="text-[10px]">
												{t('common.edit')}
											</Badge>
										) : null}
									</Item>
								);
							})}
						</div>
					)}
				</div>

				<div className="flex min-h-0 flex-col rounded-sm border">
					<div className="flex items-center gap-2 border-b px-2 py-1">
						<div className="min-w-0 flex-1">
							<div className="truncate text-xs font-medium">
								{file?.path ??
									selectedEntry?.path ??
									t('panel.memory.noFileSelected')}
							</div>
							<div className="text-[11px] text-muted-foreground">
								{file ? formatBytes(file.size) : t('panel.memory.selectFile')}
							</div>
						</div>
						<Button
							type="button"
							size="sm"
							variant="outline"
							className="h-7 gap-1 px-2"
							disabled={!editable || !dirty || saving}
							onClick={saveFile}
						>
							<Save className="size-3.5" />
							{saving ? t('common.saving') : t('common.save')}
						</Button>
					</div>
					<div className="min-h-0 flex-1 overflow-auto p-2">
						{fileLoading ? (
							<div className="flex h-full items-center justify-center text-xs text-muted-foreground">
								{t('common.loading')}
							</div>
						) : fileError ? (
							<div className="rounded-sm border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
								{fileError}
							</div>
						) : file ? (
							<Textarea
								value={content}
								readOnly={!editable}
								onChange={(event) => setContent(event.target.value)}
								className="h-full min-h-full resize-none border-0 bg-transparent p-0 font-mono text-xs focus-visible:ring-0"
							/>
						) : (
							<PanelEmpty
								icon={FileText}
								title={t('panel.memory.noFileSelected')}
								description={t('panel.memory.selectFile')}
							/>
						)}
					</div>
				</div>
			</div>
		</div>
	);
}
