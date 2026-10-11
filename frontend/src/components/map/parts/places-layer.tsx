import { useMemo } from 'react';
import { Source, Layer } from 'react-map-gl/maplibre';
import type { FeatureCollection, Point } from 'geojson';
import type { ExpressionSpecification } from 'maplibre-gl';

import type { Place } from '@/api/types';

/** The full catalogue on the map — every point in the dataset, at once. */

export const PLACES_POINTS_LAYER_ID = 'places-points';

/** Category → colour. */
const CATEGORY_COLORS: Record<string, string> = {
  замок: '#d97706',
  дворец: '#d97706',
  усадьба: '#d97706',
  костёл: '#7c3aed',
  церковь: '#7c3aed',
  храм: '#7c3aed',
  монастырь: '#7c3aed',
  музей: '#2563eb',
  памятник: '#2563eb',
  архитектура: '#2563eb',
  парк: '#16a34a',
  кафе: '#ea580c',
  ресторан: '#ea580c',
  туалет: '#64748b',
  гостиница: '#64748b',
  инфраструктура: '#64748b',
  'остановка транспорта': '#64748b',
  кладбище: '#78716c',
};

const FALLBACK_COLOR = '#64748b';

/** Build a maplibre `match` expression: category → colour, else the fallback. */
const colorExpression = (): ExpressionSpecification => {
  const stops: unknown[] = ['match', ['get', 'category']];
  for (const [category, color] of Object.entries(CATEGORY_COLORS)) {
    stops.push(category, color);
  }
  stops.push(FALLBACK_COLOR);
  return stops as unknown as ExpressionSpecification;
};

export function PlacesLayer({ places }: { places: readonly Place[] }) {
  const data = useMemo<FeatureCollection<Point> | null>(() => {
    if (places.length === 0) return null;
    return {
      type: 'FeatureCollection',
      features: places.map((place) => ({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [place.lon, place.lat],
        },
        properties: {
          placeId: place.place_id,
          name: place.name,
          category: place.category,
          town: place.town,
        },
      })),
    };
  }, [places]);

  if (!data) return null;

  return (
    <Source id="all-places" type="geojson" data={data}>
      <Layer
        id={PLACES_POINTS_LAYER_ID}
        type="circle"
        paint={{
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 16, 7],
          'circle-color': colorExpression(),
          'circle-opacity': 0.9,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#ffffff',
        }}
      />
    </Source>
  );
}
