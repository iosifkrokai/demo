/**
 * A small map of a set of places, shared by the admin «Места» tab and the
 * tourist's «посещённые» page (spec 005 §3).
 *
 * One implementation on purpose: the two pages must not drift apart about which
 * point is where — the same reason the place card body is shared. It is
 * deliberately *not* the app map (`components/map`): that one owns layers, the
 * planner, the guide and half the stores, none of which belong on these pages.
 *
 * A click travels both ways: a row highlights its pin, a pin reports the place
 * so the caller can highlight its row. With `editingId` set, that pin becomes
 * draggable and a drop reports the new coordinates.
 */

import { useEffect, useMemo, useRef } from 'react';
import {
  Map,
  Marker,
  NavigationControl,
  type MapRef,
} from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

import { DEFAULT_CENTER, DEFAULT_MAP_STYLE } from '@/components/map/constants';

/** What the map needs of a place — `Place` and `VisitedPlace` both satisfy it. */
export interface MapPlace {
  place_id: number;
  name: string;
  lat: number;
  lon: number;
}

export interface PlaceMapProps {
  /** Every place to pin — the page frames this set. */
  places: MapPlace[];
  selectedId: number | null;
  onSelect: (placeId: number) => void;
  /** The row being edited: its pin is larger and draggable. */
  editingId?: number | null;
  /** Draft coordinates of the edited pin (fall back to the row's own). */
  editLat?: number;
  editLon?: number;
  onMove?: (lat: number, lon: number) => void;
  /** Height of the map box; the two pages want different room. */
  className?: string;
}

const PIN_SELECTED = '#ff385c';
const PIN_OTHER = '#64748b';
const ZOOM_SELECTED = 16;
const ZOOM_FIT_MAX = 15;

type Bounds = [[number, number], [number, number]];

const boundsOf = (places: MapPlace[]): Bounds | null => {
  if (places.length === 0) return null;
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const place of places) {
    minLon = Math.min(minLon, place.lon);
    maxLon = Math.max(maxLon, place.lon);
    minLat = Math.min(minLat, place.lat);
    maxLat = Math.max(maxLat, place.lat);
  }
  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ];
};

export function PlaceMap({
  places,
  selectedId,
  onSelect,
  editingId = null,
  editLat,
  editLon,
  onMove,
  className = 'h-[22rem]',
}: PlaceMapProps) {
  const mapRef = useRef<MapRef | null>(null);
  const bounds = useMemo(() => boundsOf(places), [places]);

  // Frame whatever is currently on the page — a filter change re-frames the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bounds) return;
    const [[west, south], [east, north]] = bounds;
    if (west === east && south === north) {
      map.flyTo({ center: [west, south], zoom: ZOOM_SELECTED, duration: 0 });
      return;
    }
    map.fitBounds(bounds, { padding: 48, maxZoom: ZOOM_FIT_MAX, duration: 0 });
  }, [bounds]);

  // Picking a row centres its pin. Keyed on the id, so dragging (same id) does
  // not yank the map out from under the finger.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || selectedId == null || editingId != null) return;
    const target = places.find((place) => place.place_id === selectedId);
    if (!target) return;
    map.flyTo({
      center: [target.lon, target.lat],
      zoom: ZOOM_SELECTED,
      duration: 400,
    });
  }, [selectedId, places, editingId]);

  const initialViewState = useMemo(() => {
    const first = places[0];
    return {
      longitude: first?.lon ?? DEFAULT_CENTER[0],
      latitude: first?.lat ?? DEFAULT_CENTER[1],
      zoom: ZOOM_FIT_MAX,
    };
  }, [places]);

  return (
    <div
      data-testid="place-map"
      className={`relative w-full overflow-hidden rounded-2xl border border-border bg-muted ${className}`}
    >
      <Map
        ref={mapRef}
        initialViewState={initialViewState}
        mapStyle={DEFAULT_MAP_STYLE}
        style={{ width: '100%', height: '100%' }}
        attributionControl={false}
        // This map is embedded in a normally-scrolling page (the «посещённые»
        // page and the admin «Места» tab). Without this a wheel/trackpad scroll
        // or a one-finger swipe over it zoomed/panned the map instead of
        // scrolling the page past it. Cooperative gestures reserve one-finger
        // scroll for the page and require ctrl/⌘ (desktop) or two fingers
        // (touch) to move the map.
        cooperativeGestures
      >
        <NavigationControl position="top-right" showCompass={false} />
        {places.map((place) => {
          const isSelected = place.place_id === selectedId;
          const isEditing = editingId === place.place_id;
          const lat = isEditing && editLat != null ? editLat : place.lat;
          const lon = isEditing && editLon != null ? editLon : place.lon;
          return (
            <Marker
              key={place.place_id}
              longitude={lon}
              latitude={lat}
              anchor="bottom"
              draggable={isEditing}
              onDragEnd={
                isEditing
                  ? (event) => onMove?.(event.lngLat.lat, event.lngLat.lng)
                  : undefined
              }
            >
              <button
                type="button"
                data-testid={`place-pin-${place.place_id}`}
                data-selected={isSelected ? 'true' : 'false'}
                title={place.name}
                aria-label={place.name}
                onClick={() => onSelect(place.place_id)}
                style={{
                  backgroundColor: isSelected ? PIN_SELECTED : PIN_OTHER,
                }}
                className={`block rounded-full border-2 border-white shadow-md transition-transform ${
                  isSelected ? 'size-4 scale-125' : 'size-3'
                } ${isEditing ? 'cursor-grab ring-2 ring-primary/60' : ''}`}
              />
            </Marker>
          );
        })}
      </Map>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-card/90 px-2 py-1 text-meta shadow-card">
        {places.length === 0
          ? 'ничего не найдено'
          : `точек на карте: ${places.length}`}
        {editingId != null && (
          <span className="text-muted-foreground"> · перетащите маркер</span>
        )}
      </div>
    </div>
  );
}
