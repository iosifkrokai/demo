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

import type { ParsedDirectionsGeometry } from '@/components/types';
import i18n from '@/i18n';
import { useDirectionsStore } from '@/stores/directions-store';

import { installGeoSim, resetSim } from '@/lib/geo-sim';

import { GuidePanel, guideRouteKey, type GuideStop } from './guide-panel';

// The app's own language is Russian while the specs run (src/test-setup.ts), and
// the English tests below flip it — put it back for the next test either way.
afterEach(async () => {
  await i18n.changeLanguage('ru');
});

// Module-level mutable array: vi.mock is hoisted so this must be declared before it.
// Only used by the "re-acquires" test; cleared at the start of that test.
const toastCalls: unknown[][] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
vi.mock('sonner', () => ({ toast: (...args: any[]) => toastCalls.push(args) }));

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
  });

  afterEach(() => {
    cleanup();
    useDirectionsStore.getState().resetRoute();
    Reflect.deleteProperty(navigator, 'geolocation');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('помнит пройденную остановку и после того, как турист пошёл дальше', () => {
    // Навигатор обязан помнить «я здесь был»: иначе весь пройденный маршрут
    // остаётся «пройдено 0 из N», что и было видно на записи прохода.
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

    render(<GuidePanel stops={STOPS} />);
    act(() => {
      push?.({
        coords: {
          latitude: STOPS[1]!.lat,
          longitude: STOPS[1]!.lon,
          accuracy: 8,
        },
      });
    });
    // Дальше турист уходит из радиуса остановки.
    act(() => {
      push?.({ coords: { latitude: 53.7, longitude: 23.9, accuracy: 8 } });
    });

    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('отмечает остановку, на которой стоит турист, даже если предыдущую он пропустил', () => {
    // Срезанный угол не должен замолчать весь маршрут: раньше цикл выходил на
    // первой же непройденной остановке дальше радиуса, и все следующие
    // оставались неотмеченными, хотя турист шёл прямо по ним.
    stubGeolocation({ latitude: STOPS[1]!.lat, longitude: STOPS[1]!.lon });
    render(<GuidePanel stops={STOPS} />);

    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('explains itself when there is no route yet', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={[]} />);

    expect(
      screen.getByText(/соберите маршрут в режиме планирования/i)
    ).toBeInTheDocument();
  });

  it('не выдаёт симуляцию за настоящий GPS', () => {
    // The badge is the whole point of the simulation being allowed in a build:
    // a replayed position must never look like something the phone reported.
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    expect(screen.queryByTestId('guide-simulated')).not.toBeInTheDocument();
  });

  it('говорит вслух, когда положение проигрывается', () => {
    installGeoSim('?sim=walk&sim-speed=20');
    try {
      stubGeolocation(null);
      render(<GuidePanel stops={STOPS} />);

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
    const { unmount } = render(<GuidePanel stops={STOPS} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    // A reload must not lose where the tourist got to.
    unmount();
    render(<GuidePanel stops={STOPS} />);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
  });

  it('marks the stop it is standing at once geolocation arrives', async () => {
    // Standing at the next stop on the route (order is kept: a walk marks its
    // current stop, not whichever one happens to be nearby).
    stubGeolocation({ latitude: STOPS[0]!.lat, longitude: STOPS[0]!.lon });
    render(<GuidePanel stops={STOPS} />);

    await waitFor(() =>
      expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument()
    );
  });

  it('falls back to tapping when the browser refuses geolocation', async () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    await waitFor(() =>
      expect(
        screen.getByText(/геолокация недоступна — отмечайте остановки вручную/i)
      ).toBeInTheDocument()
    );
  });

  it('starts a fresh walk when the route is rebuilt', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    const { unmount } = render(<GuidePanel stops={STOPS} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    // «а добавь кофейню» rebuilt the route: the sidebar remounts the guide with
    // a new route fingerprint, and the walk starts over.
    const rebuilt = [
      ...STOPS,
      { id: '3', name: 'Туалет', lat: 53.68, lon: 23.83 },
    ];
    unmount();
    render(<GuidePanel key={guideRouteKey(rebuilt)} stops={rebuilt} />);

    expect(screen.getByText(/пройдено 0 из 3/i)).toBeInTheDocument();
  });

  it('offers the next stop in a maps app', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    const link = screen.getByRole('link', { name: /открыть в картах/i });
    expect(link).toHaveAttribute(
      'href',
      `https://www.google.com/maps/search/?api=1&query=${STOPS[0]!.lat},${STOPS[0]!.lon}`
    );
  });

  it('gives the next stop a card with its number, category and visit time', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    const card = screen.getByTestId('guide-next-stop');
    // 1 — the badge is the stop's place on the route, not a list index.
    expect(within(card).getByText('1')).toBeInTheDocument();
    expect(within(card).getByText('Монастырь бригиток')).toBeInTheDocument();
    expect(within(card).getByText('монастырь')).toBeInTheDocument();
    // The visit time is the dataset's estimate, so it is offered as approximate
    // and stays editable: «≈ 30 мин», not a claim about this visit.
    expect(within(card).getByTestId('visit-time-chip')).toHaveTextContent(
      '≈ 30 мин'
    );
  });

  it('lets the tourist set their own time at a stop, and keeps it for the totals', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    const card = screen.getByTestId('guide-next-stop');
    fireEvent.click(within(card).getByTestId('visit-time-chip'));
    // The controls live in a popover, which renders in a portal outside the
    // card — deliberately, so a scrolling panel cannot clip them.
    fireEvent.click(screen.getByTestId('visit-time-plus'));

    // Their number replaces the estimate and drops the «≈».
    expect(within(card).getByTestId('visit-time-chip')).toHaveTextContent(
      '40 мин'
    );
    // 40 (chosen) + 40 (the other stop) = 1 ч 20 мин of visits still ahead.
    expect(screen.getByTestId('guide-minutes-left')).toHaveTextContent(
      'осталось осмотра ~1 ч 20 мин'
    );
  });

  it('shows how far the next stop is and walks the card forward', () => {
    const geo = stubWatchingGeolocation();
    render(<GuidePanel stops={STOPS} />);

    // 240 m short of the first stop: close enough to aim at, too far to count.
    geo.standNorthOf(STOPS[0]!, 240);
    expect(screen.getByTestId('guide-next-distance')).toHaveTextContent(
      'до неё 240 м'
    );
    expect(screen.getByText(/до следующей 240 м/i)).toBeInTheDocument();
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();

    // Walk the last stretch: within 40 m the stop is done and the card moves on.
    geo.standNorthOf(STOPS[0]!, 20);
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();
    const card = screen.getByTestId('guide-next-stop');
    expect(within(card).getByText('Кафе Немо')).toBeInTheDocument();
    expect(within(card).getByText('2')).toBeInTheDocument();
  });

  it('mutes the stops already walked and keeps the next one in the accent', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} />);

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
    render(<GuidePanel stops={STOPS} />);

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
    render(<GuidePanel stops={STOPS} />);

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('resets the walk on request', async () => {
    stubGeolocation(null);
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} />);

    await user.click(screen.getByTestId('guide-stop-1'));
    await user.click(screen.getByTestId('guide-stop-2'));
    expect(screen.getByText(/маршрут пройден/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'сбросить' }));

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-next-stop')).toHaveTextContent(
      'Монастырь бригиток'
    );
    // The reset itself is persisted, so a reload does not bring the ticks back.
    const stored = JSON.parse(
      localStorage.getItem('grodno-guide-progress') ?? '{}'
    ) as { visited: string[] };
    expect(stored.visited).toEqual([]);
  });

  it('keeps the cards calm for anyone who asked for less motion', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    // The slide is behind motion-safe, so prefers-reduced-motion only fades.
    expect(screen.getByTestId('guide-next-stop')).toHaveClass(
      'motion-safe:slide-in-from-bottom-1'
    );
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
    render(<GuidePanel stops={STOPS} />);
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
    expect(screen.getByTestId('guide-geo-status')).toHaveTextContent(
      /GPS неточный/i
    );
    // …and it must not silently complete the stop it is "standing at".
    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('lets the tourist advance stops by hand without geolocation', async () => {
    const geo = stubWatchingGeolocation();
    const user = await start();

    geo.fail();
    await waitFor(() =>
      expect(screen.getByTestId('guide-geo-status')).toHaveTextContent(
        /геолокация недоступна/i
      )
    );

    await user.click(screen.getByTestId('guide-advance'));
    expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument();

    await user.click(screen.getByTestId('guide-advance'));
    expect(screen.getByText(/маршрут пройден/i)).toBeInTheDocument();
  });

  it('сообщает, насколько пройден маршрут, — это забирает история', async () => {
    const onWalked = vi.fn();
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} onWalked={onWalked} />);

    // До начала прогулки прогресса нет, но он уже честно равен нулю.
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

    render(<GuidePanel stops={STOPS} onReroute={onReroute} />);
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

  it('folds the stop list behind a toggle while moving', async () => {
    const geo = stubWatchingGeolocation();
    const user = await start();
    geo.push(STOPS[0]!.lat - 0.0005, STOPS[0]!.lon, 8);

    expect(screen.queryByTestId('guide-stop-1')).toBeNull();
    const toggle = screen.getByTestId('guide-stop-list-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('guide-stop-1')).toHaveTextContent(
      'Монастырь бригиток'
    );
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
      />
    );
    await user.click(screen.getByTestId('guide-start'));

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
    render(<GuidePanel stops={STOPS} />);
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
    render(<GuidePanel stops={STOPS} />);
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
    // Слабый фикс сам по себе (без хорошего) не тикает остановку — это ключевое
    // поведение: в фоне фиксы приходят реже/хуже, и они не должны тикать.
    // Сценарий: турист стоит в 200 м от первой остановки. Слабый фикс
    // (poor accuracy) — остановка не тикается. Хороший фикс из той же точки —
    // остановка тикается. Фикс из радиуса остановки + слабый фикс — уже тикнуто.
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} />);
    await user.click(screen.getByTestId('guide-start'));

    // 200 м к северу от первой остановки: достаточно близко чтобы дойти, но
    // за пределами ARRIVAL_RADIUS_M (40 м) — ни один фикс отсюда не тикнет.
    geo.standNorthOf(STOPS[0]!, 200);

    await waitFor(() =>
      expect(screen.getByTestId('guide-line-progress')).toBeInTheDocument()
    );

    // Слабый фикс из той же точки: quality = 'poor' → precise = false → остановка не тикается.
    geo.push(STOPS[0]!.lat + 200 / 111_320, STOPS[0]!.lon, 120);

    expect(screen.getByText(/пройдено 0 из 2/i)).toBeInTheDocument();
  });

  it('не теряет текущую остановку по возвращении из фона — она уже в effectiveVisited', async () => {
    // Остановка тикается в effectiveVisited через useEffect по fix + precise.
    // Она уже в effectiveVisited, значит nextStop вычисляется по ней и при
    // возвращении вкладки следующей остановкой остаётся та же, что и была.
    const geo = stubWatchingGeolocation();
    const user = userEvent.setup();
    render(<GuidePanel stops={STOPS} />);
    await user.click(screen.getByTestId('guide-start'));

    geo.push(STOPS[0]!.lat, STOPS[0]!.lon, 8);

    await waitFor(() =>
      expect(screen.getByText(/пройдено 1 из 2/i)).toBeInTheDocument()
    );

    const nextCard = screen.getByTestId('guide-next-stop');
    expect(within(nextCard).getByText('Кафе Немо')).toBeInTheDocument();
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
    render(<GuidePanel stops={STOPS} />);

    // Шапка.
    expect(screen.getByText('Guide')).toBeInTheDocument();
    expect(screen.queryByText('Проводник')).toBeNull();
    expect(
      screen.getByText('walking the route stop by stop')
    ).toBeInTheDocument();
    expect(screen.getByText('sound on')).toBeInTheDocument();
    expect(screen.getByText('reset')).toBeInTheDocument();

    // Карточка следующей остановки и её кнопки.
    const card = screen.getByTestId('guide-next-stop');
    expect(card).toHaveTextContent('next stop');
    expect(
      screen.getByRole('link', { name: /open in maps/i })
    ).toBeInTheDocument();

    // Прогресс, статус геолокации и главное действие.
    expect(screen.getByText(/walked 0 of 2/i)).toBeInTheDocument();
    expect(screen.getByTestId('guide-geo-status')).toHaveTextContent(
      'geolocation unavailable — mark the stops by hand'
    );
    expect(screen.getByTestId('guide-start')).toHaveTextContent(
      'start the route'
    );
  });

  it('объясняет пустой маршрут по-английски', async () => {
    await i18n.changeLanguage('en');
    stubGeolocation(null);
    render(<GuidePanel stops={[]} />);

    expect(screen.getByTestId('guide-empty')).toHaveTextContent('no route yet');
  });

  it('переносит ряд кнопок шапки, чтобы кнопки не уезжали за край на 390px', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={STOPS} />);

    const header = screen.getByTestId('guide-header');
    // Ряд переносится, блок заголовка может сжиматься (min-w-0), группа кнопок
    // не сжимается (shrink-0) — при 390px ни одна кнопка не выходит за экран и
    // не накладывается на соседнюю.
    expect(header.className).toContain('flex-wrap');
    // The title block can shrink so it never pushes the controls out.
    expect(header.querySelector('.min-w-0')).not.toBeNull();
    const resetButton = screen.getByRole('button', { name: 'сбросить' });
    expect(resetButton.className).toContain('shrink-0');
    // Компактная подпись вместо прежней «сбросить прогресс».
    expect(screen.queryByText('сбросить прогресс')).toBeNull();
  });
});
