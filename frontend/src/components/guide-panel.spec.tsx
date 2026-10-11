import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: unknown[][] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
vi.mock('sonner', () => ({ toast: (...args: any[]) => toastCalls.push(args) }));

vi.mock('@/hooks/use-services-along', () => ({
  useServicesAlong: vi.fn(),
}));

vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => ({
    user: null,
    authenticated: false,
    isAdmin: false,
    isLoading: false,
  }),
  describeAccountError: (error: unknown) => String(error),
}));
vi.mock('@/hooks/use-visited', () => ({
  useVisitedIds: () => new Set<number>(),
  useToggleVisited: () => ({ mutate: vi.fn(), isPending: false }),
}));

import type { ParsedDirectionsGeometry } from '@/components/types';
import i18n from '@/i18n';
import { useCommonStore } from '@/stores/common-store';
import { useDirectionsStore } from '@/stores/directions-store';

import { installGeoSim, resetSim } from '@/lib/geo-sim';
import { useServicesAlong } from '@/hooks/use-services-along';

import { GuidePanel, guideRouteKey, type GuideStop } from './guide-panel';

afterEach(async () => {
  await i18n.changeLanguage('ru');
  useCommonStore.setState({ guideVoiceMuted: false });
});

const STOPS: GuideStop[] = [
  {
    id: '1',
    name: 'Монастырь бригиток',
    lat: 53.6778,
    lon: 23.8295,
    placeId: 101,
    category: 'монастырь',
    visitMinutes: 30,
  },
  {
    id: '2',
    name: 'Кафе Немо',
    lat: 53.6832,
    lon: 23.8365,
    placeId: 102,
    category: 'кафе',
    visitMinutes: 40,
  },
];

/** A route line that runs through both stops, with one turn at the middle vertex. */
const ROUTE = {
  id: 'valhalla_directions',
  decodedGeometry: [
    [53.6778, 23.8295],
    [53.6788, 23.8305],
    [53.6832, 23.8365],
  ],
  trip: {
    locations: [],
    status_message: 'ok',
    status: 0,
    units: 'kilometers',
    language: 'ru-RU',
    summary: {
      length: 0.76,
      time: 590,
      cost: 0,
      has_time_restrictions: false,
      has_toll: false,
      has_highway: false,
      has_ferry: false,
      min_lat: 0,
      min_lon: 0,
      max_lat: 0,
      max_lon: 0,
    },
    legs: [
      {
        shape: '',
        summary: { length: 0.76, time: 590, cost: 0 },
        maneuvers: [
          {
            type: 1,
            instruction: 'Идите на север по Советской',
            time: 0,
            length: 0,
            cost: 0,
            begin_shape_index: 0,
            end_shape_index: 0,
          },
          {
            type: 10,
            instruction: 'Поверните направо к Кафе Немо',
            time: 120,
            length: 0.35,
            cost: 0,
            begin_shape_index: 1,
            end_shape_index: 1,
          },
          {
            type: 4,
            instruction: 'Вы прибыли в пункт назначения',
            time: 10,
            length: 0,
            cost: 0,
            begin_shape_index: 2,
            end_shape_index: 2,
          },
        ],
      },
    ],
  },
} as unknown as ParsedDirectionsGeometry;

/** Leaving navigation is the sidebar's business; the panel only reports it. */
const noop = () => {};

const seedRoute = () =>
  useDirectionsStore.getState().receiveRouteResults({ data: ROUTE });

const stubGeolocation = (
  coords: { latitude: number; longitude: number } | null
) => {
  const watchPosition = vi.fn(
    (ok: (p: unknown) => void, err: (e: unknown) => void) => {
      if (coords) ok({ coords });
      else err(new Error('denied'));
      return 1;
    }
  );
  Object.defineProperty(navigator, 'geolocation', {
    value: { watchPosition, clearWatch: vi.fn() },
    configurable: true,
  });
};

