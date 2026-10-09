/**
 * The accounts / visits / admin API.
 *
 * Every call is same-origin relative: nginx (and the dev Vite proxy) forwards
 * `/auth/*`, `/me/*` and `/admin/*` to the agent, so nothing hardcodes a host.
 *
 * Identity rides in an HttpOnly cookie the browser attaches itself — this module
 * never reads or stores the session token, which is the whole point of using a
 * cookie rather than localStorage. Every call still sends `X-Client-Id`: on
 * register/login the agent *adopts* that anonymous client, so routes and
 * preferences saved before signing in stay reachable.
 *
 * Failures become an `AccountApiError` carrying the agent's machine code, so a
 * caller can tell «the store is down» (`storage_unavailable`) from «wrong
 * password» (`invalid_credentials`) without parsing prose.
 */

import { getClientId } from '@/utils/client-id';

import {
  STORAGE_DOWN_CODES,
  type AccountApiErrorCode,
  type AccountUser,
  type AdminPlaceInput,
  type AdminPlaceList,
  type AdminStats,
  type AdminUser,
  type AdminUserList,
  type AuthState,
  type Place,
  type UserRole,
  type VisitedList,
  type VisitedPlace,
} from './types';

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

const CLIENT_ID_HEADER = 'X-Client-Id';

/** A failed account call, carrying the agent's machine code rather than prose. */
export class AccountApiError extends Error {
  readonly code: AccountApiErrorCode;
  readonly status: number | null;

  constructor(
    code: AccountApiErrorCode,
    message: string,
    status: number | null = null
  ) {
    super(message);
    this.name = 'AccountApiError';
    this.code = code;
    this.status = status;
  }
}

/** «The server cannot be reached / does not store» — never «the data is gone». */
export const isStorageDown = (error: unknown): boolean =>
  error instanceof AccountApiError && STORAGE_DOWN_CODES.includes(error.code);

const KNOWN_CODES: readonly string[] = [
  'storage_unavailable',
  'not_authenticated',
  'not_admin',
  'invalid_credentials',
  'email_taken',
  'weak_password',
  'invalid_email',
  'user_not_found',
  'place_not_found',
  'source_taken',
  'last_admin',
  'self_role',
  'self_delete',
  'invalid_request',
];

const isKnownCode = (value: unknown): value is AccountApiErrorCode =>
  typeof value === 'string' && KNOWN_CODES.includes(value);

const reasonFromBody = (body: unknown): AccountApiErrorCode | null => {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  for (const key of ['reason', 'code', 'error_code']) {
    const value = record[key];
    if (isKnownCode(value)) return value;
  }
  return null;
};

const codeFromStatus = (status: number): AccountApiErrorCode => {
  if (status === 503) return 'storage_unavailable';
  if (status === 401) return 'not_authenticated';
  if (status === 403) return 'not_admin';
  if (status === 404) return 'place_not_found';
  if (status >= 500) return 'server_error';
  return 'bad_response';
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set(CLIENT_ID_HEADER, getClientId());
  if (init.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`${AGENT_URL}${path}`, {
      ...init,
      headers,
      // The session cookie must travel; same-origin needs no CORS for it, but
      // `include` also covers a remote VITE_AGENT_URL.
      credentials: 'include',
    });
  } catch {
    // The request never reached the agent: a different failure from a refusal.
    throw new AccountApiError(
      'network_unavailable',
      'агент недоступен — попробуйте позже'
    );
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const code = reasonFromBody(body) ?? codeFromStatus(response.status);
    throw new AccountApiError(
      code,
      `агент ответил ${response.status}`,
      response.status
    );
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new AccountApiError(
      'bad_response',
      'агент ответил не JSON',
      response.status
    );
  }
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const asRole = (value: unknown): UserRole =>
  value === 'admin' ? 'admin' : 'user';

const normalizeUser = (value: unknown): AccountUser | null => {
  const record = asRecord(value);
  if (!record || typeof record.id !== 'string') return null;
  return {
    id: record.id,
    email: typeof record.email === 'string' ? record.email : '',
    display_name:
      typeof record.display_name === 'string' ? record.display_name : null,
    role: asRole(record.role),
    created_at:
      typeof record.created_at === 'string' ? record.created_at : null,
  };
};

const normalizeAuthState = (body: unknown): AuthState => {
  const record = asRecord(body);
  const user = normalizeUser(record?.user);
  return {
    authenticated: record?.authenticated === true && user !== null,
    user,
  };
};

const normalizeAdminUser = (value: unknown): AdminUser | null => {
  const base = normalizeUser(value);
  if (!base) return null;
  const record = asRecord(value) ?? {};
  const num = (v: unknown) =>
    typeof v === 'number' && Number.isFinite(v) ? v : 0;
  return {
    ...base,
    client_id: typeof record.client_id === 'string' ? record.client_id : null,
    last_login_at:
      typeof record.last_login_at === 'string' ? record.last_login_at : null,
    saved_routes: num(record.saved_routes),
    visited: num(record.visited),
  };
};

