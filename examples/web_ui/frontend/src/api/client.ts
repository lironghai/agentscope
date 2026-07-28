import { toast } from 'sonner';

export const getBaseUrl = () => localStorage.getItem('server_url') ?? '';
export const getUserId = () => localStorage.getItem('username') ?? '';

/**
 * Structured error thrown for non-2xx HTTP responses.
 * `message` contains the human-readable detail extracted from the backend.
 */
export class ApiError extends Error {
	readonly status: number;
	readonly detail: string;

	constructor(status: number, detail: string) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

interface RequestOptions {
	method?: string;
	body?: unknown;
	params?: Record<string, string>;
	headers?: Record<string, string>;
	signal?: AbortSignal;
	/** When true, suppresses the automatic error toast. Useful when the caller shows its own inline error UI. */
	silent?: boolean;
}

function buildHeaders(hasBody: boolean, isFormData = false): Record<string, string> {
	const headers: Record<string, string> = { 'X-User-ID': getUserId() };
	if (hasBody && !isFormData) headers['Content-Type'] = 'application/json';
	return headers;
}

/** Parse the response body and extract the `detail` field if the backend returned JSON. */
async function extractErrorDetail(res: Response): Promise<string> {
	const text = await res.text();
	try {
		const json = JSON.parse(text) as { detail?: unknown };
		if (typeof json.detail === 'string') return json.detail;
		if (json.detail !== undefined) return JSON.stringify(json.detail);
	} catch {
		// not JSON – fall through
	}
	return text || res.statusText;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
	const {
		method = 'GET',
		body,
		params,
		headers: extraHeaders,
		signal,
		silent = false,
	} = options;
	const url = new URL(path, getBaseUrl());
	if (params) {
		Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
	}

	const isFormData = body instanceof FormData;
	const res = await fetch(url.toString(), {
		method,
		headers: { ...buildHeaders(body !== undefined, isFormData), ...extraHeaders },
		body:
			body === undefined
				? undefined
				: isFormData
					? body
					: JSON.stringify(body),
		signal,
	});

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		const error = new ApiError(res.status, detail);
		if (!silent) toast.error(detail);
		throw error;
	}

	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

async function streamRequest(
	path: string,
	options: RequestOptions & { signal?: AbortSignal } = {},
): Promise<Response> {
	const { method = 'GET', body, params, signal, silent = false } = options;
	const url = new URL(path, getBaseUrl());
	if (params) {
		Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
	}

	const res = await fetch(url.toString(), {
		method,
		headers: buildHeaders(body !== undefined),
		body: body ? JSON.stringify(body) : undefined,
		signal,
	});

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		const error = new ApiError(res.status, detail);
		if (!silent) toast.error(detail);
		throw error;
	}

	return res;
}

export const client = {
	get: <T>(
		path: string,
		params?: Record<string, string>,
		options?: { headers?: Record<string, string>; signal?: AbortSignal; silent?: boolean },
	) =>
		request<T>(path, {
			method: 'GET',
			params,
			headers: options?.headers,
			signal: options?.signal,
			silent: options?.silent,
		}),
	post: <T>(
		path: string,
		body?: unknown,
		params?: Record<string, string>,
		options?: { headers?: Record<string, string>; signal?: AbortSignal; silent?: boolean },
	) =>
		request<T>(path, {
			method: 'POST',
			body,
			params,
			headers: options?.headers,
			signal: options?.signal,
			silent: options?.silent,
		}),
	put: <T>(
		path: string,
		body?: unknown,
		params?: Record<string, string>,
		options?: { headers?: Record<string, string>; silent?: boolean },
	) =>
		request<T>(path, {
			method: 'PUT',
			body,
			params,
			headers: options?.headers,
			silent: options?.silent,
		}),
	patch: <T>(
		path: string,
		body?: unknown,
		params?: Record<string, string>,
		options?: { headers?: Record<string, string>; silent?: boolean },
	) =>
		request<T>(path, {
			method: 'PATCH',
			body,
			params,
			headers: options?.headers,
			silent: options?.silent,
		}),
	delete: <T = void>(path: string, params?: Record<string, string>) =>
		request<T>(path, { method: 'DELETE', params }),
	stream: (path: string, options?: RequestOptions & { signal?: AbortSignal }) =>
		streamRequest(path, options),
};