/** Geolocation watcher that stays quiet until the test pushes a position. */
const stubWatchingGeolocation = () => {
  const listeners: Array<(pos: unknown) => void> = [];
  const failures: Array<(err: unknown) => void> = [];
  const watchPosition = vi.fn(
    (ok: (p: unknown) => void, err: (e: unknown) => void) => {
      listeners.push(ok);
      failures.push(err);
      return 1;
    }
  );
  Object.defineProperty(navigator, 'geolocation', {
    value: { watchPosition, clearWatch: vi.fn() },
    configurable: true,
  });
  return {
    watchPosition,
    /** Walk to `metres` north of the given stop (1° lat ≈ 111 320 m). */
    standNorthOf: (stop: GuideStop, metres: number) => {
      act(() => {
        listeners.forEach((ok) =>
          ok({
            coords: {
              latitude: stop.lat + metres / 111_320,
              longitude: stop.lon,
            },
          })
        );
      });
    },
    /** A fix at an exact coordinate, with the accuracy the device reports. */
    push: (lat: number, lon: number, accuracy = 8) => {
      act(() => {
        listeners.forEach((ok) =>
          ok({
            coords: { latitude: lat, longitude: lon, accuracy },
            timestamp: Date.now(),
          })
        );
      });
    },
    fail: () => {
      act(() => {
        failures.forEach((err) => err({ code: 1, message: 'denied' }));
      });
    },
  };
};

