import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ReactNode } from 'react';

import type { Place } from '@/api/types';

import { PlaceMap } from './place-map';

const fitBounds = vi.hoisted(() => vi.fn());
const flyTo = vi.hoisted(() => vi.fn());

interface MarkerProps {
  children?: ReactNode;
  longitude: number;
  latitude: number;
  draggable?: boolean;
  onDragEnd?: (event: { lngLat: { lat: number; lng: number } }) => void;
}

/**
 * The map itself needs WebGL; what is asserted here is the contract both pages
 * lean on — a pin per place, coordinates, which pin is draggable, what a drop
 * reports, and that a click travels back with the place id.
 */
vi.mock('react-map-gl/maplibre', async () => {
  const React = await import('react');
  return {
    Map: React.forwardRef(function MockMap(
      {
        children,
        initialViewState,
      }: {
        children?: ReactNode;
        initialViewState: { longitude: number; latitude: number };
      },
      ref: React.ForwardedRef<unknown>
    ) {
      React.useImperativeHandle(ref, () => ({
        fitBounds,
        flyTo,
        getZoom: () => 10,
      }));
      return (
        <div
          data-testid="map"
          data-lon={initialViewState.longitude}
          data-lat={initialViewState.latitude}
        >
          {children}
        </div>
      );
    }),
    Marker: ({
      children,
      longitude,
      latitude,
      draggable,
      onDragEnd,
    }: MarkerProps) => (
      <div
        data-testid="marker"
        data-lon={longitude}
        data-lat={latitude}
        data-draggable={draggable ? 'true' : 'false'}
      >
        {children}
        <button
          type="button"
          data-testid="marker-drag"
          onClick={() => onDragEnd?.({ lngLat: { lat: 53.9, lng: 23.7 } })}
        />
      </div>
    ),
    NavigationControl: () => null,
  };
});

const place = (id: number, lat: number, lon: number): Place => ({
  place_id: id,
  source_url: `city:${id}`,
  name: `Место ${id}`,
  category: 'памятник',
  town: 'Гродно',
  district: null,
  lat,
  lon,
  visit_minutes: 15,
  opening_hours: null,
  blurb: null,
  fun_fact: null,
  fun_facts: [],
  links: [],
  ticket_price: null,
  photo: null,
});

const PLACES = [place(1, 53.6, 23.8), place(2, 53.7, 23.9)];

beforeEach(() => {
  fitBounds.mockClear();
  flyTo.mockClear();
});

describe('PlaceMap', () => {
  it('draws a pin for every place on the page', () => {
    render(<PlaceMap places={PLACES} selectedId={null} onSelect={() => {}} />);

    expect(screen.getByTestId('place-pin-1')).toBeInTheDocument();
    expect(screen.getByTestId('place-pin-2')).toBeInTheDocument();
    expect(
      screen.getByTestId('place-pin-1').closest('[data-testid="marker"]')
    ).toHaveAttribute('data-lat', '53.6');
  });

  it('reports the clicked pin, so a map click can select a row', () => {
    const onSelect = vi.fn();
    render(<PlaceMap places={PLACES} selectedId={null} onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId('place-pin-2'));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it('frames the whole page on mount', () => {
    render(<PlaceMap places={PLACES} selectedId={null} onSelect={() => {}} />);

    expect(fitBounds).toHaveBeenCalledWith(
      [
        [23.8, 53.6],
        [23.9, 53.7],
      ],
      expect.objectContaining({ maxZoom: 15 })
    );
  });

  it('is view-only until a row is edited: no pin is draggable', () => {
    const onMove = vi.fn();
    render(
      <PlaceMap
        places={PLACES}
        selectedId={1}
        onSelect={() => {}}
        onMove={onMove}
      />
    );

    const marker = screen
      .getByTestId('place-pin-1')
      .closest('[data-testid="marker"]')!;
    expect(marker).toHaveAttribute('data-draggable', 'false');

    fireEvent.click(marker.querySelector('[data-testid="marker-drag"]')!);
    expect(onMove).not.toHaveBeenCalled();
  });

  it('makes the edited pin draggable and reports the drop as (lat, lon)', () => {
    const onMove = vi.fn();
    render(
      <PlaceMap
        places={PLACES}
        selectedId={2}
        editingId={2}
        editLat={53.71}
        editLon={23.91}
        onSelect={() => {}}
        onMove={onMove}
      />
    );

    const marker = screen
      .getByTestId('place-pin-2')
      .closest('[data-testid="marker"]')!;
    expect(marker).toHaveAttribute('data-draggable', 'true');
    // The edited pin follows the draft, not the stored row.
    expect(marker).toHaveAttribute('data-lat', '53.71');

    fireEvent.click(marker.querySelector('[data-testid="marker-drag"]')!);
    expect(onMove).toHaveBeenCalledWith(53.9, 23.7);
  });

  it('centres on the picked row', () => {
    render(<PlaceMap places={PLACES} selectedId={2} onSelect={() => {}} />);

    expect(flyTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [23.9, 53.7] })
    );
  });
});
