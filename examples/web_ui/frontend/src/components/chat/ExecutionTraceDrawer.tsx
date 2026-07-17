import { Activity, AlertCircle, Clock, Database, FileSearch, Wrench } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';

import { sessionApi } from '@/api';
import { ApiError } from '@/api/client';
import type { ExecutionTraceRecord } from '@/api/types';
import { mergeAdjacentTextBlockDeltas } from '@/components/chat/executionTraceUtils';
import { PanelEmpty } from '@/components/panel/PanelEmpty';
import { Badge } from '@/components/ui/badge';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet';
import { useTranslation } from '@/i18n/useI18n';
import { formatNumber, formatTime } from '@/utils/common';

interface ExecutionTraceDrawerProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	sessionId: string | null;
	agentId: string | null;
	replyId: string | null;
}

function formatDurationMs(value: unknown): string {
	if (typeof value !== 'number') return '-';
	const duration = Math.max(0, value);
	if (duration < 1000) return `${Math.round(duration)} ms`;
	return formatTime(duration / 1000);
}

function stringifyValue(value: unknown): string {
	if (value === null || value === undefined || value === '') return '-';
	if (typeof value === 'string') return value;
	if (typeof value === 'number' || typeof value === 'boolean') return String(value);
	return JSON.stringify(value, null, 2);
}

function jsonPreview(value: unknown): string {
	return JSON.stringify(value, null, 2);
}

function Field({ label, value }: { label: string; value: unknown }) {
	return (
		<div className="flex min-w-0 flex-col gap-1 rounded-sm border bg-muted/30 px-2 py-2">
			<span className="text-[11px] uppercase text-muted-foreground">{label}</span>
			<span className="min-w-0 break-words text-xs leading-5">{stringifyValue(value)}</span>
		</div>
	);
}

function Section({
	title,
	icon,
	children,
}: {
	title: string;
	icon: ReactNode;
	children: ReactNode;
}) {
	return (
		<section className="flex shrink-0 flex-col gap-3">
			<div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
				{icon}
				<span>{title}</span>
			</div>
			{children}
		</section>
	);
}

function Summary({ trace }: { trace: ExecutionTraceRecord }) {
	const { t } = useTranslation();
	const usage = trace.usage;
	const model = trace.model;
	const fallbackModel = trace.fallback_model;
	return (
		<Section title={t('diagnostics.summary')} icon={<Activity className="size-3.5" />}>
			<div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
				<Field label={t('diagnostics.status')} value={trace.status} />
				<Field label={t('diagnostics.duration')} value={formatDurationMs(trace.duration_ms)} />
				<Field label={t('diagnostics.model')} value={model?.model ?? model?.name ?? '-'} />
				<Field
					label={t('diagnostics.fallbackModel')}
					value={fallbackModel?.model ?? fallbackModel?.name ?? '-'}
				/>
				<Field label={t('diagnostics.provider')} value={model?.type ?? '-'} />
				<Field
					label={t('diagnostics.inputTokens')}
					value={usage ? formatNumber(usage.input_tokens ?? 0) : '-'}
				/>
				<Field
					label={t('diagnostics.outputTokens')}
					value={usage ? formatNumber(usage.output_tokens ?? 0) : '-'}
				/>
			</div>
			{trace.error ? (
				<div className="rounded-sm border border-destructive/30 bg-destructive/5 p-2 text-xs">
					<div className="mb-1 flex items-center gap-1.5 font-medium text-destructive">
						<AlertCircle className="size-3.5" />
						{t('diagnostics.error')}
					</div>
					<pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words">
						{jsonPreview(trace.error)}
					</pre>
				</div>
			) : null}
		</Section>
	);
}

function Stages({ stages }: { stages: Array<Record<string, unknown>> }) {
	const { t } = useTranslation();
	return (
		<Section title={t('diagnostics.stages')} icon={<Clock className="size-3.5" />}>
			{stages.length === 0 ? (
				<p className="text-xs text-muted-foreground">{t('diagnostics.emptySection')}</p>
			) : (
				<div className="flex flex-col gap-2">
					{stages.map((stage, index) => (
						<div
							key={`${String(stage.name ?? index)}-${index}`}
							className="flex min-w-0 flex-col gap-1 overflow-hidden rounded-sm border px-2 py-2 text-xs leading-5"
						>
							<div className="flex min-w-0 items-start justify-between gap-2">
								<div className="min-w-0 break-words font-medium leading-5">
									{stringifyValue(stage.name)}
								</div>
								<Badge variant="outline" className="h-fit shrink-0">
									{stringifyValue(stage.status)}
								</Badge>
							</div>
							<div className="text-muted-foreground">
								{formatDurationMs(stage.duration_ms)}
							</div>
							{stage.error ? (
								<pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">
									{jsonPreview(stage.error)}
								</pre>
							) : null}
						</div>
					))}
				</div>
			)}
		</Section>
	);
}

