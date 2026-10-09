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

// Module-level mutable array: vi.mock is hoisted so this must be declared before it.
// Only used by the "re-acquires" test; cleared at the start of that test.
const toastCalls: unknown[][] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
vi.mock('sonner', () => ({ toast: (...args: any[]) => toastCalls.push(args) }));

// Mock function for useServicesAlong (hoisted above imports).
vi.mock('@/hooks/use-services-along', () => ({
  useServicesAlong: vi.fn(),
}));

import type { ParsedDirectionsGeometry } from '@/components/types';
import i18n from '@/i18n';
import { useCommonStore } from '@/stores/common-store';
import { useDirectionsStore } from '@/stores/directions-store';

import { installGeoSim, resetSim } from '@/lib/geo-sim';
import { useServicesAlong } from '@/hooks/use-services-along';

import { GuidePanel, guideRouteKey, type GuideStop } from './guide-panel';

// The app's own language is Russian while the specs run (src/test-setup.ts), and
// the English tests below flip it — put it back for the next test either way.
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
    // ~600 m away: standing at stop 1 must not tick stop 2 as well
    lat: 53.6832,
    lon: 23.8365,
    placeId: 102,
    category: 'кафе',
    visitMinutes: 40,
  },
];

/**
 * A route line that runs through both stops, with one turn at the middle
 * vertex. Shaped like the Valhalla response the map already draws
 * (decodedGeometry is [lat, lon] pairs).
 */
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
  // Replace only geolocation: swapping the whole navigator leaks into other
  // test files sharing the worker.
  Object.defineProperty(navigator, 'geolocation', {
    value: { watchPosition, clearWatch: vi.fn() },
    configurable: true,
  });
};

