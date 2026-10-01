import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mockStoreState = vi.hoisted(() => ({
  waypoints: [] as Record<string, unknown>[],
  placeDetails: {} as Record<string, unknown>,
  // The guide (W6) reads its line from here; the real store defaults to this.
  results: { data: null, show: {} } as {
    data: unknown;
    show: Record<string, boolean>;
  },
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
  markWalked: vi.fn(),
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

// The panel fetches the authored itineraries through react-query. This spec is
// about the panel, so the hook is mocked like the directions one — which also
// lets a test assert that opening a ready-made route makes no request at all.
const mockItineraries = vi.hoisted(() => ({
  items: [] as unknown[],
  missing: [] as string[],
  isLoading: false,
  error: null as unknown,
}));
const mockReloadItineraries = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/use-itineraries', () => ({
  useItineraries: () => ({
    itineraries: mockItineraries.items,
    missing: mockItineraries.missing,
    isLoading: mockItineraries.isLoading,
    error: mockItineraries.error,
    reload: mockReloadItineraries,
  }),
}));

vi.mock('@/stores/common-store', () => ({
  useCommonStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      directionsPanelOpen: true,
      toggleDirections: vi.fn(),
      resetSettings: mockResetSettings,
      focus: null,
      guideFix: null,
      guiding: false,
      guideTurnDistanceM: null,
      focusOn: vi.fn(),
      setGuideFix: vi.fn(),
      setGuiding: vi.fn(),
      setGuideTurnDistanceM: vi.fn(),
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
  screen.getByRole('button', { name: /подобрать маршрут/i });

/** A fetch that answers with `AGENT_ANSWER` and records every body sent. */
const agentFetch = () => {
  const sentBodies: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    sentBodies.push(JSON.parse(String(init.body)));
    return { ok: true, json: async () => AGENT_ANSWER };
  });
  stubAgentFetch(fetchMock);
  return { fetchMock, sentBodies, body: (i: number) => sentBodies[i] ?? {} };
};

/**
 * The panel also asks `GET /routes/progress/{id}` while a request is in flight.
 * These specs are about the plan request, so the poll is answered right here —
 * «the server does not know this id», which is exactly what a spec without a
 * pipeline should say — and never reaches the mock under test. Call counts and
 * recorded bodies therefore stay about plans, as every assertion here expects.
 */
const stubAgentFetch = (
  mock: (url: string, init: RequestInit) => Promise<unknown>
) => {
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    if (String(url).includes('/routes/progress/')) {
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ detail: { reason: 'unknown_progress_id' } }),
      } as unknown as Response);
    }
    return mock(url, init as RequestInit);
  });
};

