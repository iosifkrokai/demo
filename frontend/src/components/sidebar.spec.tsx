import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockSetWaypoint = vi.hoisted(() => vi.fn());
const mockSetPlaceDetails = vi.hoisted(() => vi.fn());
const mockRefetch = vi.hoisted(() => vi.fn());
const mockGetState = vi.hoisted(() => vi.fn(() => ({ waypoints: [] })));
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
        setWaypoint: mockSetWaypoint,
        setPlaceDetails: mockSetPlaceDetails,
        addEmptyWaypointToEnd: vi.fn(),
        routeHistory: [],
        addToHistory: vi.fn(),
        removeFromHistory: vi.fn(),
        clearHistory: vi.fn(),
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
    await user.type(screen.getByPlaceholderText('прогулка по замкам Гродно'), 'замки Гродно');
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

    await user.type(screen.getByPlaceholderText('прогулка по замкам Гродно'), 'замки Гродно');
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

    await user.type(screen.getByPlaceholderText('прогулка по замкам Гродно'), 'костёлы области');
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() => expect(mockResetSettings).toHaveBeenCalledWith('car'));
    // the URL profile follows, so the line the webapp draws uses "auto" too
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    const search = mockNavigate.mock.calls.at(-1)![0].search({});
    expect(search.profile).toBe('car');
  });

  it('starts the planned route at my position and numbers the stops from 1', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Sidebar />);

    await user.type(screen.getByPlaceholderText('прогулка по замкам Гродно'), 'замки Гродно');
    await user.click(screen.getByLabelText('построить маршрут'));

    await waitFor(() => expect(mockSetWaypoint.mock.calls.length).toBeGreaterThanOrEqual(2));
    const planned = mockSetWaypoint.mock.calls.at(-1)?.[0];
    expect(planned).toHaveLength(3); // my position + 2 stops
    expect(planned[0].id).toBe('me');
    expect(planned[1].userInput).toBe('Старый замок');
    expect(planned[1].placeId).toBe(11);
  });
});
