import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockSetWaypoint = vi.hoisted(() => vi.fn());
const mockSetPlaceDetails = vi.hoisted(() => vi.fn());
const mockRefetch = vi.hoisted(() => vi.fn());
const mockGetState = vi.hoisted(() =>
  // Loose return type: individual tests swap in different store shapes.
  vi.fn((): Record<string, unknown> => ({
    waypoints: [],
    refinementLog: [],
    excludedPlaceIds: [],
    snapshotRoute: vi.fn(),
  }))
);
const mockNavigate = vi.hoisted(() => vi.fn());
const mockResetSettings = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/use-directions-queries', () => ({
  useDirectionsQuery: () => ({ refetch: mockRefetch }),
}));

vi.mock('@/stores/common-store', () => ({
  useCommonStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      directionsPanelOpen: true,
      toggleDirections: vi.fn(),
      resetSettings: mockResetSettings,
    }),
}));

vi.mock('@/stores/directions-store', () => ({
  ME_WAYPOINT_ID: 'me',
  useDirectionsStore: Object.assign(
    (selector: (s: Record<string, unknown>) => unknown) =>
      selector({
        waypoints: [],
        placeDetails: {},
        setWaypoint: mockSetWaypoint,
        setPlaceDetails: mockSetPlaceDetails,
        addEmptyWaypointToEnd: vi.fn(),
        routeHistory: [],
        addToHistory: vi.fn(),
        removeFromHistory: vi.fn(),
        clearHistory: vi.fn(),
        // refinement context (phase 0/1): the sidebar renders the log and reads
        // these on submit, so the double has to carry them.
        refinementLog: [],
        routeSnapshots: [],
        excludedPlaceIds: [],
        snapshotRoute: vi.fn(),
        undoRefinement: vi.fn(),
        resetRoute: vi.fn(),
        pushRefinement: vi.fn(),
      }),
    { getState: mockGetState }
  ),
}));

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => ({ profile: 'bicycle' }),
  useNavigate: () => mockNavigate,
}));

vi.mock('./waypoint-list', () => ({
  WaypointList: () => <div data-testid="waypoint-list" />,
}));

vi.mock('@/utils/nominatim', () => ({
  forward_geocode: vi.fn(async () => ({ data: [] })),
}));

import { Sidebar } from './sidebar';

const mockGetCurrentPosition = vi.fn();

/** The agent's answer for a two-stop walk. */
const AGENT_ANSWER = {
  points: [
    {
      id: 11,
      name: 'Старый замок',
      category: 'замок',
      lat: 53.6772,
      lon: 23.8222,
      blurb: 'резиденция',
      fun_facts: ['факт'],
      visit_minutes: 40,
    },
    {
      id: 12,
      name: 'Новый замок',
      category: 'дворец',
      lat: 53.678,
      lon: 23.8246,
      visit_minutes: 30,
    },
  ],
  costing: 'auto',
  summary: { length_km: 1.5, time_seconds: 900 },
  budget: {
    budget_minutes: null,
    walk_minutes: 15,
    visit_minutes: 70,
    total_minutes: 85,
    fits: true,
  },
};

