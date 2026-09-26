import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { GuidePanel, guideRouteKey, type GuideStop } from './guide-panel';

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
  const watchPosition = vi.fn((ok: (p: unknown) => void) => {
    listeners.push(ok);
    return 1;
  });
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
  };
};

describe('GuidePanel', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(navigator, 'geolocation');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('explains itself when there is no route yet', () => {
    stubGeolocation(null);
    render(<GuidePanel stops={[]} />);

    expect(
      screen.getByText(/соберите маршрут в режиме планирования/i)
    ).toBeInTheDocument();
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
    expect(within(card).getByText(/осмотр 30 мин/i)).toBeInTheDocument();
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

    await user.click(
      screen.getByRole('button', { name: /сбросить прогресс/i })
    );

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