function ToolCalls({ toolCalls }: { toolCalls: Array<Record<string, unknown>> }) {
	const { t } = useTranslation();
	return (
		<Section title={t('diagnostics.toolCalls')} icon={<Wrench className="size-3.5" />}>
			{toolCalls.length === 0 ? (
				<p className="text-xs text-muted-foreground">{t('diagnostics.emptySection')}</p>
			) : (
				<div className="flex flex-col gap-2">
					{toolCalls.map((toolCall, index) => (
						<div
							key={`${String(toolCall.id ?? index)}-${index}`}
							className="min-w-0 overflow-hidden rounded-sm border px-2 py-2 text-xs leading-5"
						>
							<div className="flex min-w-0 items-start justify-between gap-2">
								<span className="min-w-0 break-words font-medium leading-5">
									{stringifyValue(toolCall.name ?? toolCall.id)}
								</span>
								<Badge variant="outline" className="h-fit shrink-0">
									{stringifyValue(toolCall.state)}
								</Badge>
							</div>
							<div className="mt-1 grid grid-cols-2 gap-1 text-muted-foreground">
								<span className="truncate">{stringifyValue(toolCall.id)}</span>
								<span className="text-right">
									{formatDurationMs(toolCall.duration_ms)}
								</span>
							</div>
							{toolCall.input_preview || toolCall.result_preview || toolCall.error ? (
								<pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">
									{jsonPreview({
										input: toolCall.input_preview,
										result: toolCall.result_preview,
										error: toolCall.error,
									})}
								</pre>
							) : null}
						</div>
					))}
				</div>
			)}
		</Section>
	);
}

function Events({ events }: { events: Array<Record<string, unknown>> }) {
	const { t } = useTranslation();
	const mergedEvents = mergeAdjacentTextBlockDeltas(events);
	return (
		<Section title={t('diagnostics.events')} icon={<Database className="size-3.5" />}>
			{mergedEvents.length === 0 ? (
				<p className="text-xs text-muted-foreground">{t('diagnostics.emptySection')}</p>
			) : (
				<div className="flex flex-col gap-2">
					{mergedEvents.map((event, index) => (
						<details
							key={index}
							className="min-w-0 overflow-hidden rounded-sm border px-2 py-2 text-xs leading-5"
						>
							<summary className="cursor-pointer select-none truncate leading-5">
								{stringifyValue(event.type ?? event.name ?? index)}
								{event.merged_count ? ` x${event.merged_count}` : ''}
							</summary>
							<pre className="mt-1 max-h-44 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2">
								{jsonPreview(event)}
							</pre>
						</details>
					))}
				</div>
			)}
		</Section>
	);
}

export function ExecutionTraceDrawer({
	open,
	onOpenChange,
	sessionId,
	agentId,
	replyId,
}: ExecutionTraceDrawerProps) {
	const { t } = useTranslation();
	const [trace, setTrace] = useState<ExecutionTraceRecord | null>(null);
	const [loading, setLoading] = useState(false);
	const [notFound, setNotFound] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!open || !sessionId || !agentId || !replyId) return;
		let alive = true;
		setLoading(true);
		setTrace(null);
		setNotFound(false);
		setError(null);

		sessionApi
			.diagnosticsByReply(sessionId, agentId, replyId)
			.then((nextTrace) => {
				if (!alive) return;
				setTrace(nextTrace);
			})
			.catch((err: unknown) => {
				if (!alive) return;
				if (err instanceof ApiError && err.status === 404) {
					setNotFound(true);
					return;
				}
				setError(err instanceof Error ? err.message : t('common.error'));
			})
			.finally(() => {
				if (alive) setLoading(false);
			});

		return () => {
			alive = false;
		};
	}, [open, sessionId, agentId, replyId, t]);

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent className="w-[min(42rem,calc(100vw-1rem))] gap-0 sm:max-w-[42rem]">
				<SheetHeader className="border-b px-4 py-3">
					<SheetTitle className="flex items-center gap-2">
						<Activity className="size-4" />
						{t('diagnostics.title')}
					</SheetTitle>
					<SheetDescription className="truncate">
						{replyId ?? t('diagnostics.noReply')}
					</SheetDescription>
				</SheetHeader>
				<div className="flex min-h-0 flex-1 flex-col gap-6 overflow-auto p-4">
					{loading ? (
						<div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
							{t('common.loading')}
						</div>
					) : notFound ? (
						<PanelEmpty
							icon={FileSearch}
							title={t('diagnostics.emptyTitle')}
							description={t('diagnostics.emptyDescription')}
						/>
					) : error ? (
						<PanelEmpty icon={AlertCircle} title={t('common.error')} description={error} />
					) : trace ? (
						<>
							<Summary trace={trace} />
							<Stages stages={trace.stages} />
							<ToolCalls toolCalls={trace.tool_calls} />
							<Events events={trace.events} />
						</>
					) : (
						<PanelEmpty
							icon={FileSearch}
							title={t('diagnostics.emptyTitle')}
							description={t('diagnostics.emptyDescription')}
						/>
					)}
				</div>
			</SheetContent>
		</Sheet>
	);
}