describe('Sidebar', () => {
  beforeEach(() => {
    vi.stubGlobal('navigator', {
      ...navigator,
      geolocation: { getCurrentPosition: mockGetCurrentPosition },
    });
    mockGetCurrentPosition.mockImplementation((ok: (p: unknown) => void) => {
      ok({ coords: { latitude: 53.7, longitude: 23.8 } });
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('pins the tourist’s position as the route start', async () => {
    render(<Sidebar />);

    await waitFor(() => {
      expect(mockSetWaypoint).toHaveBeenCalled();
    });
    const waypoints = mockSetWaypoint.mock.calls.at(-1)?.[0];
    expect(waypoints[0].id).toBe('me');
    expect(waypoints[0].geocodeResults[0].displaylnglat).toEqual([23.8, 53.7]);
  });

  it('sends the chosen transport and my coordinates to the agent', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.click(screen.getByTestId('transport-car'));
    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'замки Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    expect(body.profile).toBe('auto'); // car → Valhalla's "auto"
    expect(body.origin).toEqual({ lat: 53.7, lon: 23.8 });
    expect(body.time_budget_minutes).toBeUndefined(); // "без ограничения"
  });

  it('sends no transport at all when the tourist did not pick one', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'замки Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    // «как удобно» is not a constraint: the agent picks the costing itself
    expect(body.profile).toBeUndefined();
  });

  it('adopts the costing the agent planned with when transport is "как удобно"', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'костёлы области'
    );
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() => expect(mockResetSettings).toHaveBeenCalledWith('car'));
    // the URL profile follows, so the line the webapp draws uses "auto" too
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    const search = mockNavigate.mock.calls.at(-1)![0].search({});
    expect(search.profile).toBe('car');
  });

  it('still builds when the browser refuses geolocation', async () => {
    mockGetCurrentPosition.mockImplementation((_ok, fail) =>
      fail?.({ code: 1, message: 'denied' })
    );
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'замки Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));

    // geolocation is a nice-to-have: a refusal must not block the plan
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    expect(body.origin).toBeUndefined();
    expect(body.query).toBe('замки Гродно');
  });

  it('re-plans the route when the transport changes', async () => {
    // the agent planned on foot, so picking "машина" is a real change
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => ({ ...AGENT_ANSWER, costing: 'pedestrian' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'замки Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // switching the transport must not leave the walking plan (and its travel
    // time) on screen: the same query is re-planned for the new costing
    await user.click(screen.getByTestId('transport-car'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(String(fetchMock.mock.calls[1]![1].body));
    expect(body.profile).toBe('auto');
    expect(body.query).toBe('замки Гродно');
  });

  it('starts the planned route at my position and numbers the stops from 1', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'замки Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() =>
      expect(mockSetWaypoint.mock.calls.length).toBeGreaterThanOrEqual(2)
    );
    const planned = mockSetWaypoint.mock.calls.at(-1)?.[0];
    expect(planned).toHaveLength(3); // my position + 2 stops
    expect(planned[0].id).toBe('me');
    expect(planned[1].userInput).toBe('Старый замок');
    expect(planned[1].placeId).toBe(11);
  });

  it('sends the current route as context on a refinement turn', async () => {
    const sentBodies: string[] = [];
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      sentBodies.push(String(init.body));
      return { ok: true, json: async () => AGENT_ANSWER };
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'музеи Гродно'
    );
    await user.click(screen.getByLabelText('построить маршрут'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // From here the route exists: a second submit is a refinement and must
    // carry the stops, the pinned flags and the hand-deleted ids.
    const snapshot = vi.fn();
    mockGetState.mockReturnValue({
      waypoints: [
        {
          id: 'me',
          userInput: 'моё местоположение',
          geocodeResults: [
            {
              title: 'me',
              sourcelnglat: [23.8, 53.7],
              displaylnglat: [23.8, 53.7],
            },
          ],
        },
        {
          id: '0',
          userInput: 'Старый замок',
          placeId: 11,
          geocodeResults: [
            {
              title: 'Старый замок',
              selected: true,
              sourcelnglat: [23.8222, 53.6772],
              displaylnglat: [23.8222, 53.6772],
            },
          ],
        },
      ],
      refinementLog: [{ id: 'r1' }],
      excludedPlaceIds: [17],
      snapshotRoute: snapshot,
    });

    await user.type(
      screen.getByPlaceholderText('прогулка по замкам Гродно'),
      'добавь кофейню и туалет'
    );
    await user.click(screen.getByLabelText('построить маршрут'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const body = JSON.parse(sentBodies[1]!);
    expect(snapshot).toHaveBeenCalled(); // snapshot before the rebuild
    expect(body.context.instruction).toBe('добавь кофейню и туалет');
    expect(body.context.revision).toBe(2); // log length + 1
    expect(body.context.excluded_ids).toEqual([17]);
    expect(
      body.context.base_points.map((p: { source: string }) => p.source)
    ).toEqual(['mine', 'agent']);
    expect(body.context.base_points[1].pinned).toBe(false);
  });

  it('switches to the guide mode and back', async () => {
    const user = userEvent.setup();
    render(<Sidebar />);

    expect(screen.queryByTestId('guide-panel')).toBeNull();

    await user.click(screen.getByTestId('mode-guide'));
    // No route built yet: the guide explains what it needs instead of pretending
    expect(screen.getByTestId('guide-panel')).toBeInTheDocument();
    expect(
      screen.getByText(/соберите маршрут в режиме планирования/i)
    ).toBeInTheDocument();

    await user.click(screen.getByTestId('mode-plan'));
    expect(screen.queryByTestId('guide-panel')).toBeNull();
    expect(
      screen.getByLabelText('построить маршрут')
    ).toBeInTheDocument();
  });
});
