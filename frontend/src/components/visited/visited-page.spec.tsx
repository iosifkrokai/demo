import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ReactNode } from 'react';

import type { VisitedPlace } from '@/api/types';

import { VisitedPage } from './visited-page';

const visited = (id: number, name: string): VisitedPlace => ({
  place_id: id,
  source_url: `city:${id}`,
  name,
  category: 'замок',
  town: 'Гродно',
  district: null,
  lat: 53.67,
  lon: 23.82,
  visit_minutes: 90,
  opening_hours: null,
  blurb: null,
  fun_fact: null,
  fun_facts: [],
  links: [],
  ticket_price: null,
  photo: null,
  visited_at: '2026-10-07T10:00:00Z',
});

const VISITED = [visited(7, 'Старый замок'), visited(8, 'Новый замок')];

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children?: ReactNode }) => <a>{children}</a>,
}));

vi.mock('@/hooks/use-auth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 't@e.co', display_name: null, role: 'user' },
    authenticated: true,
    isAdmin: false,
    isLoading: false,
    refetch: () => {},
  }),
}));

vi.mock('@/hooks/use-visited', () => ({
  useVisited: () => ({ items: VISITED, count: VISITED.length }),
  useToggleVisited: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/hooks/use-places', () => ({
  usePlaces: () => ({ places: [], isLoading: false }),
}));

vi.mock('@/components/place-map/place-map', () => ({
  PlaceMap: ({
    places,
    selectedId,
    onSelect,
  }: {
    places: VisitedPlace[];
    selectedId: number | null;
    onSelect: (placeId: number) => void;
  }) => (
    <div
      data-testid="fake-map"
      data-selected-id={selectedId ?? ''}
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
    </div>
  ),
}));

describe('VisitedPage — the marked places are on a map too', () => {
  it('puts every visited place on the map', () => {
    render(<VisitedPage />);

    expect(screen.getByTestId('fake-map')).toHaveAttribute(
      'data-pin-count',
      '2'
    );
    expect(screen.getByTestId('fake-pin-7')).toBeInTheDocument();
    expect(screen.getByTestId('fake-pin-8')).toBeInTheDocument();
  });

  it('highlights the row whose pin was clicked on the map', () => {
    render(<VisitedPage />);

    expect(screen.getByTestId('visited-item-8')).toHaveAttribute(
      'data-selected',
      'false'
    );

    fireEvent.click(screen.getByTestId('fake-pin-8'));

    expect(screen.getByTestId('visited-item-8')).toHaveAttribute(
      'data-selected',
      'true'
    );
  });

  it('points the map at the row that was clicked in the list', () => {
    render(<VisitedPage />);

    fireEvent.click(screen.getByTestId('visited-select-7'));

    expect(screen.getByTestId('fake-map')).toHaveAttribute(
      'data-selected-id',
      '7'
    );
  });
});
