import { ChevronDown, Plug, Search } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, UIEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { globalCatalogApi, type SkillHubCatalogItem } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

type McpTab = 'servers' | 'internal';
const PAGE_SIZE = 24;
const SCROLL_LOAD_THRESHOLD = 240;

function readString(item: SkillHubCatalogItem, keys: string[], fallback = '') {
	for (const key of keys) {
		const value = item[key];
		if (typeof value === 'string' && value.trim()) return value;
		if (typeof value === 'number') return String(value);
	}
	return fallback;
}

function readTags(item: SkillHubCatalogItem) {
	const value = item.tags;
	if (!Array.isArray(value)) return [];
	return value.filter((tag): tag is string => typeof tag === 'string');
}

function McpCard({ server }: { server: SkillHubCatalogItem }) {
	const name = readString(server, ['name', 'id'], 'Unnamed MCP');
	const description = readString(server, ['description', 'summary']);
	const provider = readString(server, ['provider', 'team', 'ownerEmail']);
	const category = readString(server, ['category', 'visibility']);
	const authType = readString(server, ['authType', 'auth_type']);
	const transport = readString(server, ['transport']);
	const tags = readTags(server).slice(0, 5);

	return (
		<article className="rounded-lg border bg-background p-4">
			<div className="flex items-start justify-between gap-3">
				<div className="min-w-0">
					<h2 className="truncate text-sm font-semibold">{name}</h2>
					{provider ? (
						<p className="mt-1 truncate text-xs text-muted-foreground">{provider}</p>
					) : null}
				</div>
				{category ? (
					<Badge variant="secondary" className="shrink-0">
						{category}
					</Badge>
				) : null}
			</div>
			{description ? (
				<p className="mt-3 line-clamp-3 text-sm text-muted-foreground">{description}</p>
			) : null}
			<div className="mt-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
				{authType ? <span>{authType}</span> : null}
				{transport ? <span>{transport}</span> : null}
				{tags.map((tag) => (
					<Badge key={tag} variant="outline">
						{tag}
					</Badge>
				))}
			</div>
		</article>
	);
}

function getMcpKey(server: SkillHubCatalogItem, fallback: string | number) {
	const id = readString(server, ['id']);
	const name = readString(server, ['name']);
	const provider = readString(server, ['provider', 'team', 'ownerEmail']);
	if (id) return id;
	if (provider && name) return `${provider}/${name}`;
	return name || String(fallback);
}

function appendUniqueMcps(
	current: SkillHubCatalogItem[],
	incoming: SkillHubCatalogItem[],
) {
	const seen = new Set(current.map((item, index) => getMcpKey(item, index)));
	const next = [...current];
	incoming.forEach((item, index) => {
		const key = getMcpKey(item, `${current.length}-${index}`);
		if (seen.has(key)) return;
		seen.add(key);
		next.push(item);
	});
	return next;
}