describe('GuidePanel', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(useServicesAlong).mockReturnValue({
      items: [],
      state: 'idle',
      measured: null,
      maxOffLineM: null,
      reason: null,
      capped: false,
    });
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    Reflect.deleteProperty(navigator, 'geolocation');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('помнит пройденную остановку и после того, как турист пошёл дальше', () => {
    let push: ((p: unknown) => void) | null = null;
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        watchPosition: (ok: (p: unknown) => void) => {
          push = ok;
          return 1;
        },
        clearWatch: () => {},
        getCurrentPosition: () => {},
      },
    });

    render(<GuidePanel stops={STOPS} onExit={noop} />);
    act(() => {
      push?.({
        coords: {
          latitude: STOPS[1]!.lat,
          longitude: STOPS[1]!.lon,
          accuracy: 8,
        },
      });
    });
    act(() => {
      push?.({ coords: { latitude: 53.7, longitude: 23.9, accuracy: 8 } });
    });

    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('отмечает остановку, на которой стоит турист, даже если предыдущую он пропустил', () => {
    stubGeolocation({ latitude: STOPS[1]!.lat, longitude: STOPS[1]!.lon });
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('explains itself when there is no route yet', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={[]} onExit={noop} />);

    expect(
      screen.getByText(/соберите маршрут в режиме планирования/i)
    ).toBeInTheDocument();
  });

  it('не выдаёт симуляцию за настоящий GPS', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.queryByTestId('guide-simulated')).not.toBeInTheDocument();
  });

  it('говорит вслух, когда положение проигрывается', () => {
    installGeoSim('?sim=walk&sim-speed=20');
    try {
      stubGeolocation(null);
      render(<GuidePanel stops={STOPS} onExit={noop} />);

      expect(screen.getByTestId('guide-simulated')).toHaveTextContent(
        /СИМУЛЯЦИЯ GPS/
      );
    } finally {
      resetSim();
    }
  });

  it('marks stops by hand and keeps the progress across a reload', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    const { unmount } = render(<GuidePanel stops={STOPS} onExit={noop} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    unmount();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('marks the stop it is standing at once geolocation arrives', async () => {
    stubGeolocation({ latitude: STOPS[0]!.lat, longitude: STOPS[0]!.lon });
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    await waitFor(() =>
      expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument()
    );
  });

  it('keeps manual stop marking available without a GPS status banner', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.queryByTestId('guide-geo-status')).toBeNull();
    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('starts a fresh walk when the route is rebuilt', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    const { unmount } = render(<GuidePanel stops={STOPS} onExit={noop} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    const rebuilt = [
      ...STOPS,
      { id: '3', name: 'Туалет', lat: 53.68, lon: 23.83 },
    ];
    unmount();
    render(
      <GuidePanel key={guideRouteKey(rebuilt)} stops={rebuilt} onExit={noop} />
    );

    expect(screen.getByText(/пройдено 0 из 3/i)).toBeInTheDocument();
  });

  it('keeps route stops in the list without duplicating the next stop card', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.queryByTestId('guide-next-stop')).toBeNull();
    expect(screen.getByTestId('guide-stop-1')).toHaveTextContent(
      'Монастырь бригиток'
    );
    expect(
      screen.getByRole('button', {
        name: /время осмотра: примерно 30 минут, изменить/i,
      })
    ).toBeInTheDocument();
  });

  it('lets the tourist set their own time at a stop, and keeps it for the totals', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    fireEvent.click(
      screen.getByRole('button', {
        name: /время осмотра: примерно 30 минут, изменить/i,
      })
    );
    fireEvent.click(screen.getByTestId('visit-time-plus'));

    expect(
      screen.getByRole('button', {
        name: /время осмотра: 40 минут, изменить/i,
      })
    ).toBeInTheDocument();
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~1 ч 20 мин'
    );
  });

  it('updates route progress as GPS advances without adding a duplicate stop card', () => {
    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    geo.standNorthOf(STOPS[0]!, 240);
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
    expect(screen.queryByTestId('guide-next-stop')).toBeNull();

    geo.standNorthOf(STOPS[0]!, 20);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-stop-2')).toHaveTextContent('Кафе Немо');
  });

  it('starts in moving mode when a trusted GPS fix is already available', () => {
    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );
    expect(screen.getByTestId('guide-maneuver')).toBeInTheDocument();
    expect(screen.getByTestId('guide-advance')).toBeInTheDocument();
    expect(screen.queryByTestId('guide-start')).toBeNull();
  });

  it('falls back to review mode when GPS is not yet available', () => {
    stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'review'
    );
    expect(screen.getByTestId('guide-start')).toBeInTheDocument();
  });

  it('starts navigating immediately when opened from the route action', () => {
    stubGeolocation(null);
    const onExit = vi.fn();
    render(<GuidePanel stops={STOPS} startInMoving onExit={onExit} />);

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );
    expect(screen.queryByTestId('guide-start')).not.toBeInTheDocument();
    expect(screen.getByTestId('guide-advance')).toBeInTheDocument();
    expect(screen.queryByTestId('guide-mobile-actions')).toBeNull();
    expect(screen.queryByTestId('guide-exit-mobile-hud')).toBeNull();
    expect(screen.queryByTestId('guide-voice-toggle-mobile')).toBeNull();
    expect(screen.queryByTestId('guide-geo-status')).toBeNull();
    expect(onExit).not.toHaveBeenCalled();
  });

  it('shows the route overview when the mobile sheet is expanded, then resumes', () => {
    stubGeolocation(null);
    const onExit = vi.fn();
    const { rerender } = render(
      <GuidePanel stops={STOPS} startInMoving onExit={onExit} />
    );

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );

    rerender(
      <GuidePanel stops={STOPS} startInMoving overviewOpen onExit={onExit} />
    );

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'review'
    );
    expect(screen.queryByTestId('guide-maneuver')).toBeNull();
    expect(screen.queryByTestId('guide-bottom-stack')).toBeNull();
    expect(screen.queryByTestId('guide-next-stop')).toBeNull();
    expect(screen.getByTestId('guide-header')).toBeInTheDocument();
    expect(screen.getByTestId('guide-stop-1')).toBeInTheDocument();
    expect(screen.getByTestId('guide-exit-overview')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('guide-exit-overview'));
    expect(onExit).toHaveBeenCalledOnce();

    rerender(<GuidePanel stops={STOPS} startInMoving onExit={noop} />);

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );
  });

  it('mutes the stops already walked and keeps the next one in the accent', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    const first = screen.getByTestId('guide-stop-1');
    const second = screen.getByTestId('guide-stop-2');
    expect(first).toHaveAttribute('aria-current', 'step');
    expect(second).not.toHaveAttribute('aria-current');

    await user.click(first);

    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(within(first).getByText('Монастырь бригиток')).toHaveClass(
      'line-through'
    );
    expect(first).not.toHaveAttribute('aria-current');
    expect(second).toHaveAttribute('aria-current', 'step');

    await user.click(first);
    expect(first).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('counts the walk in the progress bar and in the remaining time', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    const bar = screen.getByRole('progressbar', { name: /прогресс/i });
    expect(bar).toHaveAttribute('aria-valuenow', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '2');
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~1 ч 10 мин'
    );

    await user.click(screen.getByTestId('guide-stop-1'));

    expect(bar).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~40 мин'
    );
  });

  it('forgets the progress of a route it no longer has', () => {
    stubGeolocation(null);
    localStorage.setItem(
      'grodno-guide-progress',
      JSON.stringify({ route: 'some-other-route', visited: ['1', '2'] })
    );
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('resets the walk on request', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    await user.click(screen.getByTestId('guide-stop-2'));
    expect(screen.getByText(/пройдено 2 из 2/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'сбросить' }));

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
    expect(screen.queryByTestId('guide-next-stop')).toBeNull();
    const stored = JSON.parse(
      localStorage.getItem('grodno-guide-progress') ?? '{}'
    ) as { visited: string[] };
    expect(stored.visited).toEqual([]);
  });
});

