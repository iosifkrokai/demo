import type { GeoLineString } from '@/components/types';

/** The one place GeoJSON's `[lon, lat]` meets the map's `[lat, lon]`. */
export const asMapShape = (
  shape: GeoLineString | null | undefined
): { type: string; coordinates: number[][] } | null => {
  const coordinates = shape?.coordinates;
  if (!coordinates || coordinates.length < 2) return null;
  return {
    type: shape?.type ?? 'LineString',
    coordinates: coordinates.map(([lon, lat]) => [lat ?? 0, lon ?? 0]),
  };
};
