/**
 * The agent's client API, in one place.
 *
 * Every call goes to a same-origin relative path: the webapp's nginx (and the
 * dev Vite proxy) forwards `/clients/*` to the agent, so nothing here hardcodes
 * a host — the same code works behind Codespaces, a tunnel, or a remote agent
 * with `VITE_AGENT_URL` set.
 *
 * Every call carries `X-Client-Id`, minted once and reused (see
 * `@/utils/client-id`). Failures are turned into a `ClientApiError` with the
 * agent's own machine code, so callers can tell
 * «saving is unavailable» (`storage_unavailable`, or a network that never
 * reached the server) apart from «this route does not exist».
 */

import { getClientId } from '@/utils/client-id';

import {
  SAVING_UNAVAILABLE_CODES,
  type ClientApiErrorCode,
  type ClientPreferences,
  type ClientPreferencesPatch,
  type CreatedRoute,
  type CreateRouteInput,
  type SavedRoute,
  type SavedRouteSummary,
} from './types';

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

const CLIENT_ID_HEADER = 'X-Client-Id';

/** A failed agent call, carrying the agent's machine code rather than prose. */
export class ClientApiError extends Error {
  readonly code: ClientApiErrorCode;
  readonly status: number | null;

  constructor(
    code: ClientApiErrorCode,
    message: string,
    status: number | null = null
  ) {
    super(message);
    this.name = 'ClientApiError';
    this.code = code;
    this.status = status;
  }
}

/** The honest reading of an error: can this be saved right now, or not? */
export const isSavingUnavailable = (error: unknown): boolean =>
  error instanceof ClientApiError &&
  SAVING_UNAVAILABLE_CODES.includes(error.code);

const isKnownCode = (value: unknown): value is ClientApiErrorCode =>
  typeof value === 'string' &&
  (
    [
      'storage_unavailable',
      'route_not_found',
      'invalid_client_id',
      'too_many_routes',
    ] as readonly string[]
  ).includes(value);

/** The agent's reason code from a JSON body, wherever it put it. */
const reasonFromBody = (body: unknown): ClientApiErrorCode | null => {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  for (const key of ['code', 'error_code', 'reason']) {
    const value = record[key];
    if (isKnownCode(value)) return value;
  }
  return null;
};

const codeFromStatus = (status: number): ClientApiErrorCode => {
  if (status === 503) return 'storage_unavailable';
  if (status === 404) return 'route_not_found';
  if (status === 409) return 'too_many_routes';
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
    response = await fetch(`${AGENT_URL}${path}`, { ...init, headers });
  } catch {
    // The request never reached the agent. That is not a success and not a
    // "route not found": it means saving cannot work right now.
    throw new ClientApiError(
      'network_unavailable',
      'агент недоступен — сохранение не работает'
    );
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const code = reasonFromBody(body) ?? codeFromStatus(response.status);
    throw new ClientApiError(
      code,
      `агент ответил ошибкой ${response.status}`,
      response.status
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ClientApiError(
      'bad_response',
      'агент ответил не JSON',
      response.status
    );
  }
}

// ── Preferences ─────────────────────────────────────────────────────────────
//
// The response is normalised field by field instead of trusted as-is: a missing
// field becomes `null` («не указано»), and junk is dropped rather than turned
// into a number the tourist never chose.

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const asFiniteNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const asStringArray = (value: unknown): string[] | null => {
  if (!Array.isArray(value)) return null;
  const strings = value.filter(
    (item): item is string => typeof item === 'string'
  );
  return strings.length > 0 ? strings : [];
};

const asVisitMinutes = (value: unknown): Record<string, number> | null => {
  const record = asRecord(value);
  if (!record) return null;
  const entries = Object.entries(record).filter(
    (entry): entry is [string, number] =>
      typeof entry[1] === 'number' && Number.isFinite(entry[1])
  );
  return Object.fromEntries(entries);
};

const asTransport = (value: unknown): ClientPreferences['transport'] =>
  value === 'pedestrian' || value === 'bicycle' || value === 'auto'
    ? value
    : null;

const asLanguage = (value: unknown): ClientPreferences['language'] =>
  value === 'ru' || value === 'en' ? value : null;