describe('GuidePanel · режим движения', () => {
  beforeEach(() => {
    localStorage.clear();
    seedRoute();
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    Reflect.deleteProperty(navigator, 'geolocation');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const start = async () => {
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));
    return user;
  };

  it('hands the screen to the navigator on «начать маршрут»', async () => {
    stubGeolocation(null);
    await start();

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );
    expect(screen.getByTestId('guide-maneuver')).toBeInTheDocument();
    expect(screen.getByTestId('guide-advance')).toBeInTheDocument();
  });

  it('shows the next manoeuvre with a distance on a good fix', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-maneuver-instruction')).toHaveTextContent(
      'Поверните направо к Кафе Немо'
    );
    expect(screen.getByTestId('guide-maneuver-distance')).toHaveTextContent(
      /через \d+ м/
    );
  });

  it('suppresses the confident distance when the GPS is poor', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 120);

    expect(screen.queryByTestId('guide-maneuver-distance')).toBeNull();
    expect(screen.getByTestId('guide-maneuver-unprecise')).toHaveTextContent(
      /расстояние скрыто/i
    );
    expect(screen.queryByTestId('guide-geo-status')).toBeNull();
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('lets the tourist advance stops by hand without geolocation', async () => {
    const geo = stubWatchingGeolocation();
    const user = await start();

    geo.fail();
    expect(screen.queryByTestId('guide-geo-status')).toBeNull();

    await user.click(screen.getByTestId('guide-advance'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-advance'));
    expect(screen.getByText(/пройдено 2 из 2/i)).toBeInTheDocument();
  });

  it('сообщает, насколько пройден маршрут, — это забирает история', async () => {
    const onWalked = vi.fn();
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onWalked={onWalked} onExit={noop} />);

    await waitFor(() =>
      expect(onWalked).toHaveBeenLastCalledWith({ visited: 0, total: 2 })
    );

    geo.fail();
    await user.click(screen.getByTestId('guide-start'));
    await waitFor(() =>
      expect(screen.getByTestId('guide-advance')).toBeInTheDocument()
    );

    await user.click(screen.getByTestId('guide-advance'));
    await waitFor(() =>
      expect(onWalked).toHaveBeenLastCalledWith({ visited: 1, total: 2 })
    );

    await user.click(screen.getByTestId('guide-advance'));
    await waitFor(() =>
      expect(onWalked).toHaveBeenLastCalledWith({ visited: 2, total: 2 })
    );
  });

  it('offers a re-plan once the tourist is clearly off route', async () => {
    const geo = stubWatchingGeolocation();
    const onReroute = vi.fn();
    const user = userEvent.setup();
    useDirectionsStore.getState().setWaypoint([
      {
        id: 'me',
        userInput: 'Моё местоположение',
        geocodeResults: [
          {
            title: 'Моё местоположение',
            selected: true,
            displaylnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            sourcelnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            key: 0,
            addressindex: 0,
          },
        ],
      },
      {
        id: '1',
        userInput: STOPS[0]!.name,
        placeId: 101,
        geocodeResults: [
          {
            title: STOPS[0]!.name,
            selected: true,
            displaylnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            sourcelnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            key: 1,
            addressindex: 0,
          },
        ],
      },
    ]);

    render(<GuidePanel stops={STOPS} onReroute={onReroute} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    expect(screen.queryByTestId('guide-off-route')).toBeNull();

    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    expect(screen.getByTestId('guide-off-route')).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-reroute'));
    expect(onReroute).toHaveBeenCalledTimes(1);

    const ids = useDirectionsStore
      .getState()
      .waypoints.map((w) => w.id)
      .sort();
    expect(ids).toEqual(['1', 'me']);
  });

  it('drops no stop when the re-plan falls back to the store', async () => {
    const geo = stubWatchingGeolocation();
    const user = await start();
    useDirectionsStore.getState().setWaypoint([
      {
        id: '1',
        userInput: STOPS[0]!.name,
        placeId: 101,
        geocodeResults: [
          {
            title: STOPS[0]!.name,
            selected: true,
            displaylnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            sourcelnglat: [STOPS[0]!.lon, STOPS[0]!.lat],
            key: 0,
            addressindex: 0,
          },
        ],
      },
    ]);

    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    expect(screen.getByTestId('guide-off-route')).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-reroute'));

    const ids = useDirectionsStore
      .getState()
      .waypoints.map((w) => w.id)
      .sort();
    expect(ids).toEqual(['1', 'me']);
  });

  it('opens the stops from the map HUD and lets the tourist mark one', async () => {
    const geo = stubWatchingGeolocation();
    const user = await start();
    geo.push(STOPS[0]!.lat - 0.0005, STOPS[0]!.lon, 8);

    const detailsToggle = screen.getByTestId('guide-route-details-toggle');
    expect(screen.queryByTestId('guide-details-panel')).toBeNull();

    await user.click(detailsToggle);
    expect(screen.getByTestId('guide-details-panel')).toBeInTheDocument();
    expect(screen.getByTestId('guide-stop-1')).toHaveTextContent(
      'Монастырь бригиток'
    );
    expect(screen.getByTestId('guide-advance')).toHaveAccessibleName(
      'отметить остановку «Монастырь бригиток»'
    );

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByTestId('guide-stop-1')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
  });

  it('shows information for the next place without opening the planner', async () => {
    stubGeolocation(null);
    useDirectionsStore.getState().setPlaceDetails({
      101: {
        name: 'Монастырь бригиток',
        category: 'монастырь',
        blurb: 'Барочный монастырский комплекс в центре города.',
        funFact: 'Здесь сохранились старинные фрески.',
        funFacts: ['История монастыря'],
        links: [],
        visitMinutes: 30,
      },
    });
    const user = await start();

    expect(screen.queryByTestId('guide-next-place')).toBeNull();
    expect(screen.queryByTestId('guide-place-details-toggle')).toBeNull();

    await user.click(screen.getByTestId('guide-route-details-toggle'));

    expect(screen.getByTestId('guide-details-panel')).toHaveTextContent(
      'Барочный монастырский комплекс в центре города.'
    );
    expect(screen.getByTestId('guide-place-details-toggle')).toHaveAttribute(
      'aria-expanded',
      'false'
    );

    await user.click(screen.getByTestId('guide-place-details-toggle'));

    expect(screen.getByTestId('guide-place-details-toggle')).toHaveAttribute(
      'aria-expanded',
      'true'
    );
    expect(
      screen.getByText('Здесь сохранились старинные фрески.')
    ).toBeInTheDocument();
  });

  it('never lets a suggestion change the route by itself', async () => {
    stubGeolocation(null);
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(
      <GuidePanel
        stops={STOPS}
        suggestions={[
          { id: 'w1', name: 'Туалет у ратуши', detail: 'отклонение 4 мин' },
        ]}
        onAddSuggestion={onAdd}
        onExit={noop}
      />
    );
    await user.click(screen.getByTestId('guide-start'));

    await user.click(screen.getByTestId('guide-route-details-toggle'));
    expect(screen.getByTestId('guide-suggestions')).toHaveTextContent(
      'Туалет у ратуши'
    );

    await user.click(screen.getByTestId('guide-suggestion-add-w1'));
    expect(onAdd).toHaveBeenCalledWith('w1');
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-suggestion-skip-w1'));
    expect(screen.queryByTestId('guide-suggestions')).toBeNull();
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('reads the line progress off the route geometry', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-line-progress')).toHaveTextContent(
      /по линии пройдено 0 м/
    );
    expect(screen.getByTestId('guide-line-progress')).toHaveTextContent(
      /осталось \d+[.,]\d км|осталось \d+ м/
    );
    expect(
      screen.getByRole('progressbar', { name: /прогресс/i })
    ).toHaveAttribute('aria-valuetext', 'пройдено 0% линии');
  });

  it('announces the turn politely and without a number on a poor fix', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 200);
    const live = screen.getByRole('status');
    expect(live).toHaveTextContent('Поверните направо к Кафе Немо');
    expect(live.textContent ?? '').not.toMatch(/через \d+ м/);
  });

  it('показывает крупную дистанцию, когда GPS хороший', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    const distance = screen.getByTestId('guide-maneuver-distance');
    expect(distance).toHaveTextContent(/\d+ м|\d+[.,]\d км/i);
    expect(distance.className).toContain('text-2xl');
  });

  it('скрывает крупную дистанцию, когда GPS слабый', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 120);

    expect(screen.queryByTestId('guide-maneuver-distance')).toBeNull();
    expect(screen.getByTestId('guide-maneuver-unprecise')).toBeInTheDocument();
  });
});

