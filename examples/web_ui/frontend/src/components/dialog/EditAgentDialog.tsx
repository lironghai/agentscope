import { CircleAlert, Loader2, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { agentApi } from '@/api';
import type {
	AgentMcpBinding,
	AgentListView,
	AgentShareGrant,
	AgentSkillBinding,
	AgentView,
	AgentToolConfig,
	ContextConfig,
	InviteConfig,
	ReActConfig,
	ShareConfig,
} from '@/api';
import { AgentCatalogBindings } from '@/components/form/AgentCatalogBindings';
import {
	AgentFormFields,
	defaultAgentFormValues,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useAgentSchema } from '@/hooks/useAgentSchema';
import { formatApiErrorForAlert } from '@/lib/api-error';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	agent: AgentListView;
	onUpdated?: (agent: AgentView) => void;
}

export function EditAgentDialog({ open, onOpenChange, agent, onUpdated }: Props) {
	const { update } = useAgents();
	const { t } = useTranslation();
	const { schema } = useAgentSchema();
	const [submitting, setSubmitting] = useState(false);
	const [detail, setDetail] = useState<AgentView | null>(null);
	const [values, setValues] = useState<AgentFormValues | null>(null);
	const [shareGrants, setShareGrants] = useState<AgentShareGrant[]>([]);
	const [skillBindings, setSkillBindings] = useState<AgentSkillBinding[]>([]);
	const [mcpBindings, setMcpBindings] = useState<AgentMcpBinding[]>([]);
	const [errorMsg, setErrorMsg] = useState('');

	useEffect(() => {
		if (!open) {
			setDetail(null);
			setValues(null);
			setShareGrants([]);
			setSkillBindings([]);
			setMcpBindings([]);
			setErrorMsg('');
			return;
		}
		let cancelled = false;
		setDetail(null);
		setValues(null);
		setErrorMsg('');
		agentApi
			.get(agent.id)
			.then((res) => {
				if (!cancelled) setDetail(res);
			})
			.catch((e) => {
				if (!cancelled) setErrorMsg(formatApiErrorForAlert(e));
			});
		return () => {
			cancelled = true;
		};
	}, [open, agent.id]);

	useEffect(() => {
		if (!open || !schema || !detail) {
			return;
		}
		// Start from schema defaults, then overlay the existing agent's data so
		// any unset fields fall back to defaults rather than empty.
		const base = defaultAgentFormValues(schema);
		const d = detail.data;
		setValues({
			identity: {
				...base.identity,
				name: d.name,
				system_prompt: d.system_prompt,
			},
			context_config: { ...base.context_config, ...(d.context_config ?? {}) },
			react_config: { ...base.react_config, ...(d.react_config ?? {}) },
			invite_config: { ...base.invite_config, ...(d.invite_config ?? {}) },
			share_config: { ...base.share_config, ...(d.share_config ?? {}) },
			tool_config: { ...base.tool_config, ...(d.tool_config ?? {}) },
		});
		setShareGrants(d.share_grants ?? []);
		setSkillBindings(d.skill_bindings ?? []);
		setMcpBindings(d.mcp_bindings ?? []);
		setErrorMsg('');
	}, [open, schema, detail]);

	const handleChange = (section: AgentSection, key: string, value: SchemaFormValue) => {
		setErrorMsg('');
		setValues((prev) =>
			prev ? { ...prev, [section]: { ...prev[section], [key]: value } } : prev,
		);
	};

	const handleSubmit = async () => {
		if (!values) return;
		const name = (values.identity.name as string | undefined)?.trim();
		if (!name) return;
		setErrorMsg('');
		setSubmitting(true);
		try {
			const updated = await update(
				agent.id,
				{
					name,
					system_prompt: values.identity.system_prompt as string | undefined,
					context_config: values.context_config as unknown as ContextConfig,
					react_config: values.react_config as unknown as ReActConfig,
					invite_config: values.invite_config as unknown as InviteConfig,
					share_config: values.share_config as unknown as ShareConfig,
					share_grants: shareGrants,
					tool_config: values.tool_config as unknown as AgentToolConfig,
					skill_bindings: skillBindings,
					mcp_bindings: mcpBindings,
				},
				{ silent: true },
			);
			onOpenChange(false);
			onUpdated?.(updated);
		} catch (e) {
			setErrorMsg(formatApiErrorForAlert(e));
		} finally {
			setSubmitting(false);
		}
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!max-h-[88dvh] !w-[calc(100vw-2rem)] !max-w-[calc(100vw-2rem)] sm:!w-[92vw] lg:!w-[960px] lg:!max-w-[960px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-edit.title')}</DialogTitle>
					<DialogDescription className="sr-only">
						{t('dialog-agent-edit.description')}
					</DialogDescription>
				</DialogHeader>
				<div className="no-scrollbar -mx-4 max-h-[calc(88dvh-9rem)] overflow-y-auto px-4">
					{schema && values ? (
						<>
							<AgentFormFields schema={schema} values={values} onChange={handleChange} />
							<AgentCatalogBindings
								skillBindings={skillBindings}
								mcpBindings={mcpBindings}
								onSkillBindingsChange={setSkillBindings}
								onMcpBindingsChange={setMcpBindings}
							/>
						</>
					) : (
						<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
					)}
				</div>
				{errorMsg && (
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription className="whitespace-pre-wrap">
							{errorMsg}
						</AlertDescription>
					</Alert>
				)}
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => onOpenChange(false)}
						disabled={submitting}
					>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={!nameValid || submitting || !schema || !values}
					>
						{submitting ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<Save className="size-3.5" />
						)}
						{submitting ? t('common.saving') : t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
