import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { Place } from '@/api/types';

import { PlacesPanel } from './places-panel';

const one = (id: number, name: string): Place => ({
  place_id: id,
  source_url: `city:${id}`,
  name,
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
});

const PLACES = [one(79, 'Землякам погибшим в ВОВ'), one(80, 'Второе место')];

const mutate = vi.hoisted(() => vi.fn());
const refetch = vi.hoisted(() => vi.fn());
const errorOverride = vi.hoisted(() => ({ value: null as Error | null }));

vi.mock('@/hooks/use-admin', () => ({
  useAdminPlaces: () =>
    errorOverride.value
      ? {
          items: [],
          total: 0,
          isLoading: false,
          error: errorOverride.value,
          refetch,
        }
      : {
          items: PLACES,
          total: PLACES.length,
          isLoading: false,
          error: null,
          refetch,
        },
  useUpdatePlace: () => ({ mutate, isPending: false }),
  useDeletePlace: () => ({ mutate: vi.fn(), isPending: false }),
  useCreatePlace: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/components/place-map/place-map', () => ({
  PlaceMap: ({
    places,
    selectedId,
    editingId,
    onSelect,
    onMove,
  }: {
    places: Place[];
    selectedId: number | null;
    editingId?: number | null;
    onSelect: (placeId: number) => void;
    onMove?: (lat: number, lon: number) => void;
  }) => (
    <div
      data-testid="fake-map"
      data-selected-id={selectedId ?? ''}
      data-editing-id={editingId ?? ''}
      data-pin-count={places.length}
    >
      {places.map((place) => (
        <button
          key={place.place_id}
          type="button"
          data-testid={`fake-pin-${place.place_id}`}
          onClick={() => onSelect(place.place_id)}
        />
      ))}
      <button
        type="button"
        data-testid="fake-drag"
        onClick={() => onMove?.(53.9, 23.7)}
      />
    </div>
  ),
}));

beforeEach(() => {
  mutate.mockClear();
  refetch.mockClear();
  errorOverride.value = null;
});

describe('PlacesPanel — list and map are two windows on one page', () => {
  it('shows the map always, with the whole list on it', () => {
    render(<PlacesPanel enabled />);

    expect(screen.getByTestId('fake-map')).toBeInTheDocument();
    expect(screen.getByTestId('fake-map')).toHaveAttribute(
      'data-pin-count',
      '2'
    );
  });

  it('highlights the row when a pin is clicked on the map', () => {
    render(<PlacesPanel enabled />);

    expect(screen.getByTestId('admin-place-80')).toHaveAttribute(
      'data-selected',
      'false'
    );

    fireEvent.click(screen.getByTestId('fake-pin-80'));

    expect(screen.getByTestId('admin-place-80')).toHaveAttribute(
      'data-selected',
      'true'
    );
    expect(screen.getByTestId('admin-selected-name')).toHaveTextContent(
      'Второе место'
    );
  });

  it('points the map at the place whose row is clicked', () => {
    render(<PlacesPanel enabled />);

    fireEvent.click(screen.getByTestId('admin-place-select-79'));

    expect(screen.getByTestId('fake-map')).toHaveAttribute(
      'data-selected-id',
      '79'
    );
  });

  it('writes a dropped pin into the coordinate fields and saves them', () => {
    render(<PlacesPanel enabled />);

    fireEvent.click(screen.getByTestId('admin-place-edit-79'));
    expect(screen.getByTestId('fake-map')).toHaveAttribute(
      'data-editing-id',
      '79'
    );

    const lat = screen.getByTestId('admin-place-lat');
    const lon = screen.getByTestId('admin-place-lon');
    expect(lat).toHaveValue(53.61817);
    expect(lon).toHaveValue(26.17673);

    fireEvent.click(screen.getByTestId('fake-drag'));
    expect(lat).toHaveValue(53.9);
    expect(lon).toHaveValue(23.7);

    fireEvent.click(screen.getByTestId('admin-place-save'));

    expect(mutate).toHaveBeenCalledTimes(1);
    const [args] = mutate.mock.calls[0]!;
    expect(args.placeId).toBe(79);
    expect(args.patch.lat).toBe(53.9);
    expect(args.patch.lon).toBe(23.7);
  });

  it('shows an error with a retry instead of «ничего не найдено»', () => {
    errorOverride.value = new Error('offline');
    render(<PlacesPanel enabled />);

    expect(screen.getByTestId('admin-places-error')).toBeInTheDocument();
    expect(screen.queryByText('ничего не найдено')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('admin-places-retry'));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