describe('GuidePanel · NearbyHint', () => {
  beforeEach(() => {
    localStorage.clear();
    seedRoute();
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    vi.restoreAllMocks();
  });

  const toiletService = {
    id: 1,
    source_url: 'https://example.com/toilet',
    name: 'Туалет у ратуши',
    category: 'туалет',
    town: null,
    lat: 53.679,
    lon: 23.831,
    opening_hours: null,
    hours_known: false,
    off_line_m: 5,
    along_m: 50,
    along_fraction: 0.07,
    detour_confirmed: false,
  } as const;

  const start = async () => {
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));
    return user;
  };

  it('появляется, когда POI впереди на расстоянии до 120 м', async () => {
    vi.mocked(useServicesAlong).mockReturnValue({
      items: [toiletService],
      state: 'ready',
      measured: null,
      maxOffLineM: null,
      reason: null,
      capped: false,
    });

    await start();

    expect(screen.getByTestId('guide-nearby-hint')).toBeInTheDocument();
    expect(screen.getByTestId('guide-nearby-hint')).toHaveTextContent(
      /туалет/i
    );
  });

  it('не показывается, если сервисы ещё не загружены', async () => {
    vi.mocked(useServicesAlong).mockReturnValue({
      items: [],
      state: 'idle',
      measured: null,
      maxOffLineM: null,
      reason: null,
      capped: false,
    });

    await start();

    expect(screen.queryByTestId('guide-nearby-hint')).toBeNull();
  });

  it('не спамит — тот же POI не показывается повторно', async () => {
    vi.mocked(useServicesAlong).mockReturnValue({
      items: [toiletService],
      state: 'ready',
      measured: null,
      maxOffLineM: null,
      reason: null,
      capped: false,
    });

    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-nearby-hint')).toBeInTheDocument();

    await userEvent
      .setup()
      .click(screen.getByTestId('guide-nearby-hint-dismiss'));
    expect(screen.queryByTestId('guide-nearby-hint')).toBeNull();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);
    expect(screen.queryByTestId('guide-nearby-hint')).toBeNull();
  });

  it('появляется сразу при входе в moving, если POI уже в пределах 120 м', async () => {
    vi.mocked(useServicesAlong).mockReturnValue({
      items: [toiletService],
      state: 'ready',
      measured: null,
      maxOffLineM: null,
      reason: null,
      capped: false,
    });

    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-nearby-hint')).toBeInTheDocument();
    expect(screen.getByTestId('guide-nearby-hint')).toHaveTextContent(
      /туалет/i
    );
  });
});