describe('Sidebar', () => {
  beforeEach(() => {
    // The panel now reads `waypoints` to decide whether the field is planning or
    // refining, so a route left behind by an earlier test would silently change
    // which chips the next one sees. Start every test with no route.
    mockStoreState.waypoints = [];
    vi.stubGlobal('navigator', {
      ...navigator,
      geolocation: { getCurrentPosition: mockGetCurrentPosition },
    });
    // Call history outlives a test (restoreAllMocks does not clear it), and a
    // «did not ask for the position» assertion must start from zero.
    mockGetCurrentPosition.mockClear();
    mockGetCurrentPosition.mockImplementation((ok: (p: unknown) => void) => {
      ok({ coords: { latitude: 53.7, longitude: 23.8 } });
    });
    // A fresh store per test: the panel reads it through the same selectors the
    // real one uses, and a test that swapped the shape must not leak it.
    mockStoreState.waypoints = [];
    mockStoreState.placeDetails = {};
    mockStoreState.results = { data: null, show: {} };
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

  it('pins the tourist’s position as the route start from one tap', async () => {
    // The panel asks for nothing on open: an attempt fired without a gesture is
    // not answered, and the panel used to declare «не удалось определить»
    // before the tourist had asked for anything. One tap does the whole job.
    render(<Sidebar />);

    await waitFor(() => {
      expect(
        screen.getByText(/определить моё местоположение/i)
      ).toBeInTheDocument();
    });
    expect(mockGetCurrentPosition).not.toHaveBeenCalled();

    await userEvent
      .setup({ delay: null })
      .click(screen.getByText(/определить моё местоположение/i));

    await waitFor(() => expect(mockSetWaypoint).toHaveBeenCalled());
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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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
    stubAgentFetch(fetchMock);

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

  it('has no guide tab: the guide opens from one explicit action on a route', async () => {
    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    // Walking is not a section of the panel any more, so it is not in the strip.
    expect(screen.queryByTestId('mode-guide')).toBeNull();
    expect(screen.queryByTestId('mode-plan')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-history')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-itineraries')).toBeInTheDocument();
    // Nothing to walk yet: the action is not offered before there is a route.
    expect(screen.queryByTestId('guide-enter')).toBeNull();
    expect(screen.queryByTestId('guide-panel')).toBeNull();

    // Two stops — a route worth walking.
    mockStoreState.waypoints = [
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
      {
        id: '1',
        userInput: 'Новый замок',
        placeId: 12,
        geocodeResults: [
          {
            title: 'Новый замок',
            selected: true,
            sourcelnglat: [23.8223, 53.6799],
            displaylnglat: [23.8223, 53.6799],
          },
        ],
      },
    ];

    try {
      cleanup();
      render(<Sidebar />);

      await user.click(screen.getByTestId('guide-enter'));

      expect(screen.getByTestId('guide-panel')).toBeInTheDocument();
      // Guide mode takes the panel over: read nothing past the tabs, walk.
      expect(screen.queryByTestId('mode-plan')).toBeNull();
      expect(screen.queryByTestId('mode-history')).toBeNull();
      expect(screen.queryByTestId('guide-enter')).toBeNull();

      await user.click(screen.getByTestId('guide-exit'));
      expect(screen.queryByTestId('guide-panel')).toBeNull();
      // …and back to where the tourist was, with the route still in hand.
      expect(screen.getByTestId('mode-plan')).toBeInTheDocument();
      expect(buildButton()).toBeInTheDocument();
      expect(screen.getByTestId('guide-enter')).toBeInTheDocument();
    } finally {
      mockStoreState.waypoints = [];
    }
  });

  // ── The redesign (DESIGN.md phases 1–2) ────────────────────────────────────

  it('держит шапку без крестика: панель закрывает её же ручка', () => {
    render(<Sidebar />);

    // The panel is opened *and closed* by one handle on the map's left edge
    // (panel-toggle), so a second, ✕-shaped control in the header would be a
    // second way to say the same thing — and could fall out of step with it.
    expect(
      screen.queryByRole('button', { name: 'закрыть панель' })
    ).not.toBeInTheDocument();
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
      screen.getByPlaceholderText('Что хотите посмотреть?')
    ).toBeInTheDocument();
    // The chips are whole questions, not filter names: what is on them is what
    // the agent receives, so each one has to read as something a tourist says.
    for (const [id, said] of [
      ['old-town', 'Старый город за два часа пешком'],
      ['castles-churches', 'Замки и костёлы Гродно'],
      ['food', 'Где поесть в центре, недорого'],
      ['evening', 'Вечерняя прогулка по Советской'],
      ['with-children', 'С детьми: парки и замки'],
    ] as const) {
      expect(screen.getByTestId(`hint-${id}`)).toHaveTextContent(said);
    }
    // nothing typed yet → nothing to build
    expect(buildButton()).toBeDisabled();
  });

  it('keeps the main action in a footer outside the scroll area', () => {
    // Measured at 390x844 before this: the hint chips wrapped into five 44px
    // lines, the panel's content grew to 612px inside a 380px sheet, the body
    // was squeezed to 36px and the sticky footer was pushed clean out of the
    // sheet — «Построить» was not on the screen at all. The invariant that
    // broke is structural: the action must be a sibling of the scrolling body,
    // in the sheet's own flex column, not inside the part that scrolls.
    render(<Sidebar />);

    const action = buildButton();
    expect(action.closest('footer')).not.toBeNull();
    expect(action.closest('.slim-scroll')).toBeNull();
    // and the examples stay in the panel, just as one row on a phone
    const hints = screen.getByTestId('hint-old-town').parentElement;
    expect(hints?.getAttribute('role')).toBe('group');
    expect(hints?.querySelectorAll('button').length).toBe(5);
    expect(hints?.className).toContain('max-md:flex-nowrap');
    expect(hints?.className).toContain('max-md:overflow-x-auto');
  });

  it('once a route exists the chips edit it instead of starting a new one', () => {
    // A route with one real stop: planning is over, so the same field now asks
    // a different question and must not re-offer the starting questions.
    mockStoreState.waypoints = [
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
    ];

    render(<Sidebar />);

    for (const [id, said] of [
      ['refine-add-cafe', 'добавь кафе по пути'],
      ['refine-remove-museum', 'убери музей из маршрута'],
      ['refine-shorter', 'сделай короче — часа на два'],
      ['refine-only-churches', 'оставь только костёлы и замки'],
      ['refine-with-children', 'добавь что-нибудь для детей'],
    ] as const) {
      expect(screen.getByTestId(`hint-${id}`)).toHaveTextContent(said);
    }
    // The starting questions are gone: next to a finished route they read as
    // «начать заново», which is the opposite of what the field now does.
    expect(screen.queryByTestId('hint-old-town')).not.toBeInTheDocument();
    expect(
      screen.getByPlaceholderText('Что уточнить? «добавь кофейню»')
    ).toBeInTheDocument();

    mockStoreState.waypoints = [];
  });

  it('fills the query from a hint chip and submits on Enter', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.click(screen.getByTestId('hint-old-town'));
    // the chip only fills the field — the tourist still decides when to go
    expect(askField()).toHaveValue('Старый город за два часа пешком');
    expect(fetchMock).not.toHaveBeenCalled();

    await user.type(askField(), '{Enter}');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    // And what the agent gets is the sentence on the chip, word for word.
    expect(body(0).query).toBe('Старый город за два часа пешком');
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
    // The count tile inflects its own noun: 2 → «2 точки», never «2 точек».
    expect(tile('точки')).toHaveTextContent('2');
    // …and distances use the Russian decimal comma.
    expect(tile('длина')).toHaveTextContent('1,5 км');
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
    stubAgentFetch(fetchMock);

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

  it('announces route-building progress politely and errors as alerts', async () => {
    let fail: (reason?: unknown) => void = () => undefined;
    // No parameters on purpose: the test only needs the pending promise it can reject.
    const fetchMock = vi.fn(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        })
    );
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    // The polite announcement is the real, observable stage now — not a bare
    // «Строю маршрут…» that leaves the tourist with no idea what is happening.
    const announcement = await screen.findByTestId('route-progress');
    expect(announcement).toHaveAttribute('aria-live', 'polite');
    expect(announcement).toHaveTextContent(
      'отправил запрос — жду план от агента'
    );
    expect(announcement).toHaveTextContent(/\d+ с/);
    // …and a real cancel, so a 23-second wait is not a trap.
    expect(
      within(announcement).getByTestId('route-cancel')
    ).toBeInTheDocument();
    // No invented stage: the client cannot see inside the request.
    expect(announcement.textContent).not.toMatch(/проверя|анализ|требован/i);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fail(new Error('агент недоступен'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'агент недоступен'
    );
  });

  it('говорит стадию словами конвейера, когда он её называет', async () => {
    // The pipeline reports its own stages; the panel must prefer them over its
    // own (honest but vaguer) «отправил запрос», and it must never blend the two.
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes('/routes/progress/')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            stage: 'ordering_stops',
            done: false,
            failed: false,
            elapsed_ms: 4200,
          }),
        };
      }
      // The plan request itself never answers: the stage is what is under test.
      return new Promise(() => undefined);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    const announcement = await screen.findByTestId('route-progress');
    await waitFor(() =>
      expect(announcement).toHaveTextContent('собираю порядок остановок')
    );
    expect(announcement).not.toHaveTextContent('отправил запрос');
    expect(
      fetchMock.mock.calls.some((c) =>
        String(c[0]).includes('/routes/progress/')
      )
    ).toBe(true);
  });

  it('shows a Russian network hint instead of browser “Failed to fetch”', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'нет связи с агентом — проверьте сеть и попробуйте ещё раз'
    );
    expect(screen.queryByText(/Failed to fetch/i)).toBeNull();
  });

  it('shows a Russian HTTP error with the status, not raw JSON', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      text: async () =>
        JSON.stringify({ detail: 'upstream Valhalla timed out' }),
    }));
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'агент ответил ошибкой 503 — попробуйте ещё раз'
    );
    expect(alert).not.toHaveTextContent('upstream Valhalla');
    expect(alert).not.toHaveTextContent('detail');
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

  it('warns amber when the query asks for a toilet but none is in the route', async () => {
    const { fetchMock } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки и санузел');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // Route still lands: warning is non-blocking, no fabricated toilet stop.
    expect(
      await screen.findByTestId('toilet-missing-warning')
    ).toHaveTextContent(
      'В базе не нашлось туалетов — маршрут построен без них.'
    );
    expect(screen.getByTestId('toilet-missing-warning')).toHaveAttribute(
      'role',
      'status'
    );
    const planned = mockSetWaypoint.mock.calls.at(-1)?.[0] as Array<{
      placeId?: number;
      userInput?: string;
    }>;
    expect(planned).toHaveLength(3); // me + 2 agent stops, no invented toilet
    expect(planned?.map((w) => w.userInput)).toEqual([
      'Моё местоположение',
      'Старый замок',
      'Новый замок',
    ]);
  });

  it('skips the toilet warning when a returned stop has category туалет', async () => {
    const withToilet = {
      ...AGENT_ANSWER,
      points: [
        ...AGENT_ANSWER.points,
        {
          id: 99,
          name: 'Туалет у замка',
          category: 'туалет',
          lat: 53.6775,
          lon: 23.823,
          visit_minutes: 5,
        },
      ],
    };
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => withToilet,
    }));
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки и туалет');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await waitFor(() =>
      expect(mockSetWaypoint.mock.calls.length).toBeGreaterThanOrEqual(2)
    );
    expect(screen.queryByTestId('toilet-missing-warning')).toBeNull();
    expect(screen.queryByText(/В базе не нашлось туалетов/i)).toBeNull();
  });

  it('does not warn about missing toilets for a generic sightseeing query', async () => {
    // AGENT_ANSWER stops are замок/дворец — no туалет category — but the ask
    // never mentioned toilet/санузел, so the warning must stay dark.
    const { fetchMock } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки и дворцы Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await waitFor(() =>
      expect(mockSetWaypoint.mock.calls.length).toBeGreaterThanOrEqual(2)
    );
    expect(screen.queryByTestId('toilet-missing-warning')).toBeNull();
    expect(screen.queryByText(/В базе не нашлось туалетов/i)).toBeNull();
  });

  it('shows the error alert, not the toilet warning, when generate fails', async () => {
    // Boundary: a toilet ask must not leak the amber "missing toilet" status
    // when /routes/generate itself fails — only the normal error alert.
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      text: async () =>
        JSON.stringify({ detail: 'upstream Valhalla timed out' }),
    }));
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки и туалет');
    await user.click(buildButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'агент ответил ошибкой 503 — попробуйте ещё раз'
    );
    expect(screen.queryByTestId('toilet-missing-warning')).toBeNull();
    expect(screen.queryByText(/В базе не нашлось туалетов/i)).toBeNull();
  });

  // ── Advanced filters (spec 002, W5) ────────────────────────────────────────

  /** The advanced block is behind «ещё фильтры» — open it the way a user does. */
  const openAdvanced = async (user: ReturnType<typeof userEvent.setup>) => {
    const toggle = screen.getByTestId('more-filters');
    if (toggle.getAttribute('aria-expanded') !== 'true') {
      await user.click(toggle);
    }
    return screen.getByTestId('advanced-filters');
  };

  it('keeps the advanced filters closed until «ещё фильтры» is asked for', async () => {
    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    // Progressive disclosure: time and transport are visible, the rest is not.
    expect(screen.getByRole('button', { name: '2 ч' })).toBeInTheDocument();
    expect(screen.getByTestId('transport-car')).toBeInTheDocument();
    expect(screen.queryByTestId('advanced-filters')).toBeNull();
    expect(screen.getByTestId('more-filters')).toHaveAttribute(
      'aria-expanded',
      'false'
    );

    await user.click(screen.getByTestId('more-filters'));
    expect(screen.getByTestId('advanced-filters')).toBeInTheDocument();
    expect(screen.getByTestId('more-filters')).toHaveAttribute(
      'aria-expanded',
      'true'
    );
    // the new controls live inside it
    expect(screen.getByTestId('party-children-inc')).toBeInTheDocument();
    expect(screen.getByTestId('amenity-туалет-hard')).toBeInTheDocument();
    expect(screen.getByTestId('result-mode-catalogue')).toBeInTheDocument();
  });

  it('sends nothing for the new fields while they are left alone', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // "send nothing when not chosen" — as with time and transport.
    for (const field of [
      'party_adults',
      'party_children',
      'party_children_ages',
      'hard_services',
      'interests',
      'avoid',
      'result_mode',
      'round_trip',
      'mobility',
      'locale',
    ]) {
      expect(field in body(0)).toBe(false);
    }
  });

  it('sends the party, mandatory amenities, interests, mode and round trip', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);
    // 2 adults, 1 child, ages named by hand (never inferred)
    await user.click(screen.getByTestId('party-adults-inc'));
    await user.click(screen.getByTestId('party-adults-inc'));
    await user.click(screen.getByTestId('party-children-inc'));
    await user.type(screen.getByTestId('children-ages'), '4, 7');
    // туалет обязателен, кафе желательно, интерес — замки
    await user.click(screen.getByTestId('amenity-туалет-hard'));
    await user.click(screen.getByTestId('amenity-кафе-soft'));
    await user.click(screen.getByTestId('interest-замок'));
    await user.click(screen.getByTestId('avoid-кладбище'));
    await user.click(screen.getByTestId('result-mode-catalogue'));
    await user.click(screen.getByTestId('round-trip'));

    await user.type(askField(), 'старый Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    expect(body(0).party_adults).toBe(2);
    expect(body(0).party_children).toBe(1);
    expect(body(0).party_children_ages).toEqual([4, 7]);
    expect(body(0).hard_services).toEqual(['туалет']);
    // a soft amenity joins the interests it is: one list to the agent
    expect(body(0).interests).toEqual(['замок', 'кафе']);
    expect(body(0).avoid).toEqual(['кладбище']);
    expect(body(0).result_mode).toBe('catalogue');
    expect(body(0).round_trip).toBe(true);
    expect(body(0).query).toBe('старый Гродно');
  });

  it('остановка транспорта отправляется как hard_service целиком', async () => {
    const { fetchMock, sentBodies } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);
    await user.click(screen.getByTestId('amenity-остановка транспорта-hard'));
    await user.type(askField(), 'прогулка по городу');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const body = sentBodies[0]!;
    // код из двух слов должен дойти одним элементом, не разбитым по пробелу
    expect(body.hard_services).toContain('остановка транспорта');
    expect(body.hard_services).not.toContain('остановка');
    expect(body.hard_services).not.toContain('транспорта');
  });

  it('группа фильтров уходит всеми своими кодами и снимается целиком', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);

    // «всё религиозное» is a label for four real categories — the agent gets the
    // codes themselves, because the data has no parent category to send. The
    // chip is chosen only when every code it stands for is.
    const group = () => screen.getByTestId('interest-религиозное');
    expect(group()).toHaveAttribute('aria-pressed', 'false');

    await user.click(group());
    expect(group()).toHaveAttribute('aria-pressed', 'true');
    // …and the single chips it covers read as chosen too: one state, two views.
    expect(screen.getByTestId('interest-костёл')).toHaveAttribute(
      'aria-pressed',
      'true'
    );

    await user.type(askField(), 'старый город');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(body(0).interests).toEqual([
      'костёл',
      'церковь',
      'храм',
      'монастырь',
    ]);

    // Clicking the group again clears the whole thing, never half of it.
    await user.click(group());
    expect(group()).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('interest-костёл')).toHaveAttribute(
      'aria-pressed',
      'false'
    );
  });

  it('steps the party back to unset instead of inventing a group', async () => {
    const { fetchMock, body } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);
    await user.click(screen.getByTestId('party-children-inc'));
    expect(screen.getByTestId('party-children-value')).toHaveTextContent('1');
    // stepping below one clears the count: "not said" is not "0 children"
    await user.click(screen.getByTestId('party-children-dec'));
    expect(screen.getByTestId('party-children-value')).toHaveTextContent('—');

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect('party_children' in body(0)).toBe(false);
  });

  it('summarises the chosen filters and says they outrank the text query', async () => {
    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    // nothing chosen yet → no summary to show
    expect(screen.queryByTestId('filters-summary')).toBeNull();

    await openAdvanced(user);
    await user.click(screen.getByTestId('amenity-туалет-hard'));
    await user.click(screen.getByTestId('result-mode-catalogue'));
    await user.click(screen.getByTestId('more-filters')); // close again
    expect(screen.queryByTestId('advanced-filters')).toBeNull();

    // the summary stays visible with the panel closed
    const summary = screen.getByTestId('filters-summary');
    expect(summary).toHaveTextContent('обязательно: туалет');
    expect(summary).toHaveTextContent('каталог мест');

    // a conflict with the typed request is shown, not silently resolved
    await user.type(askField(), 'замки без туалета');
    expect(screen.getByTestId('filters-precedence')).toHaveTextContent(
      /поверх текста запроса/
    );
  });

  it('keeps the chosen filters on a refinement turn', async () => {
    const sentBodies: string[] = [];
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      sentBodies.push(String(init.body));
      return { ok: true, json: async () => AGENT_ANSWER };
    });
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);
    await user.click(screen.getByTestId('party-children-inc'));
    await user.click(screen.getByTestId('amenity-туалет-hard'));
    await user.click(screen.getByTestId('result-mode-catalogue'));

    await user.type(askField(), 'музеи Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(sentBodies[0]!).hard_services).toEqual(['туалет']);

    // From here the route exists: the second submit is a refinement.
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
      refinementLog: [],
      excludedPlaceIds: [],
      snapshotRoute: vi.fn(),
    });

    await user.type(askField(), 'добавь кофейню');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const body = JSON.parse(sentBodies[1]!);
    // the refinement carries the route context AND the surviving filters
    expect(body.context).toBeDefined();
    expect(body.party_children).toBe(1);
    expect(body.hard_services).toEqual(['туалет']);
    expect(body.result_mode).toBe('catalogue');
  });

  it('warns about a missing toilet when «туалет» was marked mandatory', async () => {
    const { fetchMock } = agentFetch();

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await openAdvanced(user);
    await user.click(screen.getByTestId('amenity-туалет-hard'));

    // the text never mentions a toilet — the explicit filter does
    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    expect(
      await screen.findByTestId('toilet-missing-warning')
    ).toHaveTextContent(
      'В базе не нашлось туалетов — маршрут построен без них.'
    );
  });
});

