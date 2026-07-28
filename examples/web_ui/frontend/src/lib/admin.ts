import { getUserId } from '@/api/client';

const DEFAULT_ADMIN_USER_IDS = ['admin'];

function configuredAdminUserIds(): string[] {
	const raw = import.meta.env.VITE_ADMIN_USER_IDS as string | undefined;
	if (!raw) return DEFAULT_ADMIN_USER_IDS;
	const ids = raw
		.split(',')
		.map((id) => id.trim())
		.filter(Boolean);
	return ids.length > 0 ? ids : DEFAULT_ADMIN_USER_IDS;
}

export function isCurrentUserAdmin(): boolean {
	return configuredAdminUserIds().includes(getUserId());
}