describe('GuidePanel · wake lock', () => {
  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    vi.restoreAllMocks();
  });

  it('releases the wake lock when the tab goes hidden', async () => {
    const release = vi.fn().mockResolvedValue(undefined);
    let acquireResolve: ((v: { release: () => Promise<void> }) => void) | null =
      null;
    const acquire = vi.fn().mockImplementation(
      () =>
        new Promise<{ release: () => Promise<void> }>((resolve) => {
          acquireResolve = resolve;
        })
    );

    const origAddEventListener = document.addEventListener.bind(document);
    const listeners: Array<() => void> = [];
    vi.spyOn(document, 'addEventListener').mockImplementation(
      (event, handler) => {
        if (event === 'visibilitychange') {
          listeners.push(handler as () => void);
        }
        return origAddEventListener(event, handler as EventListener);
      }
    );

    Object.defineProperty(document, 'visibilityState', {
      value: 'visible',
      writable: true,
      configurable: true,
    });

    vi.stubGlobal('navigator', {
      geolocation: {
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
      wakeLock: { request: acquire },
    });

    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    await act(async () => {
      acquireResolve?.({ release });
    });

    Object.defineProperty(document, 'visibilityState', {
      value: 'hidden',
      configurable: true,
    });
    listeners.forEach((l) => l());

    expect(release).toHaveBeenCalledTimes(1);
  });

  it('re-acquires the wake lock and shows a toast when the tab returns to visible', async () => {
    toastCalls.length = 0;
    const release = vi.fn().mockResolvedValue(undefined);
    let acquireResolve: ((v: { release: () => Promise<void> }) => void) | null =
      null;
    const acquire = vi.fn().mockImplementation(
      () =>
        new Promise<{ release: () => Promise<void> }>((resolve) => {
          acquireResolve = resolve;
        })
    );

    const origAddEventListener = document.addEventListener.bind(document);
    const listeners: Array<() => void> = [];
    vi.spyOn(document, 'addEventListener').mockImplementation(
      (event, handler) => {
        if (event === 'visibilitychange') {
          listeners.push(handler as () => void);
        }
        return origAddEventListener(event, handler as EventListener);
      }
    );

    Object.defineProperty(document, 'visibilityState', {
      value: 'visible',
      writable: true,
      configurable: true,
    });

    vi.stubGlobal('navigator', {
      geolocation: {
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
      wakeLock: { request: acquire },
    });

    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    await act(async () => {
      acquireResolve?.({ release });
    });

    Object.defineProperty(document, 'visibilityState', {
      value: 'hidden',
      configurable: true,
    });
    listeners.forEach((l) => l());

    let acquireResolve2:
      | ((v: { release: () => Promise<void> }) => void)
      | null = null;
    acquire.mockImplementationOnce(
      () =>
        new Promise<{ release: () => Promise<void> }>((resolve) => {
          acquireResolve2 = resolve;
        })
    );

    Object.defineProperty(document, 'visibilityState', {
      value: 'visible',
      configurable: true,
    });
    listeners.forEach((l) => l());
    await act(async () => {
      acquireResolve2?.({ release });
    });

    expect(acquire).toHaveBeenCalledTimes(2);
    expect(release).toHaveBeenCalled();
    expect(toastCalls).toContainEqual([
      'Продолжаем навигацию…',
      { duration: 2000 },
    ]);
  });
});

