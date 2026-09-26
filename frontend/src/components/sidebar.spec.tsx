import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockStoreState = vi.hoisted(() => ({
  waypoints: [] as Record<string, unknown>[],
  placeDetails: {} as Record<string, unknown>,
  routeHistory: [] as unknown[],
  refinementLog: [] as unknown[],
  routeSnapshots: [] as unknown[],
  excludedPlaceIds: [] as number[],
  setWaypoint: vi.fn(),
  setPlaceDetails: vi.fn(),
  addEmptyWaypointToEnd: vi.fn(),
  addToHistory: vi.fn(),
  removeFromHistory: vi.fn(),
  clearHistory: vi.fn(),
  snapshotRoute: vi.fn(),
  undoRefinement: vi.fn(),
  resetRoute: vi.fn(),
  pushRefinement: vi.fn(),
}));
const mockSetWaypoint = mockStoreState.setWaypoint;
const mockRefetch = vi.hoisted(() => vi.fn());
const mockGetState = vi.hoisted(() =>
  // Loose return type: individual tests swap in different store shapes.
  vi.fn((): Record<string, unknown> => mockStoreState)
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
      selector(mockStoreState),
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
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
  forward_geocode: vi.fn(async (_url: string, _init: RequestInit) => ({
    data: [],
  })),
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

/**
 * The ask field. Its placeholder is part of the empty state's contract, so the
 * empty-state test asserts it; the queries go through the stable label instead.
 */
const askField = () =>
  screen.getByRole('textbox', { name: 'что хотите посмотреть' });

/** The sticky footer's main action. */
const buildButton = () =>
  screen.getByRole('button', { name: /построить маршрут/i });

/** A fetch that answers with `AGENT_ANSWER` and records every body sent. */
const agentFetch = () => {
  const sentBodies: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    sentBodies.push(JSON.parse(String(init.body)));
    return { ok: true, json: async () => AGENT_ANSWER };
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, sentBodies, body: (i: number) => sentBodies[i] ?? {} };
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
    // A fresh store per test: the panel reads it through the same selectors the
    // real one uses, and a test that swapped the shape must not leak it.
    mockStoreState.waypoints = [];
    mockStoreState.placeDetails = {};
    mockStoreState.refinementLog = [];
    mockStoreState.routeSnapshots = [];
    mockStoreState.excludedPlaceIds = [];
    // Writing the waypoints back into the double is what makes a reset visible.
    mockStoreState.setWaypoint.mockImplementation(
      (next: Record<string, unknown>[]) => {
        mockStoreState.waypoints = next;
      }
    );
    mockGetState.mockReturnValue(mockStoreState);
  });

  afterEach(() => {
    // A worker shares one jsdom document between its files: unmount by hand.
    cleanup();
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
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.click(screen.getByTestId('transport-car'));
    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    expect(body.profile).toBe('auto'); // car → Valhalla's "auto"
    expect(body.origin).toEqual({ lat: 53.7, lon: 23.8 });
    expect(body.time_budget_minutes).toBeUndefined(); // "без ограничения"
  });

  it('sends no transport at all when the tourist did not pick one', async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    // «как удобно» is not a constraint: the agent picks the costing itself
    expect(body.profile).toBeUndefined();
  });

  it('adopts the costing the agent planned with when transport is "как удобно"', async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'костёлы области');
    await user.click(buildButton());

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
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    // geolocation is a nice-to-have: a refusal must not block the plan
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body));
    expect(body.origin).toBeUndefined();
    expect(body.query).toBe('замки Гродно');
  });

  it('re-plans the route when the transport changes', async () => {
    // the agent planned on foot, so picking "машина" is a real change
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => ({ ...AGENT_ANSWER, costing: 'pedestrian' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
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
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: async () => AGENT_ANSWER,
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

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

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'музеи Гродно');
    await user.click(buildButton());
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

    await user.type(askField(), 'добавь кофейню и туалет');
    await user.click(buildButton());
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
    const user = userEvent.setup({ delay: null });
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
    expect(buildButton()).toBeInTheDocument();
  });

  // ── The redesign (DESIGN.md phases 1–2) ────────────────────────────────────

  it('keeps the close button out of the title’s way', () => {
    render(<Sidebar />);

    // It sits in the header's flex row, never absolutely placed over the text.
    expect(
      screen.getByRole('button', { name: 'закрыть панель' }).className
    ).not.toMatch(/absolute/);
    expect(screen.getByText('AI-гид по Гродно')).toBeInTheDocument();
  });

  it('never leaves the panel blank: empty state, hint chips, disabled CTA', () => {
    render(<Sidebar />);

    expect(screen.getByTestId('plan-empty')).toBeInTheDocument();
    expect(
      screen.getByText(/Здесь появятся остановки маршрута/i)
    ).toBeInTheDocument();
    // the ask field says what to type (DESIGN.md)
    expect(
      screen.getByPlaceholderText('Что хотите посмотреть? …')
    ).toBeInTheDocument();
    for (const hint of ['замки', 'костёлы', 'монастыри', 'где поесть']) {
      expect(screen.getByRole('button', { name: hint })).toBeInTheDocument();
    }
    // nothing typed yet → nothing to build
    expect(buildButton()).toBeDisabled();
  });

  it('fills the query from a hint chip and submits on Enter', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.click(screen.getByTestId('hint-замки'));
    // the chip only fills the field — the tourist still decides when to go
    expect(askField()).toHaveValue('замки');
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(askField(), '{Enter}');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(body(0).query).toBe('замки');
  });

  it('sends the time budget the tourist picked', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.click(screen.getByRole('button', { name: '2 ч' }));
    expect(screen.getByRole('button', { name: '2 ч' })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(body(0).time_budget_minutes).toBe(120);
  });

  it('leaves the budget out of the body for «без ограничения»', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    // pick a budget and change your mind: the field is absent, not 0
    await user.click(screen.getByRole('button', { name: '2 ч' }));
    await user.click(screen.getByRole('button', { name: 'без ограничения' }));
    expect(
      screen.getByRole('button', { name: 'без ограничения' })
    ).toHaveAttribute('aria-pressed', 'true');

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect('time_budget_minutes' in body(0)).toBe(false);
  });

  it('shows the route summary numbers the answer came with', async () => {
    const { fetchMock } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const tile = (label: string) => screen.getByText(label).parentElement;
    // summary.length_km, summary.time_seconds
    expect(tile('точек')).toHaveTextContent('2');
    expect(tile('длина')).toHaveTextContent('1.5 км');
    expect(tile('мин в пути')).toHaveTextContent('15');
    // budget.* on the line under the tiles
    expect(screen.getByText(/в пути ~15 мин/)).toBeInTheDocument();
    expect(screen.getByText(/осмотр ~1 ч 10 мин/)).toBeInTheDocument();
    expect(screen.getByText('без лимита')).toBeInTheDocument();
    // the ask field becomes the refinement input — one field, not two
    expect(askField()).toHaveAttribute(
      'placeholder',
      'Что уточнить? «добавь кофейню»'
    );
  });

  it('replaces the stops with skeleton rows while the agent is thinking', async () => {
    let answer: (value: unknown) => void = () => undefined;
    // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the test reads calls[i][1]
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => ({
      ok: true,
      json: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    expect(await screen.findByTestId('stops-skeleton')).toBeInTheDocument();
    expect(screen.getByTestId('summary-skeleton')).toBeInTheDocument();
    // the main action says it is working, and cannot be pressed twice
    expect(
      screen.getByRole('button', { name: /Строю маршрут/i })
    ).toBeDisabled();

    answer(AGENT_ANSWER);
    await waitFor(() =>
      expect(screen.queryByTestId('stops-skeleton')).toBeNull()
    );
  });

  it('lists the stops and the way out of a refinement once a route exists', async () => {
    const { fetchMock } = agentFetch();
    mockStoreState.waypoints = [
      { id: 'me', userInput: 'Моё местоположение', geocodeResults: [{}] },
      { id: '0', userInput: 'Старый замок', placeId: 11, geocodeResults: [{}] },
    ];
    mockStoreState.excludedPlaceIds = [17, 18];

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    expect(screen.getByTestId('waypoint-list')).toBeInTheDocument();
    expect(screen.getByTestId('excluded-chip')).toHaveTextContent(
      'убрано вручную: 2'
    );
    // nothing was refined yet, so there is nothing to roll back
    expect(
      screen.getByRole('button', { name: /отменить уточнение/i })
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: /новый маршрут/i })
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /новый маршрут/i }));
    expect(mockStoreState.resetRoute).toHaveBeenCalled();
    // the route is emptied: no agent stop is left on it any more
    expect(
      mockStoreState.waypoints.filter((wp) => wp.placeId != null)
    ).toHaveLength(0);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('switches the sheet between its two snap points', async () => {
    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    const peek = screen.getByRole('button', { name: 'развернуть панель' });
    expect(peek).toHaveAttribute('aria-expanded', 'false');
    await user.click(peek);
    expect(
      screen.getByRole('button', { name: 'свернуть панель' })
    ).toHaveAttribute('aria-expanded', 'true');
  });
});
