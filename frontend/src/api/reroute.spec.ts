import { afterEach, describe, expect, it, vi } from 'vitest';

import { recalculateRoute } from './reroute';

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];
const mockFetch = (impl: (...args: FetchArgs) => Promise<Response>) =>
  vi.fn(impl);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('re-measuring a route the client holds', () => {
  it('sends the stops, in the order they are on screen, to be measured', async () => {
    const fetchMock = mockFetch(async () => jsonResponse({ points: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await recalculateRoute({
      stops: [
        { key: 'me', lat: 53.7, lon: 23.8 },
        { key: '0', place_id: 11 },
        { key: '1', place_id: 12 },
      ],
      profile: 'auto',
      round_trip: true,
      time_budget_minutes: 120,
      preserve_order: true,
    });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/routes/reroute');
    const body = JSON.parse(String(init?.body));
    expect(body.stops.map((stop: { key: string }) => stop.key)).toEqual([
      'me',
      '0',
      '1',
    ]);
    expect(body.preserve_order).toBe(true);
    expect(body.profile).toBe('auto');
  });

  it('lets a cancellation stay a cancellation', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => {
        throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      })
    );

    await expect(
      recalculateRoute({ stops: [{ key: '0', place_id: 1 }] })
    ).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('fails with the status when the server refuses', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ detail: 'nope' }, 422))
    );

    await expect(
      recalculateRoute({ stops: [{ key: '0', place_id: 1 }] })
    ).rejects.toMatchObject({ name: 'ApiError', status: 422 });
  });
});