/** Preferences may arrive bare or wrapped; both are read the same way. */
export const normalizePreferences = (body: unknown): ClientPreferences => {
  const outer = asRecord(body) ?? {};
  const source = asRecord(outer.preferences) ?? outer;

  return {
    transport: asTransport(source.transport),
    time_budget_minutes: asFiniteNumber(source.time_budget_minutes),
    party_adults: asFiniteNumber(source.party_adults),
    party_children: asFiniteNumber(source.party_children),
    interests: asStringArray(source.interests),
    language: asLanguage(source.language),
    visit_minutes_by_category: asVisitMinutes(source.visit_minutes_by_category),
    updated_at:
      typeof source.updated_at === 'string' ? source.updated_at : null,
  };
};

export const getClientPreferences = async (): Promise<ClientPreferences> =>
  normalizePreferences(await request<unknown>('/clients/me/preferences'));

export const putClientPreferences = async (
  patch: ClientPreferencesPatch
): Promise<ClientPreferences> =>
  normalizePreferences(
    await request<unknown>('/clients/me/preferences', {
      method: 'PUT',
      body: JSON.stringify(patch),
    })
  );

// ── Saved routes ────────────────────────────────────────────────────────────

const normalizeSummary = (value: unknown): SavedRouteSummary | null => {
  const record = asRecord(value);
  if (!record || typeof record.id !== 'string') return null;

  return {
    id: record.id,
    name: typeof record.name === 'string' ? record.name : null,
    query: typeof record.query === 'string' ? record.query : '',
    created_at:
      typeof record.created_at === 'string' ? record.created_at : null,
    stop_count: asFiniteNumber(record.stop_count),
    distance_m: asFiniteNumber(record.distance_m),
    duration_min: asFiniteNumber(record.duration_min),
  };
};

/**
 * The list endpoint may answer with a bare array or wrap it; either way only
 * the summary fields survive — anything heavy (a `plan`, a geometry) is dropped
 * here, so no caller can accidentally render what the list never promised.
 */
export const normalizeRouteList = (body: unknown): SavedRouteSummary[] => {
  const rows: unknown = Array.isArray(body)
    ? body
    : (asRecord(body)?.routes ?? asRecord(body)?.items ?? []);
  if (!Array.isArray(rows)) return [];
  return rows
    .map(normalizeSummary)
    .filter((row): row is SavedRouteSummary => row !== null);
};

export const listClientRoutes = async (
  limit = 50
): Promise<SavedRouteSummary[]> =>
  normalizeRouteList(
    await request<unknown>(`/clients/me/routes?limit=${limit}`)
  );

export const createClientRoute = (
  input: CreateRouteInput
): Promise<CreatedRoute> =>
  request<CreatedRoute>('/clients/me/routes', {
    method: 'POST',
    body: JSON.stringify(input),
  });

export const getClientRoute = async (id: string): Promise<SavedRoute> => {
  const body = asRecord(await request<unknown>(`/clients/me/routes/${id}`));
  if (!body || typeof body.id !== 'string') {
    throw new ClientApiError('bad_response', 'сохранённый маршрут не прочитан');
  }
  const overrides = asVisitMinutes(body.visit_overrides);
  return {
    id: body.id,
    name: typeof body.name === 'string' ? body.name : null,
    query: typeof body.query === 'string' ? body.query : '',
    // The stored plan goes through untouched — restore re-applies exactly what
    // was saved, it does not rebuild it.
    plan: body.plan,
    visit_overrides: overrides,
    created_at: typeof body.created_at === 'string' ? body.created_at : null,
    updated_at: typeof body.updated_at === 'string' ? body.updated_at : null,
  };
};

export const renameClientRoute = (
  id: string,
  name: string | null
): Promise<unknown> =>
  request<unknown>(`/clients/me/routes/${id}`, {
    method: 'PATCH',
    // The contract's rename field is a string: an empty name clears it, `null`
    // is not a name the server accepts (so clearing is sent as '').
    body: JSON.stringify({ name: name ?? '' }),
  });

export const deleteClientRoute = (id: string): Promise<void> =>
  request<void>(`/clients/me/routes/${id}`, { method: 'DELETE' });

export const deleteClient = (): Promise<void> =>
  request<void>('/clients/me', { method: 'DELETE' });