export function McpsPage() {
	const { t } = useTranslation();
	const [tab, setTab] = useState<McpTab>('servers');
	const [query, setQuery] = useState('');
	const [submittedQuery, setSubmittedQuery] = useState('');
	const [items, setItems] = useState<SkillHubCatalogItem[]>([]);
	const [total, setTotal] = useState(0);
	const [page, setPage] = useState(0);
	const [loading, setLoading] = useState(false);
	const [loadingMore, setLoadingMore] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const appendRequestInFlight = useRef(false);

	const loadPage = useCallback(async (nextPage: number, append = false) => {
		if (append && appendRequestInFlight.current) return;
		if (append) appendRequestInFlight.current = true;
		if (append) {
			setLoadingMore(true);
		} else {
			setLoading(true);
		}
		setError(null);
		try {
			const response =
				tab === 'servers'
					? await globalCatalogApi.listMcpServers({
							search: submittedQuery,
							page: nextPage,
							size: PAGE_SIZE,
						})
					: await globalCatalogApi.listInternalMcpServers({
							search: submittedQuery,
							page: nextPage,
							size: PAGE_SIZE,
						});
			setItems((current) =>
				append ? appendUniqueMcps(current, response.items) : response.items,
			);
			setTotal(response.total);
			setPage(response.page);
		} catch (e) {
			if (!append) {
				setItems([]);
				setTotal(0);
				setPage(0);
			}
			setError(e instanceof Error ? e.message : t('globalCatalog.error'));
		} finally {
			if (append) {
				setLoadingMore(false);
				appendRequestInFlight.current = false;
			} else {
				setLoading(false);
			}
		}
	}, [submittedQuery, tab, t]);

	useEffect(() => {
		loadPage(0);
	}, [loadPage]);

	const hasMore = items.length < total;

	const loadMore = useCallback(() => {
		if (loading || loadingMore || !hasMore) return;
		void loadPage(page + 1, true);
	}, [hasMore, loadPage, loading, loadingMore, page]);

	const handleScroll = useCallback((event: UIEvent<HTMLElement>) => {
		const target = event.currentTarget;
		const remaining =
			target.scrollHeight - target.scrollTop - target.clientHeight;
		if (remaining <= SCROLL_LOAD_THRESHOLD) {
			loadMore();
		}
	}, [loadMore]);

	const handleSubmit = (event: FormEvent) => {
		event.preventDefault();
		const nextQuery = query.trim();
		if (nextQuery === submittedQuery) {
			void loadPage(0);
			return;
		}
		setSubmittedQuery(nextQuery);
	};

	return (
		<main className="flex size-full flex-col overflow-hidden">
			<header className="border-b px-6 py-5">
				<div className="flex flex-col gap-4">
					<div>
						<h1 className="text-lg font-semibold">{t('globalCatalog.mcps.title')}</h1>
						<p className="mt-1 text-sm text-muted-foreground">
							{t('globalCatalog.mcps.subtitle')}
						</p>
					</div>
					<form className="flex max-w-xl gap-2" onSubmit={handleSubmit}>
						<Input
							value={query}
							onChange={(event) => setQuery(event.target.value)}
							placeholder={t('globalCatalog.mcps.searchPlaceholder')}
						/>
						<Button type="submit" variant="outline" disabled={loading}>
							<Search className="size-4" />
							{t('globalCatalog.search')}
						</Button>
					</form>
				</div>
			</header>
			<Tabs
				value={tab}
				onValueChange={(value) => setTab(value as McpTab)}
				className="min-h-0 flex-1 gap-0"
			>
				<div className="border-b px-6 py-3">
					<TabsList>
						<TabsTrigger value="servers">
							{t('globalCatalog.mcps.catalogTab')}
						</TabsTrigger>
						<TabsTrigger value="internal">
							{t('globalCatalog.mcps.internalTab')}
						</TabsTrigger>
					</TabsList>
				</div>
				<TabsContent
					value={tab}
					className="min-h-0 overflow-y-auto p-6"
					onScroll={handleScroll}
				>
					<div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
						<span>
							{t('globalCatalog.shown', {
								shown: items.length,
								total,
							})}
						</span>
						{loading ? <Spinner /> : null}
					</div>
					{error ? (
						<Empty className="border-none">
							<EmptyHeader>
								<EmptyMedia variant="icon">
									<Plug />
								</EmptyMedia>
								<EmptyTitle>{t('globalCatalog.errorTitle')}</EmptyTitle>
								<EmptyDescription>{error}</EmptyDescription>
							</EmptyHeader>
						</Empty>
					) : items.length === 0 && !loading ? (
						<Empty className="border-none">
							<EmptyHeader>
								<EmptyMedia variant="icon">
									<Plug />
								</EmptyMedia>
								<EmptyTitle>{t('globalCatalog.mcps.emptyTitle')}</EmptyTitle>
								<EmptyDescription>
									{t('globalCatalog.mcps.emptyDescription')}
								</EmptyDescription>
							</EmptyHeader>
						</Empty>
					) : (
						<>
							<div className="grid grid-cols-1 gap-3 xl:grid-cols-2 2xl:grid-cols-3">
								{items.map((server, index) => (
									<McpCard
										key={getMcpKey(server, index)}
										server={server}
									/>
								))}
							</div>
							<div className="flex min-h-16 items-center justify-center py-5">
								{hasMore ? (
									<Button
										type="button"
										variant="outline"
										onClick={loadMore}
										disabled={loadingMore}
									>
										{loadingMore ? (
											<Spinner />
										) : (
											<ChevronDown className="size-4" />
										)}
										{loadingMore
											? t('globalCatalog.loadingMore')
											: t('globalCatalog.loadMore')}
									</Button>
								) : total > 0 ? (
									<span className="text-sm text-muted-foreground">
										{t('globalCatalog.allLoaded')}
									</span>
								) : null}
							</div>
						</>
					)}
				</TabsContent>
			</Tabs>
		</main>
	);
}
