import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import {
  LOCAL_ROUTES_STORAGE_KEY,
  useClientRoutes,
  type SaveRouteOutcome,
} from './use-client-routes';
import { CLIENT_PREFERENCES_STORAGE_KEY } from './use-client-preferences';

const CLIENT_ID = '22222222-2222-4222-8222-222222222222';

const PLAN = {
  shape: { coordinates: [[53.68, 23.83]] },
  summary: { length_km: 3 },
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const MakeWrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return MakeWrapper;
};

const SERVER_ROW = {
  id: 'r1',
  name: 'Старый город',
  query: 'что посмотреть',
  created_at: '2026-09-01T00:00:00Z',
  stop_count: 5,
  distance_m: 3200,
  duration_min: 120,
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('grodno-client-id', CLIENT_ID);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('useClientRoutes', () => {
  it('loads the saved list without geometry, even if the server sent some', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse([{ ...SERVER_ROW, plan: PLAN }]))
    );

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.routes).toHaveLength(1));

    expect(result.current.routes[0]).toEqual({
      ...SERVER_ROW,
      local_only: false,
    });
    expect(JSON.stringify(result.current.routes)).not.toContain('coordinates');
    expect(JSON.stringify(result.current.routes)).not.toContain('plan');
  });

  it('saves on the server when the tourist asks, and only then', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') {
          return jsonResponse(
            { id: 'srv-1', created_at: '2026-09-02T00:00:00Z' },
            201
          );
        }
        return jsonResponse([SERVER_ROW]);
      }
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')
    ).toBe(false);

    let outcome!: SaveRouteOutcome;
    await act(async () => {
      outcome = await result.current.saveRoute({
        query: 'что посмотреть',
        plan: PLAN,
        name: 'Мой маршрут',
        visit_overrides: { '1': 50 },
      });
    });

    expect(outcome).toEqual({ ok: true, storage: 'server', id: 'srv-1' });

    const post = fetchMock.mock.calls.find(
      ([, init]) => init?.method === 'POST'
    );
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      query: 'что посмотреть',
      plan: PLAN,
      name: 'Мой маршрут',
      visit_overrides: { '1': 50 },
    });
    expect(new Headers(post?.[1]?.headers).get('X-Client-Id')).toBe(CLIENT_ID);
  });

  it('keeps a local copy and reports failure on 503, never success', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === 'POST'
          ? jsonResponse({ code: 'storage_unavailable' }, 503)
          : jsonResponse([])
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let outcome!: SaveRouteOutcome;
    await act(async () => {
      outcome = await result.current.saveRoute({
        query: 'что посмотреть',
        plan: PLAN,
      });
    });

    expect(outcome.ok).toBe(false);
    expect(outcome).toMatchObject({
      storage: 'local',
      storageUnavailable: true,
    });
    expect(result.current.savingUnavailable).toBe(true);

    const local = result.current.routes.find((route) => route.local_only);
    expect(local).toBeTruthy();
    expect(local?.query).toBe('что посмотреть');
    expect(local?.stop_count).toBeNull();

    const stored = JSON.parse(
      localStorage.getItem(LOCAL_ROUTES_STORAGE_KEY) ?? '[]'
    ) as { plan: unknown }[];
    expect(stored[0]?.plan).toEqual(PLAN);
  });

  it('restores a server route by re-applying exactly what was stored', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'GET' || init?.method == null) {
          const url = String(_input);
          if (url.includes('/clients/me/routes/r1')) {
            return jsonResponse({
              ...SERVER_ROW,
              plan: PLAN,
              visit_overrides: { '1': 50 },
              updated_at: '2026-09-01T00:00:00Z',
            });
          }
          return jsonResponse([SERVER_ROW]);
        }
        return jsonResponse({});
      })
    );

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.routes).toHaveLength(1));

    let restored!: Awaited<ReturnType<typeof result.current.restoreRoute>>;
    await act(async () => {
      restored = await result.current.restoreRoute('r1');
    });

    expect(restored.ok).toBe(true);
    if (restored.ok) {
      expect(restored.route.source).toBe('server');
      expect(restored.route.plan).toEqual(PLAN);
      expect(restored.route.visitOverrides).toEqual({ '1': 50 });
    }
  });

  it('deletes my data and clears the local copies', async () => {
    localStorage.setItem(
      LOCAL_ROUTES_STORAGE_KEY,
      JSON.stringify([
        {
          id: 'local-1',
          name: null,
          query: 'старое',
          created_at: '2026-09-01T00:00:00Z',
          plan: PLAN,
          visit_overrides: null,
        },
      ])
    );
    localStorage.setItem(
      CLIENT_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ transport: 'auto' })
    );

    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'DELETE') {
          return new Response(null, { status: 204 });
        }
        return jsonResponse([]);
      }
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let outcome!: Awaited<ReturnType<typeof result.current.deleteMyData>>;
    await act(async () => {
      outcome = await result.current.deleteMyData();
    });

    expect(outcome.ok).toBe(true);
    const del = fetchMock.mock.calls.find(
      ([, init]) => init?.method === 'DELETE'
    );
    expect(String(del?.[0])).toBe('/clients/me');
    expect(localStorage.getItem(LOCAL_ROUTES_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(CLIENT_PREFERENCES_STORAGE_KEY)).toBeNull();
    expect(result.current.routes).toEqual([]);
  });

  it('does not claim deletion when the server cannot be reached', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'DELETE') {
          return jsonResponse({ code: 'storage_unavailable' }, 503);
        }
        return jsonResponse([]);
      })
    );

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let outcome!: Awaited<ReturnType<typeof result.current.deleteMyData>>;
    await act(async () => {
      outcome = await result.current.deleteMyData();
    });

    expect(outcome.ok).toBe(false);
    expect(outcome).toMatchObject({ serverDeleted: false });
    expect(outcome.message).toContain('могли остаться');
  });

  it('renames and deletes a saved route by id', async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'PATCH') return jsonResponse({ id: 'r1' });
        if (init?.method === 'DELETE') {
          return new Response(null, { status: 204 });
        }
        return jsonResponse([SERVER_ROW]);
      }
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useClientRoutes(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.routes).toHaveLength(1));

    let renamed!: Awaited<ReturnType<typeof result.current.renameRoute>>;
    await act(async () => {
      renamed = await result.current.renameRoute('r1', 'Вечерняя прогулка');
    });
    expect(renamed.ok).toBe(true);

    const patch = fetchMock.mock.calls.find(
      ([, init]) => init?.method === 'PATCH'
    );
    expect(String(patch?.[0])).toBe('/clients/me/routes/r1');
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      name: 'Вечерняя прогулка',
    });

    let deleted!: Awaited<ReturnType<typeof result.current.deleteRoute>>;
    await act(async () => {
      deleted = await result.current.deleteRoute('r1');
    });
    expect(deleted.ok).toBe(true);

    const del = fetchMock.mock.calls.find(
      ([, init]) => init?.method === 'DELETE'
    );
    expect(String(del?.[0])).toBe('/clients/me/routes/r1');
  });
});
