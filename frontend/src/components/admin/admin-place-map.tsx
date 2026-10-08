/**
 * A small self-contained map for the admin places panel (spec 005 §3).
 *
 * It is deliberately *not* the app map: that one owns layers, the planner, the
 * guide and half the stores, and mounting it inside a settings page would drag
 * all of that along. This is the minimum that answers «где эта точка и туда ли
 * она стоит»: the point, and a pin you can drag to correct it.
 *
 * The `Map` is keyed by `placeKey`, so selecting another place re-centres, while
 * dragging the same place does not remount anything (the pin just follows the
 * coordinates it is given).
 */

import { Map, Marker, NavigationControl } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

import { DEFAULT_MAP_STYLE } from '@/components/map/constants';

export interface AdminPlaceMapProps {
  /** Re-centres the map when this changes — the selected/edited place, not coords. */
  placeKey: string | number;
  lat: number;
  lon: number;
  label: string;
  /** Only an editable pin is draggable; a viewed one is not. */
  draggable?: boolean;
  onMove?: (lat: number, lon: number) => void;
}

const PIN_COLOR = '#ff385c';
const ZOOM = 16;

export function AdminPlaceMap({
  placeKey,
  lat,
  lon,
  label,
  draggable = false,
  onMove,
}: AdminPlaceMapProps) {
  return (
    <div
      data-testid="admin-place-map"
      data-draggable={draggable ? 'true' : 'false'}
      className="relative h-[20rem] w-full overflow-hidden rounded-2xl border border-border bg-muted"
    >
      <Map
        key={placeKey}
        initialViewState={{ longitude: lon, latitude: lat, zoom: ZOOM }}
        mapStyle={DEFAULT_MAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        attributionControl={false}
      >
        <NavigationControl position="top-right" showCompass={false} />
        <Marker
          longitude={lon}
          latitude={lat}
          anchor="center"
          color={PIN_COLOR}
          draggable={draggable}
          onDragEnd={
            draggable
              ? (event) => onMove?.(event.lngLat.lat, event.lngLat.lng)
              : undefined
          }
        />
      </Map>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-card/90 px-2 py-1 text-meta shadow-card">
        {label}
        {draggable && (
          <span className="text-muted-foreground"> · перетащите маркер</span>
        )}
      </div>
    </div>
  );
}
