import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { Place } from '@/api/types';

import { PlacesPanel } from './places-panel';

const PLACE: Place = {
  place_id: 79,
  source_url: 'city:wwii-memorial',
  name: 'Землякам погибшим в ВОВ',
  category: 'памятник',
  town: 'Гродно',
  district: null,
  lat: 53.61817,
  lon: 26.17673,
  visit_minutes: 15,
  opening_hours: null,
  blurb: null,
  fun_fact: null,
  fun_facts: [],
  links: [],
  ticket_price: null,
  photo: null,
};

const mutate = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/use-admin', () => ({
  useAdminPlaces: () => ({ items: [PLACE], total: 1, isLoading: false }),
  useUpdatePlace: () => ({ mutate, isPending: false }),
  useDeletePlace: () => ({ mutate: vi.fn(), isPending: false }),
  useCreatePlace: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The real map needs WebGL; what this test is about is the panel's half of the
// contract — that a dropped pin's coordinates land in the form and in the PATCH.
vi.mock('./admin-place-map', () => ({
  AdminPlaceMap: ({
    draggable,
    onMove,
  }: {
    draggable?: boolean;
    onMove?: (lat: number, lon: number) => void;
  }) => (
    <button
      type="button"
      data-testid="fake-pin"
      data-draggable={draggable ? 'true' : 'false'}
      onClick={() => onMove?.(53.9, 23.7)}
    />
  ),
}));

beforeEach(() => {
  mutate.mockClear();
});

describe('PlacesPanel — moving a point', () => {
  it('writes a dropped pin into the coordinate fields and saves them', () => {
    render(<PlacesPanel enabled />);

    fireEvent.click(screen.getByTestId('admin-place-edit-79'));

    const lat = screen.getByTestId('admin-place-lat');
    const lon = screen.getByTestId('admin-place-lon');
    expect(lat).toHaveValue(53.61817);
    expect(lon).toHaveValue(26.17673);
    expect(screen.getByTestId('fake-pin')).toHaveAttribute(
      'data-draggable',
      'true'
    );

    fireEvent.click(screen.getByTestId('fake-pin'));
    expect(lat).toHaveValue(53.9);
    expect(lon).toHaveValue(23.7);

    fireEvent.click(screen.getByTestId('admin-place-save'));

    expect(mutate).toHaveBeenCalledTimes(1);
    const [args] = mutate.mock.calls[0]!;
    expect(args.placeId).toBe(79);
    expect(args.patch.lat).toBe(53.9);
    expect(args.patch.lon).toBe(23.7);
  });
});
