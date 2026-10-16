import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';

import {
  RECALCULATE_DEBOUNCE_MS,
  useRouteRecalculate,
} from './use-route-recalculate';

const mockRefetch = vi.hoisted(() => vi.fn());
const mockSetAgentRoute = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/use-directions-queries', () => ({
  useDirectionsQuery: () => ({ refetch: mockRefetch }),
  setAgentRoute: mockSetAgentRoute,
}));

const stop = (id: string, placeId: number, name: string): Waypoint => ({
  id,
  userInput: name,
  geocodeResults: [
    {
      title: name,
      displaylnglat: [23.83, 53.67],
      sourcelnglat: [23.83, 53.67],
      key: Number(id),
      addressindex: 0,
      selected: true,
    },
  ],
  placeId,
});

const me = (): Waypoint => ({
  id: ME_WAYPOINT_ID,
  userInput: 'моё местоположение',
  geocodeResults: [
    {
      title: 'моё местоположение',
      displaylnglat: [23.8, 53.7],
      sourcelnglat: [23.8, 53.7],
      key: 0,
      addressindex: 0,
      selected: true,
    },
  ],
});

/** The answer a re-measure gives: two stops, a line, and numbers that add up. */
const ANSWER = {
  points: [
    { id: 20, key: '2', name: 'Туалет', lat: 53.68, lon: 23.83 },
    { id: 10, key: '1', name: 'Кафе Немо', lat: 53.67, lon: 23.83 },
  ],
  shape: {
    type: 'LineString',
    coordinates: [
      [23.83, 53.67],
      [23.84, 53.68],
    ],
  },
  summary: { length_km: 1.5, time_seconds: 900 },
  costing: 'pedestrian',
  budget: {
    budget_minutes: null,
    walk_minutes: 15,
    visit_minutes: 70,
    total_minutes: 85,
    fits: true,
  },
};

const answerWith = (walkMinutes: number) => ({
  ...ANSWER,
  budget: { ...ANSWER.budget, walk_minutes: walkMinutes },
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];
const mockFetch = (impl: (...args: FetchArgs) => Promise<Response>) =>
  vi.fn(impl);

const seedRoute = (
  extra: Partial<ReturnType<typeof useDirectionsStore.getState>> = {}
) =>
  useDirectionsStore.setState({
    waypoints: [me(), stop('1', 10, 'Кафе Немо'), stop('2', 20, 'Туалет')],
    manualOrder: false,
    roundTrip: false,
    timeBudget: 0,
    costing: 'pedestrian',
    routeSummary: null,
    recalculating: false,
    ...extra,
  });

const measure = async (delayMs = RECALCULATE_DEBOUNCE_MS) => {
  const { result } = renderHook(() => useRouteRecalculate());
  act(() => result.current.recalculate(delayMs));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(delayMs);
  });
  return result;
};

beforeEach(() => {
  vi.useFakeTimers();
  mockRefetch.mockClear();
  mockSetAgentRoute.mockClear();
  seedRoute();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('measuring a route again when the user changes it', () => {
  it('sends the stops, and nothing to interpret', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(ANSWER));
    vi.stubGlobal('fetch', fetchMock);

    await measure();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('/routes/reroute');
    const body = JSON.parse(String(init?.body));
    expect(body.stops).toEqual([
      { key: '1', place_id: 10 },
      { key: '2', place_id: 20 },
    ]);
    expect(body.origin).toEqual({ lat: 53.7, lon: 23.8 });
    expect(body.query).toBeUndefined();
  });

  it('collapses two edits a moment apart into one measurement', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(ANSWER));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useRouteRecalculate());

    act(() => result.current.recalculate());
    act(() => result.current.recalculate());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECALCULATE_DEBOUNCE_MS);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('measures nothing while there is not a walk to measure', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(ANSWER));
    vi.stubGlobal('fetch', fetchMock);
    useDirectionsStore.setState({
      waypoints: [me(), stop('1', 10, 'Кафе Немо')],
    });

    await measure();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('puts the stops in the order the server answered with', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse(ANSWER))
    );

    await measure();

    expect(useDirectionsStore.getState().waypoints.map((wp) => wp.id)).toEqual([
      ME_WAYPOINT_ID,
      '2',
      '1',
    ]);
  });

  it('writes the numbers the answer came with', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse(ANSWER))
    );

    await measure();

    const summary = useDirectionsStore.getState().routeSummary;
    expect(summary?.stops).toBe(2);
    expect(summary?.walkMinutes).toBe(15);
    expect(summary?.visitMinutes).toBe(70);
    expect(summary?.fits).toBe(true);
  });

  it('hands the line to the map in the map’s own spelling of a position', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse(ANSWER))
    );

    await measure();

    expect(mockSetAgentRoute).toHaveBeenCalledWith(
      expect.objectContaining({
        shape: {
          type: 'LineString',
          coordinates: [
            [53.67, 23.83],
            [53.68, 23.84],
          ],
        },
      })
    );
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('asks for the order to be kept only when the user set one', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(ANSWER));
    vi.stubGlobal('fetch', fetchMock);
    seedRoute({ manualOrder: true });

    await measure();

    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]?.body));
    expect(body.preserve_order).toBe(true);
  });

  it('leaves the order to the server when the user has not touched it', async () => {
    const fetchMock = mockFetch(async () => jsonResponse(ANSWER));
    vi.stubGlobal('fetch', fetchMock);

    await measure();

    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]?.body));
    expect(body.preserve_order).toBeUndefined();
  });

  it('ignores an answer that a newer edit has overtaken', async () => {
    let releaseFirst = () => {};
    const first = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    let call = 0;
    const fetchMock = mockFetch(async () => {
      call += 1;
      if (call === 1) {
        await first;
        return jsonResponse(answerWith(999));
      }
      return jsonResponse(answerWith(11));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useRouteRecalculate());

    act(() => result.current.recalculate(0));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => result.current.recalculate(0));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    releaseFirst();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(useDirectionsStore.getState().routeSummary?.walkMinutes).toBe(11);
  });

  it('says so, and keeps the route, when the measurement fails', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(async () => jsonResponse({ detail: 'nope' }, 500))
    );

    await measure();

    expect(useDirectionsStore.getState().routeSummary).toBeNull();
    expect(mockSetAgentRoute).not.toHaveBeenCalled();
  });
});
