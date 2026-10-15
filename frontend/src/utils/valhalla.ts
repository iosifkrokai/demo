import type { Profile } from '@/stores/common-store';
import { decode } from './polyline';
import { toast } from 'sonner';
import type {
  ActiveWaypoint,
  ActiveWaypoints,
  ValhallaWarning,
} from '@/components/types';
import i18n from '@/i18n';
import { getBaseUrl } from './base-url';

export const getValhallaUrl = () => getBaseUrl();

/** The costing tune the client's own line is drawn with: only the options that move it. */
const CLIENT_COSTING_OPTIONS: Partial<Record<Profile, Record<string, number>>> =
  {
    pedestrian: {
      step_penalty: 0,
      service_penalty: 15,
      use_tracks: 0,
      use_living_streets: 0.5,
      destination_only_penalty: 300,
    },
    bicycle: {
      cycling_speed: 20,
      use_roads: 0.5,
    },
  };

export const buildDirectionsRequest = ({
  profile,
  activeWaypoints,
  dateTime,
  language,
}: {
  profile: Profile;
  activeWaypoints: ActiveWaypoints;
  dateTime: { type: number; value: string };
  language: string;
}) => {
  let valhalla_profile = profile;

  if (profile === 'car') {
    valhalla_profile = 'auto';
  }

  const req = {
    json: {
      costing: valhalla_profile,
      costing_options: {
        [valhalla_profile]: CLIENT_COSTING_OPTIONS[profile] ?? {},
      },
      exclude_polygons: [],
      locations: makeLocations(activeWaypoints),
      units: 'kilometers',
      alternates: 2,
      id: 'valhalla_directions',
      language,
    },
  };

  if (dateTime.type > -1) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (req.json as any).date_time = dateTime;
  }
  return req;
};

/** Valhalla refuses a request with more than 20 locations. */
export const VALHALLA_MAX_LOCATIONS = 20;

/** Split waypoints into groups of at most `max`, overlapping by one so segments join. */
export const chunkWaypoints = (
  waypoints: ActiveWaypoint[],
  max = VALHALLA_MAX_LOCATIONS
): ActiveWaypoint[][] => {
  if (waypoints.length <= max) {
    return waypoints.length > 0 ? [waypoints] : [];
  }
  const chunks: ActiveWaypoint[][] = [];
  const step = max - 1;
  for (let start = 0; start < waypoints.length - 1; start += step) {
    chunks.push(
      waypoints.slice(start, Math.min(start + max, waypoints.length))
    );
  }
  return chunks;
};

export const parseDirectionsGeometry = (data: {
  trip: { legs: { shape: string }[] };
}) => {
  const coordinates: number[][] = [];

  for (const feat of data.trip.legs) {
    coordinates.push(...decode(feat.shape, 6));
  }

  return coordinates;
};

export const WAYPOINT_SNAP_RADIUS_M = 500;

export const makeLocations = (waypoints: ActiveWaypoint[]) => {
  const locations = [];
  for (const [idx, waypoint] of waypoints.entries()) {
    const type = [0, waypoints.length - 1].includes(idx) ? 'break' : 'via';
    locations.push({
      lon: waypoint.displaylnglat[0],
      lat: waypoint.displaylnglat[1],
      type: type,
      radius: WAYPOINT_SNAP_RADIUS_M,
    });
  }

  return locations;
};

export function showValhallaWarnings(warnings?: ValhallaWarning[]) {
  if (!warnings?.length) return;
  for (const warning of warnings) {
    toast.warning(i18n.t('toast.warningTitle', { code: warning.code }), {
      description: warning.text,
      position: 'bottom-center',
      duration: 5000,
      closeButton: true,
    });
  }
}
