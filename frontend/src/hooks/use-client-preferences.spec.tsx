import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import {
  CLIENT_PREFERENCES_STORAGE_KEY,
  useClientPreferences,
} from './use-client-preferences';

const CLIENT_ID = '22222222-2222-4222-8222-222222222222';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const headerOf = (init: RequestInit | undefined, name: string) =>
  new Headers(init?.headers).get(name);

/** A fresh QueryClient per render so cases do not share a cache. */
const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const MakeWrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return MakeWrapper;
};

const SERVER_PREFERENCES = {
  transport: 'bicycle',
  time_budget_minutes: 120,
  party_adults: 2,
  party_children: null,
  interests: ['temple'],
  language: 'ru',
  visit_minutes_by_category: { temple: 40 },
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('grodno-client-id', CLIENT_ID);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('useClientPreferences', () => {
  it('loads the saved preferences at startup and prefills from them', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(SERVER_PREFERENCES))
    );

    const { result } = renderHook(
      () => useClientPreferences({ debounceMs: 5 }),
      { wrapper: makeWrapper() }
    );

    await waitFor(() =>
      expect(result.current.preferences.time_budget_minutes).toBe(120)
    );
    expect(result.current.preferences.transport).toBe('bicycle');
    expect(result.current.preferences.party_adults).toBe(2);
    expect(result.current.preferences.party_children).toBeNull();
    expect(result.current.preferences.visit_minutes_by_category).toEqual({
      temple: 40,
    });
    expect(result.current.savingUnavailable).toBe(false);
  });

  it('saves a change on its own, without a save button', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'PUT') {
          return jsonResponse({ ...SERVER_PREFERENCES, party_adults: 3 });
        }
        return jsonResponse(SERVER_PREFERENCES);
      }
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(
      () => useClientPreferences({ debounceMs: 5 }),
      { wrapper: makeWrapper() }
    );
    await waitFor(() =>
      expect(result.current.preferences.transport).toBe('bicycle')
    );

    await act(async () => {
      result.current.update({ party_adults: 3 });
      await new Promise((resolve) => setTimeout(resolve, 40));
    });

    // The UI moved at once, and the local copy already has it.
    expect(result.current.preferences.party_adults).toBe(3);
    expect(localStorage.getItem(CLIENT_PREFERENCES_STORAGE_KEY)).toContain(
      '"party_adults":3'
    );

    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(put).toBeTruthy();
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ party_adults: 3 });
    expect(headerOf(put?.[1], 'X-Client-Id')).toBe(CLIENT_ID);
  });

  it('reports 503 storage_unavailable honestly and keeps the local value', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
        init?.method === 'PUT'
          ? jsonResponse({ code: 'storage_unavailable' }, 503)
          : jsonResponse(SERVER_PREFERENCES)
      )
    );

    const { result } = renderHook(
      () => useClientPreferences({ debounceMs: 5 }),
      { wrapper: makeWrapper() }
    );
    await waitFor(() =>
      expect(result.current.preferences.transport).toBe('bicycle')
    );

    await act(async () => {
      result.current.update({ time_budget_minutes: 90 });
      await new Promise((resolve) => setTimeout(resolve, 40));
    });

    expect(result.current.savingUnavailable).toBe(true);
    // The change is not lost: it lives in this browser.
    expect(result.current.preferences.time_budget_minutes).toBe(90);
    expect(localStorage.getItem(CLIENT_PREFERENCES_STORAGE_KEY)).toContain(
      '"time_budget_minutes":90'
    );
  });

  it('works on the local copy when the server cannot be reached', async () => {
    localStorage.setItem(
      CLIENT_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ transport: 'auto', party_adults: 4 })
    );
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      })
    );

    const { result } = renderHook(
      () => useClientPreferences({ debounceMs: 5 }),
      { wrapper: makeWrapper() }
    );

    await waitFor(() => expect(result.current.savingUnavailable).toBe(true));
    expect(result.current.preferences.transport).toBe('auto');
    expect(result.current.preferences.party_adults).toBe(4);
  });
});
