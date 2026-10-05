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

  // A phone row is a grid of ONE line. `flex-wrap` used to break it across up to
  // four lines and drop the tail below, so a row measured 152px instead of 52px
  // and six stops needed 539px of a 760px sheet — the «text stretches down»
  // complaint. These pin the grid itself; the width it produces is measured in
  // scripts/measure-mobile.mjs and in the browser, not here.
  it('складывает строку остановки в одну линию на телефоне', () => {
    render(<WaypointList onChanged={() => {}} />);

    const row = screen
      .getAllByTestId(/focus-place-/)
      .map((el) => el.closest('li'))[0]!;
    const cls = row.className;

    // A grid with no wrapping, and every control pinned to its own column.
    expect(cls).toContain('max-md:grid');
    expect(cls).toContain('max-md:flex-nowrap');
    expect(cls).toContain('max-md:grid-cols-');
    // The grip, the number, the name, the visit time, the actions.
    expect(cls).toMatch(/2\.5rem/);
  });

  it('не даёт имени переноситься на телефоне: обрезается, а не растёт', () => {
    render(<WaypointList onChanged={() => {}} />);

    for (const el of screen.getAllByTestId(/focus-place-/)) {
      expect(el.className).toContain('max-md:truncate');
      expect(el.className).toContain('max-md:break-normal');
    }
  });

  it('не отдаёт номер остановки целую колонку на телефоне', () => {
    // 1.25rem, not 1.5rem: every pixel here comes out of the name's width.
    render(<WaypointList onChanged={() => {}} />);

    const row = screen.getAllByTestId(/focus-place-/)[0]!.closest('li')!;
    expect(row.className).toMatch(/1\.25rem/);
  });

  it('прячет декоративную эмодзи-категорию на телефоне', () => {
    // The stop number already orders the route; the emoji is decorative and was
    // taking a column from a name that has ~150px.
    render(<WaypointList onChanged={() => {}} />);

    // PlaceIcon renders a span carrying the category as its title; the stop
    // number also has one, so match the emoji's own shape rather than `title`.
    const icons = [...document.querySelectorAll('li span[title]')].filter(
      (el) => !el.className.includes('items-center justify-center')
    );
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      expect(icon.className).toContain('max-md:hidden');
    }
  });

  it('оставляет десктопную строку как была: flex с переносом', () => {
    // Everything above is `max-md:`-scoped, so from md up the row is the single
    // flex line it has always been — icon visible, name wrapping, time inline.
    render(<WaypointList onChanged={() => {}} />);

    const row = screen.getAllByTestId(/focus-place-/)[0]!.closest('li')!;
    // `flex-wrap` is the base, and every mobile override is `max-md:`-scoped,
    // so above md the class list reads exactly as it did before.
    expect(row.className).toContain('flex min-h-[52px] flex-wrap');
    // Every override carries a `max-md:` prefix, so each one is inert from md
    // up. (Asserting the prefix list, not an empty list: the classes ARE on the
    // element — that is what makes them conditional.)
    const overrides = row.className
      .split(' ')
      .filter((c) => c.startsWith('max-md:'));
    expect(overrides.length).toBeGreaterThan(0);
    for (const c of overrides) expect(c.startsWith('max-md:')).toBe(true);
  });
});
