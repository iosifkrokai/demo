import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { setAgentRoute, useDirectionsQuery } from './use-directions-queries';
import { useCommonStore } from '@/stores/common-store';
import { useDirectionsStore } from '@/stores/directions-store';
import type { ParsedDirectionsGeometry } from '@/components/types';

const mockToast = vi.hoisted(() => ({
  warning: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('@/routes', () => ({
  router: { state: { location: { search: { profile: 'pedestrian' } } } },
}));

// The polylines the mocked Valhalla answers with are not worth encoding here:
// the geometry is carried on the response so the merge of chunked legs (and the
// summary that goes with it) can be asserted directly.
vi.mock('@/utils/valhalla', async () => {
  const actual =
    await vi.importActual<typeof import('@/utils/valhalla')>('@/utils/valhalla');
  return {
    ...actual,
    parseDirectionsGeometry: (data: {
      decodedGeometry?: number[][];
    }): number[][] => data.decodedGeometry ?? [],
  };
});

const waypoint = (i: number, lng: number, lat: number) => ({
  id: String(i),
  userInput: `stop ${i}`,
  geocodeResults: [
    {
      title: `stop ${i}`,
      selected: true,
      displaylnglat: [lng, lat] as [number, number],
      sourcelnglat: [lng, lat] as [number, number],
      key: 0,
      addressindex: 0,
    },
  ],
});

/** A /route answer whose decoded geometry is fixed, for the chunk-merge cases. */
const valhallaResponse = (
  coordinates: number[][],
  length: number,
  time: number
) => ({
  id: 'valhalla_directions',
  decodedGeometry: coordinates,
  trip: {
    locations: [],
    legs: [{ shape: 'ignored', maneuvers: [], summary: { length, time } }],
    summary: {
      has_time_restrictions: false,
      has_toll: false,
      has_highway: false,
      has_ferry: false,
      min_lat: 0,
      min_lon: 0,
      max_lat: 0,
      max_lon: 0,
      length,
      time,
      cost: 0,
    },
    status_message: 'ok',
    status: 0,
    units: 'km',
    language: 'ru',
    warnings: [],
  },
});

const setWaypoints = (count: number) => {
  useDirectionsStore.setState({
    waypoints: Array.from({ length: count }, (_, i) =>
      waypoint(i, 23.8 + i * 0.01, 53.9 + i * 0.01)
    ),
  });
};

const zoomTo = vi.fn();

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider
    client={
      new QueryClient({ defaultOptions: { queries: { retry: false } } })
    }
  >
    {children}
  </QueryClientProvider>
);

const refetch = async () => {
  const { result } = renderHook(() => useDirectionsQuery(), { wrapper });
  let data: unknown;
  await act(async () => {
    ({ data } = await result.current.refetch());
  });
  return data as
    | (ParsedDirectionsGeometry & { source?: string; hasVerifiedLine?: boolean })
    | null
    | undefined;
};

let fetchMock: ReturnType<typeof vi.fn>;

describe('useDirectionsQuery — one route, one source (spec 002 §7)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAgentRoute(null);
    setWaypoints(2);
    useDirectionsStore.setState({
      results: { data: null, show: { '0': true } },
      successful: false,
    });
    useCommonStore.setState({ showLoading: () => {}, zoomTo });
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('draws the verified agent line and never calls Valhalla itself', async () => {
    const shape = {
      type: 'LineString',
      coordinates: [
        [53.9, 23.8],
        [53.91, 23.81],
        [53.92, 23.82],
      ],
    };
    setAgentRoute({
      shape,
      summary: { length_km: 7.5, time_seconds: 1800 },
      costing: 'pedestrian',
    });

    const data = await refetch();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(data?.source).toBe('agent');
    expect(data?.hasVerifiedLine).toBe(true);
    expect(data?.decodedGeometry).toEqual(shape.coordinates);
    // The summary belongs to the line on screen.
    expect(data?.trip.summary.length).toBe(7.5);
    expect(data?.trip.summary.time).toBe(1800);
    expect(useDirectionsStore.getState().results.data?.source).toBe('agent');
    expect(zoomTo).toHaveBeenCalledWith(shape.coordinates);
  });

  it('does not substitute a client line when the agent geometry is unusable', async () => {
    setAgentRoute({
      shape: { type: 'LineString', coordinates: [] },
      summary: { length_km: null, time_seconds: null },
    });

    const data = await refetch();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(data?.source).toBe('agent');
    expect(data?.hasVerifiedLine).toBe(false);
    expect(data?.decodedGeometry).toEqual([]);
    // Nothing to fit the map bounds to, and the user is told why.
    expect(zoomTo).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalledWith(
      'Нет проверенной линии',
      expect.objectContaining({
        description: expect.stringContaining('без геометрии'),
      })
    );
  });

  it('falls back to the app\'s own Valhalla call for a hand-built route', async () => {
    const coordinates = [
      [53.9, 23.8],
      [53.91, 23.81],
    ];
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => valhallaResponse(coordinates, 2, 900),
    });

    const data = await refetch();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(data?.source).toBe('client');
    expect(data?.hasVerifiedLine).toBe(true);
    expect(data?.decodedGeometry).toEqual(coordinates);
    expect(zoomTo).toHaveBeenCalledWith(coordinates);
  });

  it('still chunks a client route of more than 20 stops and sums its legs', async () => {
    setWaypoints(22);
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => valhallaResponse([[53.9, 23.8], [53.91, 23.81]], 5, 600),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () =>
          valhallaResponse([[53.9 + 0.19, 23.8], [53.9 + 0.21, 23.8]], 7, 900),
      });

    const data = await refetch();

    // Valhalla caps a request at 20 locations: two chained chunks, one line.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(data?.source).toBe('client');
    expect(data?.decodedGeometry).toHaveLength(4);
    expect(data?.trip.summary.length).toBe(12);
    expect(data?.trip.summary.time).toBe(1500);
  });

  it('ignores a handed-over line once the stops on screen are not its own', async () => {
    setAgentRoute({
      shape: {
        type: 'LineString',
        coordinates: [
          [53.9, 23.8],
          [53.91, 23.81],
        ],
      },
      summary: { length_km: 7.5, time_seconds: 1800 },
    });
    // The tourist drags a stop / adds one by hand: this is their route now.
    setWaypoints(2);
    useDirectionsStore.setState({
      waypoints: [waypoint(0, 24.5, 54.5), waypoint(1, 24.6, 54.6)],
    });
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () =>
        valhallaResponse(
          [
            [54.5, 24.5],
            [54.6, 24.6],
          ],
          3,
          800
        ),
    });

    const data = await refetch();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(data?.source).toBe('client');
    expect(data?.decodedGeometry).toEqual([
      [54.5, 24.5],
      [54.6, 24.6],
    ]);
  });

  it('clears the handed-over line when the sidebar resets', async () => {
    setAgentRoute({
      shape: {
        type: 'LineString',
        coordinates: [
          [53.9, 23.8],
          [53.91, 23.81],
        ],
      },
    });
    setAgentRoute(null);
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () =>
        valhallaResponse(
          [
            [53.9, 23.8],
            [53.91, 23.81],
          ],
          3,
          800
        ),
    });

    const data = await refetch();

    expect(data?.source).toBe('client');
  });
});
