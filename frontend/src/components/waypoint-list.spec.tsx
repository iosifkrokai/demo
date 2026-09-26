import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { PlaceDetails, Waypoint } from '@/stores/directions-store';

import { WaypointList, plannerVisitKey } from './waypoint-list';

const mockRefetch = vi.fn();
const mockSetWaypoint = vi.fn();

/**
 * Two agent stops with their dataset estimates. The list reads them from the
 * store; the numbers here are the ones the editor must show as approximate.
 */
const mockWaypoints: Waypoint[] = [
  {
    id: '0',
    userInput: 'Монастырь бригиток',
    placeId: 1,
    geocodeResults: [
      {
        title: 'Монастырь бригиток',
        selected: true,
        displaylnglat: [23.8, 53.9],
        sourcelnglat: [23.8, 53.9],
        key: 0,
        addressindex: 0,
      },
    ],
  },
  {
    id: '1',
    userInput: 'Кафе Немо',
    placeId: 2,
    geocodeResults: [
      {
        title: 'Кафе Немо',
        selected: true,
        displaylnglat: [23.81, 53.91],
        sourcelnglat: [23.81, 53.91],
        key: 1,
        addressindex: 1,
      },
    ],
  },
];

const mockPlaceDetails: Record<number, PlaceDetails> = {
  1: {
    name: 'Монастырь бригиток',
    category: 'монастырь',
    blurb: null,
    funFact: null,
    funFacts: [],
    links: [],
    visitMinutes: 40,
  },
  2: {
    name: 'Кафе Немо',
    category: 'кафе',
    blurb: null,
    funFact: null,
    funFacts: [],
    links: [],
    visitMinutes: 30,
  },
};

const STORAGE_KEY = 'grodno-guide-visit-minutes';

vi.mock('@/stores/directions-store', () => ({
  ME_WAYPOINT_ID: 'me',
  useDirectionsStore: (selector: (state: unknown) => unknown) =>
    selector({
      waypoints: mockWaypoints,
      placeDetails: mockPlaceDetails,
      setWaypoint: mockSetWaypoint,
      doRemoveWaypoint: vi.fn(),
      excludeStops: vi.fn(),
    }),
}));

vi.mock('@/hooks/use-directions-queries', () => ({
  useDirectionsQuery: () => ({ refetch: mockRefetch }),
}));

const readStored = () =>
  JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as {
    route?: string;
    overrides?: Record<string, number>;
  };

const renderList = () => render(<WaypointList onChanged={vi.fn()} />);

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe('WaypointList — the tourist sets their own visit time', () => {
  it('presents the dataset estimate as approximate, not as this person’s time', () => {
    renderList();

    const chip = screen.getAllByTestId('visit-time-chip')[0];
    expect(chip).toHaveTextContent('≈ 40 мин');
    expect(chip).toHaveAccessibleName(/примерно 40 минут/);
  });

  it('remembers a changed time and keeps it across a reload', async () => {
    const user = userEvent.setup();
    renderList();

    await user.click(screen.getAllByTestId('visit-time-chip')[0]!);
    await user.click(screen.getAllByTestId('visit-time-plus')[0]!);

    // The tourist's own number is now the one on screen, without the «≈».
    const chip = screen.getAllByTestId('visit-time-chip')[0]!;
    expect(chip).toHaveTextContent('50 мин');
    expect(chip).not.toHaveTextContent('≈');

    // …and it is in storage, under this route, so a reload finds it again.
    const stored = readStored();
    expect(stored.route).toBe(plannerVisitKey(mockWaypoints));
    expect(stored.overrides).toEqual({ '1': 50 });

    cleanup();
    renderList();
    expect(screen.getAllByTestId('visit-time-chip')[0]!).toHaveTextContent(
      '50 мин'
    );
  });

  it('hands the estimate back on reset', async () => {
    const user = userEvent.setup();
    renderList();

    await user.click(screen.getAllByTestId('visit-time-chip')[0]!);
    await user.click(screen.getAllByTestId('visit-time-plus')[0]!);
    expect(screen.getAllByTestId('visit-time-chip')[0]!).toHaveTextContent(
      '50 мин'
    );

    await user.click(screen.getAllByTestId('visit-time-reset')[0]!);

    const chip = screen.getAllByTestId('visit-time-chip')[0]!;
    expect(chip).toHaveTextContent('≈ 40 мин');
    expect(readStored().overrides).toEqual({});
  });

  it('keeps a route’s times together no matter the stop order', () => {
    const reversed = [...mockWaypoints].reverse();

    expect(plannerVisitKey(reversed)).toBe(plannerVisitKey(mockWaypoints));
  });
});