/**
 * A watcher that stays quiet until the test pushes a position — that is how a
 * real phone behaves, and it lets us stand the tourist at an exact distance.
 */
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
    // Reset to idle by default so NearbyHint stays hidden for tests that don't
    // explicitly test it — the component only shows the hint when state === 'ready'.
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
    // The navigator must remember «я здесь был»: otherwise the whole walked
    // route stays «пройдено 0 из N», which is what a recording of the walk showed.
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
    // The tourist then walks out of the stop's radius.
    act(() => {
      push?.({ coords: { latitude: 53.7, longitude: 23.9, accuracy: 8 } });
    });

    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('отмечает остановку, на которой стоит турист, даже если предыдущую он пропустил', () => {
    // A cut corner must not silence the whole route: the loop used to break at
    // the first unvisited stop beyond the radius, and every stop after it stayed
    // unmarked even though the tourist walked right through them.
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
    // The badge is the whole point of the simulation being allowed in a build:
    // a replayed position must never look like something the phone reported.
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

    // A reload must not lose where the tourist got to.
    unmount();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('marks the stop it is standing at once geolocation arrives', async () => {
    // Standing at the next stop on the route (order is kept: a walk marks its
    // current stop, not whichever one happens to be nearby).
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

    // «а добавь кофейню» rebuilt the route: the sidebar remounts the guide with
    // a new route fingerprint, and the walk starts over.
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
    // The controls live in a popover, which renders in a portal outside the
    // list — deliberately, so a scrolling panel cannot clip them.
    fireEvent.click(screen.getByTestId('visit-time-plus'));

    expect(
      screen.getByRole('button', {
        name: /время осмотра: 40 минут, изменить/i,
      })
    ).toBeInTheDocument();
    // 40 (chosen) + 40 (the other stop) = 1 ч 20 мин of visits still ahead.
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~1 ч 20 мин'
    );
  });

  it('updates route progress as GPS advances without adding a duplicate stop card', () => {
    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    // 240 m short of the first stop: close enough to aim at, too far to count.
    geo.standNorthOf(STOPS[0]!, 240);
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
    expect(screen.queryByTestId('guide-next-stop')).toBeNull();

    // Walk the last stretch: within 40 m the stop is done.
    geo.standNorthOf(STOPS[0]!, 20);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-stop-2')).toHaveTextContent('Кафе Немо');
  });

  it('starts in moving mode when a trusted GPS fix is already available', () => {
    // When the tourist already has a good fix (accuracy ≤ 50 m), the guide
    // opens directly into moving mode — no second button required.
    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} onExit={noop} />);

    // The first stubbed fix is already a good one.
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    expect(screen.getByTestId('guide-panel')).toHaveAttribute(
      'data-mode',
      'moving'
    );
    expect(screen.getByTestId('guide-maneuver')).toBeInTheDocument();
    expect(screen.getByTestId('guide-advance')).toBeInTheDocument();
    // No guide-start button: the navigator already started.
    expect(screen.queryByTestId('guide-start')).toBeNull();
  });

  it('falls back to review mode when GPS is not yet available', () => {
    // Without a trusted fix the guide opens in review, where the start button
    // lets the tourist decide when to begin.
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

    // Done: struck through, muted, no longer the current step.
    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(within(first).getByText('Монастырь бригиток')).toHaveClass(
      'line-through'
    );
    expect(first).not.toHaveAttribute('aria-current');
    expect(second).toHaveAttribute('aria-current', 'step');

    // Tapping again undoes it.
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
    // 30 + 40 minutes of looking around are still ahead.
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
    // A walk saved for some other route: the fingerprint does not match, so
    // the guide must not resurrect its ticks.
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
    // The reset itself is persisted, so a reload does not bring the ticks back.
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

    // A 120 m accuracy circle cannot justify «через 30 м».
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 120);

    expect(screen.queryByTestId('guide-maneuver-distance')).toBeNull();
    expect(screen.getByTestId('guide-maneuver-unprecise')).toHaveTextContent(
      /расстояние скрыто/i
    );
    expect(screen.queryByTestId('guide-geo-status')).toBeNull();
    // …and it must not silently complete the stop it is "standing at".
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

    // Before the walk there is no progress, but it is already honestly zero.
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

    // One straggling fix is noise; the prompt waits for the second.
    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    expect(screen.queryByTestId('guide-off-route')).toBeNull();

    geo.push(STOPS[0]!.lat + 0.004, STOPS[0]!.lon, 8);
    expect(screen.getByTestId('guide-off-route')).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-reroute'));
    expect(onReroute).toHaveBeenCalledTimes(1);

    // Every stop survives the re-plan — only the start moved to the fix.
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

    // Place information lives in the explicit route details, not in a second
    // always-visible card competing with the map.
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

    // Suggestions live inside the details panel — open it first.
    await user.click(screen.getByTestId('guide-route-details-toggle'));
    expect(screen.getByTestId('guide-suggestions')).toHaveTextContent(
      'Туалет у ратуши'
    );

    // «добавить» only reports the choice; it must not tick a stop or reorder.
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

    // The line is ~760 m long; nothing walked yet, so it is all remaining.
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

    // The distance is the large number above the instruction.
    const distance = screen.getByTestId('guide-maneuver-distance');
    expect(distance).toHaveTextContent(/\d+ м|\d+[.,]\d км/i);
    expect(distance.className).toContain('text-2xl');
  });

  it('скрывает крупную дистанцию, когда GPS слабый', async () => {
    const geo = stubWatchingGeolocation();
    await start();

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 120);

    // There is no large number; the instruction shows with a weak-signal note.
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
    // 50 m from the start — within NEARBY_HINT_AHEAD_M (120 m) of the start.
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

    // The nearby hint is shown.
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
    // Go straight to moving — no need for guide-start since we're testing the
    // hint itself, not the entry flow.
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    // First appearance.
    expect(screen.getByTestId('guide-nearby-hint')).toBeInTheDocument();

    // Dismiss.
    await userEvent
      .setup()
      .click(screen.getByTestId('guide-nearby-hint-dismiss'));
    expect(screen.queryByTestId('guide-nearby-hint')).toBeNull();

    // A re-render (new fix) — the hint does not come back.
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);
    expect(screen.queryByTestId('guide-nearby-hint')).toBeNull();
  });

  it('появляется сразу при входе в moving, если POI уже в пределах 120 м', async () => {
    // Near the start (50 m in), so it is within NEARBY_HINT_AHEAD_M (120 m).
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
    // Directly push a good fix — this puts the guide into moving mode
    // (trusted fix) AND provides the position that makes nearbyHint computable.
    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    // NearbyHint must appear without any extra interaction.
    expect(screen.getByTestId('guide-nearby-hint')).toBeInTheDocument();
    expect(screen.getByTestId('guide-nearby-hint')).toHaveTextContent(
      /туалет/i
    );
  });
});

