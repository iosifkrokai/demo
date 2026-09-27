import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  ClientApiError,
  deleteClient,
  getClientPreferences,
  isSavingUnavailable,
  listClientRoutes,
  putClientPreferences,
  renameClientRoute,
} from './client';

const CLIENT_ID = '22222222-2222-4222-8222-222222222222';

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];

const mockFetch = (
  impl: (...args: FetchArgs) => Promise<Response>
): ReturnType<
  typeof vi.fn<
    (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  >
> => vi.fn(impl);

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const headerOf = (init: RequestInit | undefined, name: string) =>
  new Headers(init?.headers).get(name);

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('grodno-client-id', CLIENT_ID);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('client API', () => {
  it('sends the X-Client-Id header on a preferences read and keeps nulls honest', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse({
        transport: 'bicycle',
        time_budget_minutes: 90,
        party_adults: null,
        interests: ['temple'],
        language: 'ru',
        visit_minutes_by_category: { temple: 40 },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const preferences = await getClientPreferences();

    expect(preferences).toEqual({
      transport: 'bicycle',
      time_budget_minutes: 90,
      party_adults: null,
      party_children: null,
      interests: ['temple'],
      language: 'ru',
      visit_minutes_by_category: { temple: 40 },
      updated_at: null,
    });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/clients/me/preferences');
    expect(headerOf(init, 'X-Client-Id')).toBe(CLIENT_ID);
  });

  it('PUTs only the changed field', async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ party_adults: 3 }));
    vi.stubGlobal('fetch', fetchMock);

    await putClientPreferences({ party_adults: 3 });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({ party_adults: 3 });
    expect(headerOf(init, 'X-Client-Id')).toBe(CLIENT_ID);
  });

  it('turns 503 storage_unavailable into an honest, non-success error', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ code: 'storage_unavailable' }, 503))
    );

    const error = await getClientPreferences().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ClientApiError);
    expect((error as ClientApiError).code).toBe('storage_unavailable');
    expect((error as ClientApiError).status).toBe(503);
    expect(isSavingUnavailable(error)).toBe(true);
  });

  it('treats a request that never reached the agent as saving-unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => {
        throw new TypeError('Failed to fetch');
      })
    );

    const error = await getClientPreferences().catch((e: unknown) => e);

    expect((error as ClientApiError).code).toBe('network_unavailable');
    expect(isSavingUnavailable(error)).toBe(true);
  });

  it('normalises the route list and drops anything heavy it was not given', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () =>
        jsonResponse([
          {
            id: 'r1',
            name: 'Старый город',
            query: 'что посмотреть',
            created_at: '2026-09-01T00:00:00Z',
            stop_count: 5,
            distance_m: 3200,
            duration_min: 120,
            // The list endpoint never promises this — it must not survive.
            plan: { shape: { coordinates: [[53.68, 23.83]] } },
          },
          { not: 'a route' },
        ])
      )
    );

    const routes = await listClientRoutes();

    expect(routes).toEqual([
      {
        id: 'r1',
        name: 'Старый город',
        query: 'что посмотреть',
        created_at: '2026-09-01T00:00:00Z',
        stop_count: 5,
        distance_m: 3200,
        duration_min: 120,
      },
    ]);
    expect(JSON.stringify(routes)).not.toContain('coordinates');
    expect(JSON.stringify(routes)).not.toContain('plan');
  });

  it('reads a wrapped route list as well as a bare array', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () =>
        jsonResponse({
          routes: [
            {
              id: 'r2',
              name: null,
              query: '',
              created_at: null,
              stop_count: null,
              distance_m: null,
              duration_min: null,
            },
          ],
        })
      )
    );

    await expect(listClientRoutes()).resolves.toEqual([
      {
        id: 'r2',
        name: null,
        query: '',
        created_at: null,
        stop_count: null,
        distance_m: null,
        duration_min: null,
      },
    ]);
  });

  it('deletes the client with DELETE /clients/me', async () => {
    const fetchMock = mockFetch(
      async () => new Response(null, { status: 204 })
    );
    vi.stubGlobal('fetch', fetchMock);

    await deleteClient();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/clients/me');
    expect(init?.method).toBe('DELETE');
    expect(headerOf(init, 'X-Client-Id')).toBe(CLIENT_ID);
  });

  it('renames a route with a string name (an empty name clears it)', async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ id: 'r1' }));
    vi.stubGlobal('fetch', fetchMock);

    await renameClientRoute('r1', null);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/clients/me/routes/r1');
    expect(init?.method).toBe('PATCH');
    // The contract takes a string: `null` is not a name it accepts.
    expect(JSON.parse(String(init?.body))).toEqual({ name: '' });
    expect(headerOf(init, 'X-Client-Id')).toBe(CLIENT_ID);
  });
});
