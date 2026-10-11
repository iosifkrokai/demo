import type { Waypoint } from '@/stores/directions-store';
import { ME_WAYPOINT_ID } from '@/stores/directions-store';

interface LngLat {
  0: number;
  1: number;
}

/** The tourist's own position as waypoint 0. */
export const meWaypoint = (
  lat: number,
  lon: number,
  label: string
): Waypoint => {
  const lngLat: [number, number] = [lon, lat];
  return {
    id: ME_WAYPOINT_ID,
    userInput: label,
    geocodeResults: [
      {
        title: label,
        description: label,
        selected: true,
        displaylnglat: lngLat,
        sourcelnglat: lngLat,
        key: 0,
        addressindex: 0,
      },
    ],
  } as Waypoint;
};

/** The position already stored as waypoint 0, if any. */
export const storedMeCoords = (
  waypoints: Waypoint[]
): { lat: number; lon: number } | null => {
  const me = waypoints.find((w) => w.id === ME_WAYPOINT_ID);
  const lngLat = me?.geocodeResults.find(
    (g: { selected?: boolean }) => g.selected
  )?.sourcelnglat as LngLat | undefined;
  if (!lngLat) return null;
  return { lat: lngLat[1], lon: lngLat[0] };
};
