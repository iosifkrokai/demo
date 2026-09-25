import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
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
});