/** The agent's catalogue place payload, passed through with only shape checks. */
const normalizePlace = (value: unknown): Place | null => {
  const record = asRecord(value);
  if (!record || typeof record.place_id !== 'number') return null;
  return record as unknown as Place;
};

const asItems = (body: unknown): unknown[] => {
  const record = asRecord(body);
  const items = Array.isArray(body) ? body : (record?.items ?? []);
  return Array.isArray(items) ? items : [];
};

const asTotal = (body: unknown, fallback: number): number => {
  const record = asRecord(body);
  return typeof record?.total === 'number' ? record.total : fallback;
};

export const getAuthState = async (): Promise<AuthState> =>
  normalizeAuthState(await request<unknown>('/auth/me'));

export async function registerAccount(input: {
  email: string;
  password: string;
  display_name?: string | null;
}): Promise<AccountUser> {
  const user = normalizeUser(
    await request<unknown>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  );
  if (!user)
    throw new AccountApiError('bad_response', 'агент ответил не пользователем');
  return user;
}

export async function loginAccount(input: {
  email: string;
  password: string;
}): Promise<AccountUser> {
  const user = normalizeUser(
    await request<unknown>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  );
  if (!user)
    throw new AccountApiError('bad_response', 'агент ответил не пользователем');
  return user;
}

export const logoutAccount = (): Promise<void> =>
  request<void>('/auth/logout', { method: 'POST' });

const normalizeVisited = (value: unknown): VisitedPlace | null => {
  const place = normalizePlace(value);
  if (!place) return null;
  const record = asRecord(value) ?? {};
  return {
    ...place,
    visited_at:
      typeof record.visited_at === 'string' ? record.visited_at : null,
  };
};

export const listVisited = async (): Promise<VisitedList> => {
  const body = await request<unknown>('/me/visited');
  const items = asItems(body)
    .map(normalizeVisited)
    .filter((i): i is VisitedPlace => i !== null);
  return { items, count: items.length };
};

export const markVisited = async (placeId: number): Promise<VisitedPlace> => {
  const item = normalizeVisited(
    await request<unknown>(`/me/visited/${placeId}`, { method: 'PUT' })
  );
  if (!item)
    throw new AccountApiError('bad_response', 'агент ответил не местом');
  return item;
};

export const unmarkVisited = (placeId: number): Promise<void> =>
  request<void>(`/me/visited/${placeId}`, { method: 'DELETE' });

export const markVisitedBulk = (
  placeIds: number[]
): Promise<{ marked: number[] }> =>
  request<{ marked: number[] }>('/me/visited', {
    method: 'POST',
    body: JSON.stringify({ place_ids: placeIds }),
  });

export const adminListUsers = async (
  params: { q?: string; limit?: number; offset?: number } = {}
): Promise<AdminUserList> => {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  query.set('limit', String(params.limit ?? 100));
  if (params.offset) query.set('offset', String(params.offset));
  const body = await request<unknown>(`/admin/users?${query.toString()}`);
  const items = asItems(body)
    .map(normalizeAdminUser)
    .filter((u): u is AdminUser => u !== null);
  return { items, total: asTotal(body, items.length) };
};

export const adminPatchUser = async (
  userId: string,
  patch: { role?: UserRole; display_name?: string | null }
): Promise<AccountUser> => {
  const user = normalizeUser(
    await request<unknown>(`/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  );
  if (!user)
    throw new AccountApiError('bad_response', 'агент ответил не пользователем');
  return user;
};

export const adminDeleteUser = (userId: string): Promise<void> =>
  request<void>(`/admin/users/${userId}`, { method: 'DELETE' });

export const adminListPlaces = async (
  params: {
    q?: string;
    category?: string;
    limit?: number;
    offset?: number;
  } = {}
): Promise<AdminPlaceList> => {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  if (params.category) query.set('category', params.category);
  query.set('limit', String(params.limit ?? 50));
  if (params.offset) query.set('offset', String(params.offset));
  const body = await request<unknown>(`/admin/places?${query.toString()}`);
  const items = asItems(body)
    .map(normalizePlace)
    .filter((p): p is Place => p !== null);
  return { items, total: asTotal(body, items.length) };
};

export const adminCreatePlace = async (
  input: AdminPlaceInput
): Promise<Place> => {
  const place = normalizePlace(
    await request<unknown>('/admin/places', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  );
  if (!place)
    throw new AccountApiError('bad_response', 'агент ответил не местом');
  return place;
};

export const adminUpdatePlace = async (
  placeId: number,
  patch: AdminPlaceInput
): Promise<Place> => {
  const place = normalizePlace(
    await request<unknown>(`/admin/places/${placeId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
  );
  if (!place)
    throw new AccountApiError('bad_response', 'агент ответил не местом');
  return place;
};

export const adminDeletePlace = (placeId: number): Promise<void> =>
  request<void>(`/admin/places/${placeId}`, { method: 'DELETE' });

export const adminStats = (): Promise<AdminStats> =>
  request<AdminStats>('/admin/stats');
