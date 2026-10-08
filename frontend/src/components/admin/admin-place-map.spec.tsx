import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { AdminPlaceMap } from './admin-place-map';

/**
 * The map is replaced by plain elements that record the props they were handed:
 * what matters is that the pin sits on the place's coordinates, that only an
 * editable pin is draggable, and that a drag end reports the new position.
 */
interface MarkerProps {
  longitude: number;
  latitude: number;
  draggable?: boolean;
  onDragEnd?: (event: { lngLat: { lat: number; lng: number } }) => void;
}

vi.mock('react-map-gl/maplibre', () => ({
  Map: ({
    children,
    initialViewState,
  }: {
    children?: React.ReactNode;
    initialViewState: { longitude: number; latitude: number };
  }) => (
    <div
      data-testid="map"
      data-lon={initialViewState.longitude}
      data-lat={initialViewState.latitude}
    >
      {children}
    </div>
  ),
  Marker: ({ longitude, latitude, draggable, onDragEnd }: MarkerProps) => (
    <button
      type="button"
      data-testid="marker"
      data-lon={longitude}
      data-lat={latitude}
      data-draggable={draggable ? 'true' : 'false'}
      onClick={() => onDragEnd?.({ lngLat: { lat: 53.7, lng: 23.9 } })}
    />
  ),
  NavigationControl: () => <div data-testid="nav" />,
}));

describe('AdminPlaceMap', () => {
  it('puts the pin on the place coordinates', () => {
    render(
      <AdminPlaceMap
        placeKey="view-7"
        lat={53.6768}
        lon={23.8223}
        label="Старый замок"
      />
    );

    expect(screen.getByTestId('marker')).toHaveAttribute('data-lat', '53.6768');
    expect(screen.getByTestId('marker')).toHaveAttribute('data-lon', '23.8223');
  });

  it('is view-only by default: the pin is not draggable and a drag reports nothing', () => {
    const onMove = vi.fn();
    render(
      <AdminPlaceMap
        placeKey="view-7"
        lat={53.6768}
        lon={23.8223}
        label="Старый замок"
        onMove={onMove}
      />
    );

    const marker = screen.getByTestId('marker');
    expect(marker).toHaveAttribute('data-draggable', 'false');

    fireEvent.click(marker);
    expect(onMove).not.toHaveBeenCalled();
  });

  it('reports the dropped position as (lat, lon) when draggable', () => {
    const onMove = vi.fn();
    render(
      <AdminPlaceMap
        placeKey="edit-7"
        lat={53.6768}
        lon={23.8223}
        label="Старый замок"
        draggable
        onMove={onMove}
      />
    );

    expect(screen.getByTestId('marker')).toHaveAttribute(
      'data-draggable',
      'true'
    );
    fireEvent.click(screen.getByTestId('marker'));

    expect(onMove).toHaveBeenCalledWith(53.7, 23.9);
  });
});