describe('GuidePanel · фиксы в фоне', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, 'geolocation');
    localStorage.clear();
    seedRoute();
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    vi.restoreAllMocks();
  });

  it('не скачет прогрессом вперёд по возвращении из фона — слабый фикс не тикает остановки', async () => {
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    geo.standNorthOf(STOPS[0]!, 200);

    await waitFor(() =>
      expect(screen.getByTestId('guide-line-progress')).toBeInTheDocument()
    );

    geo.push(STOPS[0]!.lat + 200 / 111_320, STOPS[0]!.lon, 120);

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('не теряет текущую остановку по возвращении из фона — она уже в effectiveVisited', async () => {
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    await waitFor(() =>
      expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument()
    );

    expect(screen.queryByTestId('guide-next-place')).toBeNull();
    expect(screen.getByTestId('guide-advance')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('Кафе Немо')
    );
  });
});

describe('GuidePanel · английский интерфейс', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    Reflect.deleteProperty(navigator, 'geolocation');
    vi.restoreAllMocks();
  });

  it('переводит весь проводник, а не только заголовок', async () => {
    await i18n.changeLanguage('en');
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    expect(screen.getByText('Guide')).toBeInTheDocument();
    expect(screen.queryByText('Проводник')).toBeNull();
    expect(
      screen.getByText('walking the route stop by stop')
    ).toBeInTheDocument();
    expect(screen.getByText('sound on')).toBeInTheDocument();
    expect(screen.getByText('reset')).toBeInTheDocument();

    expect(screen.queryByTestId('guide-next-stop')).toBeNull();
    expect(screen.getByTestId('guide-stop-1')).toHaveTextContent(
      'Монастырь бригиток'
    );

    expect(screen.getByText(/walked 0 of 2/i)).toBeInTheDocument();
    expect(screen.queryByTestId('guide-geo-status')).toBeNull();
    expect(screen.getByTestId('guide-start')).toHaveTextContent(
      'start the route'
    );
  });

  it('объясняет пустой маршрут по-английски', async () => {
    await i18n.changeLanguage('en');
    stubGeolocation(null);
    render(<GuidePanel stops={[]} onExit={noop} />);

    expect(screen.getByTestId('guide-empty')).toHaveTextContent('no route yet');
  });

  it('переносит ряд кнопок шапки, чтобы кнопки не уезжали за край на 390px', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    const header = screen.getByTestId('guide-header');
    expect(header.className).toContain('flex-wrap');
    expect(header.querySelector('.min-w-0')).not.toBeNull();
    const resetButton = screen.getByRole('button', { name: 'сбросить' });
    expect(resetButton.className).toContain('shrink-0');
    expect(screen.queryByText('сбросить прогресс')).toBeNull();
  });
});
