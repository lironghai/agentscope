import { CircleAlert, Loader2, PlusCircle, Upload } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface AddSkillDialogProps {
	children: ReactNode;
	onAdd: (skillPath: string) => Promise<void>;
	onUpload?: (file: File) => Promise<void>;
}

export function AddSkillDialog({ children, onAdd, onUpload }: AddSkillDialogProps) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [skillPath, setSkillPath] = useState('');
	const [skillFile, setSkillFile] = useState<File | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const handleSubmit = async () => {
		if (!skillPath.trim() && !skillFile) return;
		setLoading(true);
		setError(null);
		try {
			if (skillFile) {
				if (!onUpload) throw new Error(t('dialog-skill-add.uploadUnavailable'));
				await onUpload(skillFile);
			} else {
				await onAdd(skillPath.trim());
			}
			setSkillPath('');
			setSkillFile(null);
			setOpen(false);
		} catch (e) {
			setError((e as Error).message);
		} finally {
			setLoading(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-skill-add.title')}</DialogTitle>
					<DialogDescription>{t('dialog-skill-add.description')}</DialogDescription>
				</DialogHeader>
				<div className="flex flex-col gap-y-2">
					<Label htmlFor="skill-path">{t('dialog-skill-add.pathLabel')}</Label>
					<Input
						id="skill-path"
						placeholder={t('dialog-skill-add.pathPlaceholder')}
						value={skillPath}
						disabled={skillFile !== null}
						onChange={(e) => setSkillPath(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === 'Enter') handleSubmit();
						}}
					/>
					<Label htmlFor="skill-file">{t('dialog-skill-add.fileLabel')}</Label>
					<div className="flex min-h-10 items-center gap-2 rounded-lg border border-input bg-background px-2 py-1.5">
						<Input
							id="skill-file"
							type="file"
							accept=".zip,application/zip,application/x-zip-compressed"
							disabled={skillPath.trim().length > 0}
							className="sr-only"
							onChange={(e) => setSkillFile(e.target.files?.[0] ?? null)}
						/>
						<Label
							htmlFor="skill-file"
							aria-disabled={skillPath.trim().length > 0}
							className={cn(
								'inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-muted px-3 text-sm font-medium transition-colors hover:bg-muted/80',
								skillPath.trim().length > 0 &&
									'pointer-events-none cursor-not-allowed opacity-50',
							)}
						>
							<Upload className="size-3.5" />
							{t('dialog-skill-add.fileLabel')}
						</Label>
						<span className="text-muted-foreground min-w-0 flex-1 truncate text-sm">
							{skillFile?.name ??
								t('dialog-skill-add.noFileSelected', {
									defaultValue: 'No file selected',
								})}
						</span>
					</div>
					<p className="text-muted-foreground text-xs">
						{t('dialog-skill-add.fileDescription')}
					</p>
					{error && <p className="text-destructive text-sm">{error}</p>}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={() => setOpen(false)} disabled={loading}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={loading || (!skillPath.trim() && !skillFile)}
					>
						{loading ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : skillFile ? (
							<Upload className="size-3.5" />
						) : (
							<PlusCircle className="size-3.5" />
						)}
						{loading ? t('dialog-mcp-create.adding') : t('common.add')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