describe('GuidePanel · wake lock', () => {
  // Use real timers so that act() flushes microtasks properly without fake-timer
  // interference between the promise chain and the handler.
  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    // restore spies only; the parent's afterEach calls vi.unstubAllGlobals().
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

    // Stub only the properties this test needs; do NOT use vi.stubGlobal here
    // because the parent's afterEach calls vi.unstubAllGlobals().
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

    // Let the acquire promise resolve and populate the ref.
    await act(async () => {
      acquireResolve?.({ release });
    });

    // Tab goes hidden → handler must release the lock.
    Object.defineProperty(document, 'visibilityState', {
      value: 'hidden',
      configurable: true,
    });
    listeners.forEach((l) => l());

    expect(release).toHaveBeenCalledTimes(1);
  });

  it('re-acquires the wake lock and shows a toast when the tab returns to visible', async () => {
    toastCalls.length = 0; // clear from previous runs
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

    // Tab goes hidden.
    Object.defineProperty(document, 'visibilityState', {
      value: 'hidden',
      configurable: true,
    });
    listeners.forEach((l) => l());

    // Second acquire fires on return-to-visible.
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

    // TWO acquisitions, not three: one when the walk started, one on the return
    // to visible. The third the first draft expected came from the effect
    // re-running on the hide (the flag lived in state, so hiding re-created the
    // listener and re-requested a lock already held — two locks to release, one
    // of them dangling). Acquiring on the return is the point; acquiring twice
    // on the hide was a side effect of how the flag was stored.
    expect(acquire).toHaveBeenCalledTimes(2);
    expect(release).toHaveBeenCalled();
    expect(toastCalls).toContainEqual([
      'Продолжаем навигацию…',
      { duration: 2000 },
    ]);
  });
});

describe('GuidePanel · фиксы в фоне', () => {
  // NOTE: geolocation is stubbed inside each test body so that the parent's
  // afterEach (which calls vi.unstubAllGlobals()) does NOT remove our stub.
  beforeEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, 'geolocation');
    localStorage.clear();
    seedRoute();
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    // restore spies only; do NOT call vi.unstubAllGlobals() — the parent's
    // afterEach already calls it, and stubWatchingGeolocation is set inside each
    // test body AFTER that parent's beforeEach, so it survives that parent's
    // unstub and our own restoreAllMocks() is enough cleanup.
    vi.restoreAllMocks();
  });

  it('не скачет прогрессом вперёд по возвращении из фона — слабый фикс не тикает остановки', async () => {
    // A weak fix on its own (without a good one) does not tick a stop — this is
    // the key behaviour: in the background fixes arrive less often and worse, and
    // they must not tick. Scenario: the tourist stands 200 m from the first stop.
    // A weak fix (poor accuracy) — the stop is not ticked. A good fix from the
    // same point — the stop is ticked. A fix inside the stop radius plus a weak
    // fix — already ticked.
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onExit={noop} />);
    await user.click(screen.getByTestId('guide-start'));

    // 200 m north of the first stop: close enough to reach, but outside
    // ARRIVAL_RADIUS_M (40 m) — no fix from here ticks anything.
    geo.standNorthOf(STOPS[0]!, 200);

    await waitFor(() =>
      expect(screen.getByTestId('guide-line-progress')).toBeInTheDocument()
    );

    // A weak fix from the same point: quality = 'poor' → precise = false → the stop is not ticked.
    geo.push(STOPS[0]!.lat + 200 / 111_320, STOPS[0]!.lon, 120);

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('не теряет текущую остановку по возвращении из фона — она уже в effectiveVisited', async () => {
    // The stop is ticked into effectiveVisited by the useEffect on fix + precise.
    // It is already in effectiveVisited, so nextStop is computed from it and on
    // returning to the tab the next stop stays the same one it was.
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

    // Header.
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

    // Progress and the primary action.
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
    // The row wraps, the title block can shrink (min-w-0), the button group does
    // not shrink (shrink-0) — at 390px no button leaves the screen or overlaps its
    // neighbour.
    expect(header.className).toContain('flex-wrap');
    // The title block can shrink so it never pushes the controls out.
    expect(header.querySelector('.min-w-0')).not.toBeNull();
    const resetButton = screen.getByRole('button', { name: 'сбросить' });
    expect(resetButton.className).toContain('shrink-0');
    // A compact label instead of the former «сбросить прогресс».
    expect(screen.queryByText('сбросить прогресс')).toBeNull();
  });
});
