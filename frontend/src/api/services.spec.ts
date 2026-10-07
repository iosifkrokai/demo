import { describe, it, expect, vi, afterEach } from 'vitest';

import { fetchServicesAlong } from './services';
import type { RouteLine } from './types';

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

const LINE: RouteLine = {
  type: 'LineString',
  coordinates: [
    [23.81, 53.69],
    [23.82, 53.68],
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('services along route', () => {
  it('carries the current run id so «что по пути» joins the same session', async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse({
        items: [],
        measured: 'distance_to_line',
        not_measured: 'detour_walking_time',
        detour_confirmed: false,
        profile: 'pedestrian',
        categories: [],
        max_off_line_m: 150,
        line_m: 1000,
        result_cap: 20,
        capped: false,
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await fetchServicesAlong(LINE);

    const init = fetchMock.mock.calls[0]?.[1];
    const body = JSON.parse(init?.body as string) as { session_id?: unknown };
    expect(typeof body.session_id).toBe('string');
    expect(body.session_id).toBeTruthy();
  });
});