// ── Felt-quality pass: reachability, touch, focus order, honest counts ──────

/** Focusable elements in DOM order — that is exactly the tab order. */
const tabOrder = (root: HTMLElement): HTMLElement[] =>
  Array.from(
    root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  );

describe('Sidebar — felt quality', () => {
  beforeEach(() => {
    mockGetCurrentPosition.mockImplementation((ok: (p: unknown) => void) => {
      ok({ coords: { latitude: 53.7, longitude: 23.8 } });
    });
    mockStoreState.routeHistory = [];
    // Same reason as in the first block: a route from an earlier test would turn
    // the field into refinement mode and swap the chips under this test.
    mockStoreState.waypoints = [];
    mockStoreState.setWaypoint.mockImplementation(
      (next: Record<string, unknown>[]) => {
        mockStoreState.waypoints = next;
      }
    );
    mockGetState.mockReturnValue(mockStoreState);
  });

  it('даёт туристу задать ширину панели и помнит её', async () => {
    render(<Sidebar />);

    const handle = screen.getByTestId('panel-resize-handle');
    // Ширина живёт в CSS-переменной, а её читает класс панели только с md —
    // мобильная шторка остаётся во всю ширину экрана.
    const panelAt = () => document.querySelector('[style*="--panel-width"]');
    expect(handle).toHaveAttribute('aria-valuenow', '420');
    expect(panelAt()?.getAttribute('style')).toContain('--panel-width: 420px');

    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 460, pointerId: 1 });
    fireEvent.pointerUp(handle, { clientX: 460, pointerId: 1 });

    expect(handle).toHaveAttribute('aria-valuenow', '480');
    expect(panelAt()?.getAttribute('style')).toContain('--panel-width: 480px');
  });

  it('делает поле запроса первым таб-стопом панели', () => {
    render(<Sidebar />);

    // Nothing precedes the question: no close button, no title control.
    expect(tabOrder(screen.getByRole('dialog'))[0]).toBe(askField());
  });

  it('поднимает вкладки, чипы и поле запроса до 44px на телефоне', () => {
    render(<Sidebar />);

    expect(screen.getByTestId('mode-plan').className).toMatch(/max-md:h-11/);
    expect(screen.getByTestId('mode-itineraries').className).toMatch(
      /max-md:h-11/
    );
    for (const id of [
      'old-town',
      'castles-churches',
      'food',
      'evening',
      'with-children',
    ]) {
      expect(screen.getByTestId(`hint-${id}`).className).toMatch(/max-md:h-11/);
    }
    expect(askField().className).toMatch(/max-md:min-h-11/);
    // …and coarse pointers (tablets, touch laptops) get the same target
    expect(screen.getByTestId('mode-plan').className).toMatch(
      /pointer-coarse:h-11/
    );
    expect(screen.getByTestId('hint-old-town').className).toMatch(
      /pointer-coarse:h-11/
    );
  });

  it('shows elapsed time and a real cancel, and cancelling is not an error', async () => {
    let aborted = false;
    const fetchMock = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => {
            aborted = true;
            const err = new Error('The user aborted a request.');
            err.name = 'AbortError';
            reject(err);
          });
        })
    );
    stubAgentFetch(fetchMock);

    const user = userEvent.setup({ delay: null });
    render(<Sidebar />);

    await user.type(askField(), 'замки Гродно');
    await user.click(buildButton());

    const progress = await screen.findByTestId('route-progress');
    expect(progress).toHaveAttribute('data-stage', 'requesting');
    expect(progress).toHaveTextContent(/\d+ с/);

    await user.click(within(progress).getByTestId('route-cancel'));

    await waitFor(() => expect(aborted).toBe(true));
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Запрос отменён'
    );
    expect(screen.queryByTestId('route-progress')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('inflects the history row («3 места», not «3 мест»)', async () => {
    mockStoreState.routeHistory = [
      {
        id: 'h1',
        query: 'три остановки',
        timeBudget: 120,
        places: [1, 2, 3].map((id) => ({
          id,
          name: `Место ${id}`,
          category: 'замок',
          lat: 53.68,
          lon: 23.83,
          blurb: null,
          funFact: null,
          funFacts: [],
          links: [],
          visitMinutes: 30,
          openingHours: null,
          ticketPrice: null,
          town: null,
          district: null,
        })),
      },
    ];
    mockGetState.mockReturnValue(mockStoreState);

    render(<Sidebar />);

    // History has its own tab now, so the row is not on screen until it is
    // opened — that is the point of the tab, not a regression.
    await userEvent
      .setup({ delay: null })
      .click(screen.getByTestId('mode-history'));

    expect(screen.getByText('3 места')).toBeInTheDocument();
    expect(screen.queryByText('3 мест')).toBeNull();
  });

  it('switches views: planner, history, ready-made routes', async () => {
    render(<Sidebar />);
    const user = userEvent.setup({ delay: null });

    // Planner: the ask field is the view's own content.
    expect(askField()).toBeInTheDocument();

    await user.click(screen.getByTestId('mode-history'));
    expect(screen.getByTestId('history-tab')).toBeInTheDocument();
    // The planner's own controls are gone, not merely scrolled away.
    expect(
      screen.queryByRole('textbox', { name: 'что хотите посмотреть' })
    ).toBeNull();
    // Nothing to build from this view, so the build action is not offered.
    expect(
      screen.queryByRole('button', { name: /подобрать маршрут/i })
    ).toBeNull();

    await user.click(screen.getByTestId('mode-itineraries'));
    // An empty authored list is stated as empty, not dressed up as a route.
    expect(screen.getByTestId('itineraries-empty')).toBeInTheDocument();

    await user.click(screen.getByTestId('mode-plan'));
    expect(askField()).toBeInTheDocument();
  });

  it('opens a ready-made route on the map without asking the model for it', async () => {
    mockItineraries.items = [
      {
        id: 'old-town-castles',
        title: 'Два замка и Советская',
        blurb: 'Сердце старого города',
        transport: 'pedestrian',
        stop_count: 2,
        visit_minutes: 150,
        stops: [
          {
            place_id: 1,
            source_url: 'city:old-castle',
            name: 'Старый замок (Гродно)',
            category: 'замок',
            town: 'Гродно',
            district: null,
            lat: 53.6791,
            lon: 23.8216,
            visit_minutes: 90,
            opening_hours: 'вт–вс 10:00–18:00',
            blurb: 'Королевский замок Витовта',
            fun_fact: 'Факт',
            fun_facts: [],
            links: [],
            ticket_price: null,
          },
          {
            place_id: 2,
            source_url: 'city:new-castle',
            name: 'Новый замок',
            category: 'дворец',
            town: 'Гродно',
            district: null,
            lat: 53.6799,
            lon: 23.8223,
            visit_minutes: 60,
            opening_hours: null,
            blurb: null,
            fun_fact: null,
            fun_facts: [],
            links: [],
            ticket_price: null,
          },
        ],
      },
    ];

    // Any request at all in this test is a request the tab should not make.
    const fetchMock = vi.fn();
    stubAgentFetch(fetchMock);

    try {
      render(<Sidebar />);
      const user = userEvent.setup({ delay: null });

      await user.click(screen.getByTestId('mode-itineraries'));
      await user.click(screen.getByTestId('itinerary-open-old-town-castles'));

      // The stops are handed to the map as waypoints…
      await waitFor(() => expect(mockSetWaypoint).toHaveBeenCalled());
      const waypoints = mockSetWaypoint.mock.calls.at(-1)![0] as Array<{
        userInput: string;
        placeId: number;
      }>;
      expect(waypoints.map((w) => w.userInput)).toEqual([
        'Старый замок (Гродно)',
        'Новый замок',
      ]);
      expect(waypoints.map((w) => w.placeId)).toEqual([1, 2]);
      // …the router is asked to draw them…
      expect(mockRefetch).toHaveBeenCalled();
      // …and the model is never asked: this tab exists to avoid that request.
      expect(fetchMock).not.toHaveBeenCalled();
      // A ready-made route is still a route someone may walk, so it lands in the
      // history with its own stop key — that is what a finished walk marks.
      const entry = mockStoreState.addToHistory.mock.calls.at(-1)![0] as {
        query: string;
        routeKey: string;
        places: unknown[];
      };
      expect(entry.query).toBe('Два замка и Советская');
      expect(entry.routeKey).toContain('@');
      expect(entry.places).toHaveLength(2);
      // Back on the planner, where the route can be refined as usual.
      expect(askField()).toBeInTheDocument();
    } finally {
      mockItineraries.items = [];
    }
  });
});
