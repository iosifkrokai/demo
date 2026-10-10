import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';

import type {
  ActiveWaypoint,
  ParsedDirectionsGeometry,
  PossibleSettings,
  ValhallaRouteResponse,
} from '@/components/types';
import {
  getValhallaUrl,
  buildDirectionsRequest,
  chunkWaypoints,
  parseDirectionsGeometry,
  showValhallaWarnings,
  VALHALLA_CLIENT_HEADERS,
} from '@/utils/valhalla';
import { forward_geocode, parseGeocodeResponse } from '@/utils/nominatim';
import { filterProfileSettings } from '@/utils/filter-profile-settings';
import { getDirectionsLanguage } from '@/utils/directions-language';
import { useCommonStore, type Profile } from '@/stores/common-store';
import { useDirectionsStore, type Waypoint } from '@/stores/directions-store';
import {
  hasUsableLine,
  type ProvenancedRoute,
} from '@/components/map/parts/route-lines';
import { router } from '@/routes';

const getActiveWaypoints = (waypoints: Waypoint[]): ActiveWaypoint[] =>
  waypoints.flatMap((wp) => wp.geocodeResults.filter((r) => r.selected));

export interface AgentRoute {
  /** `shape` from the agent: `{ type: 'LineString', coordinates: [[lat, lon], …] }`. */
  shape?: { type?: string; coordinates?: number[][] } | null;
  /** `summary` from the agent — the length/time of the verified line itself. */
  summary?: { length_km?: number | null; time_seconds?: number | null } | null;
  costing?: string | null;
}

interface RegisteredAgentRoute extends AgentRoute {
  /** The stops the line was verified for, so a stale hand-over cannot be used. */
  stops: [number, number][];
}

let agentRoute: RegisteredAgentRoute | null = null;

const activeStopCoordinates = (): [number, number][] =>
  getActiveWaypoints(useDirectionsStore.getState().waypoints).map(
    (a) => a.displaylnglat
  );

/** Publish the agent's verified line to the map, or `null` to clear it. */
export function setAgentRoute(route: AgentRoute | null) {
  agentRoute = route ? { ...route, stops: activeStopCoordinates() } : null;
}

/** The agent line, but only while the on-screen stops match those it was verified for. */
const verifiedAgentRoute = (
  activeWaypoints: ActiveWaypoint[]
): AgentRoute | null => {
  if (!agentRoute) return null;
  const current = activeWaypoints.map((a) => a.displaylnglat);
  if (current.length !== agentRoute.stops.length) return null;
  const sameStops = current.every(([lng, lat], i) => {
    const [agentLng, agentLat] = agentRoute!.stops[i]!;
    return Math.abs(lng - agentLng) < 1e-6 && Math.abs(lat - agentLat) < 1e-6;
  });
  return sameStops ? agentRoute : null;
};

const agentCoordinates = (route: AgentRoute): number[][] =>
  (route.shape?.coordinates ?? []).filter(
    (c) =>
      Array.isArray(c) &&
      c.length >= 2 &&
      Number.isFinite(c[0]) &&
      Number.isFinite(c[1])
  );

const boundsOf = (coordinates: number[][]) => {
  const bounds = { min_lat: 0, min_lon: 0, max_lat: 0, max_lon: 0 };
  for (const [lat = 0, lon = 0] of coordinates) {
    bounds.min_lat = Math.min(bounds.min_lat, lat);
    bounds.max_lat = Math.max(bounds.max_lat, lat);
    bounds.min_lon = Math.min(bounds.min_lon, lon);
    bounds.max_lon = Math.max(bounds.max_lon, lon);
  }
  return bounds;
};

/** The agent's verified line as a route result. */
function buildAgentRoute(route: AgentRoute): ProvenancedRoute {
  const decodedGeometry = agentCoordinates(route);
  const hasGeometry = decodedGeometry.length > 1;

  return {
    id: 'agent_route',
    trip: {
      locations: [],
      legs: [],
      summary: {
        ...boundsOf(decodedGeometry),
        has_time_restrictions: false,
        has_toll: false,
        has_highway: false,
        has_ferry: false,
        length: route.summary?.length_km ?? 0,
        time: route.summary?.time_seconds ?? 0,
        cost: 0,
      },
      status: hasGeometry ? 0 : -1,
      status_message: hasGeometry ? 'ok' : 'no verified geometry',
      units: 'km',
      language: 'ru',
    },
    decodedGeometry,
    source: 'agent',
    hasVerifiedLine: hasGeometry,
  };
}

/** A line this app asked Valhalla for: only ever for a hand-built route. */
const asClientRoute = (data: ParsedDirectionsGeometry): ProvenancedRoute => ({
  ...data,
  source: 'client',
  hasVerifiedLine: true,
});

async function requestRoute(
  activeWaypoints: ActiveWaypoint[],
  profile: Profile,
  rawSettings: PossibleSettings,
  dateTime: { type: number; value: string },
  language: string
): Promise<ParsedDirectionsGeometry> {
  const settings = filterProfileSettings(profile || 'bicycle', rawSettings);

  const valhallaRequest = buildDirectionsRequest({
    profile: profile || 'bicycle',
    activeWaypoints,
    // @ts-expect-error todo: initial settings and filtered settings types mismatch
    settings,
    dateTime,
    language,
  });
  const send = (json: unknown) =>
    fetch(
      `${getValhallaUrl()}/route?${new URLSearchParams({
        json: JSON.stringify(json),
      })}`,
      {
        headers: {
          'Content-Type': 'application/json',
          ...VALHALLA_CLIENT_HEADERS,
        },
      }
    );

  let response = await send(valhallaRequest.json);

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));

    const tooLongForProfile = errorData.error_code === 154;
    const isDriving = valhallaRequest.json.costing === 'auto';
    if (tooLongForProfile && !isDriving) {
      response = await send({
        ...valhallaRequest.json,
        costing: 'auto',
        costing_options: { auto: {} },
      });
      if (response.ok) {
        toast.info('Длинный маршрут', {
          description: 'Показан автомобильный вариант: пешком такое не пройти.',
          position: 'bottom-center',
          closeButton: true,
        });
      }
    }

    if (!response.ok) {
      const retried = await response.json().catch(() => ({}));
      let errorMsg =
        retried.error || errorData.error || 'Could not fetch resource';

      if (retried.error_code === 154) {
        errorMsg += ` for route.`;
      }

      throw new Error(errorMsg);
    }
  }

  const data: ValhallaRouteResponse = await response.json();

  (data as ParsedDirectionsGeometry).decodedGeometry =
    parseDirectionsGeometry(data);

  data.alternates?.forEach((alternate, i) => {
    if (alternate) {
      (data.alternates![i] as ParsedDirectionsGeometry).decodedGeometry =
        parseDirectionsGeometry(alternate);
    }
  });

  showValhallaWarnings(data.trip.warnings);

  return data as ParsedDirectionsGeometry;
}

/** Draw the route for an arbitrary number of stops. */
async function fetchDirections() {
  const waypoints = useDirectionsStore.getState().waypoints;
  const profile = router.state.location.search.profile;
  const { dateTime, settings: rawSettings } = useCommonStore.getState();

  const activeWaypoints = getActiveWaypoints(waypoints);
  if (activeWaypoints.length < 2) {
    return null;
  }

  const agentRoute = verifiedAgentRoute(activeWaypoints);
  if (agentRoute) {
    const agentResult = buildAgentRoute(agentRoute);
    if (!agentResult.hasVerifiedLine) {
      toast.warning('Нет проверенной линии', {
        description:
          'Агент вернул остановки без геометрии — показываем точки без линии.',
        position: 'bottom-center',
        closeButton: true,
      });
    }
    return agentResult;
  }

  const language = getDirectionsLanguage();
  const currentProfile = (profile || 'bicycle') as Profile;
  const chunks = chunkWaypoints(activeWaypoints);

  const mergeParts = (parts: ParsedDirectionsGeometry[]) => {
    const legs: { shape: string }[] = [];
    const decodedGeometry: number[][] = [];
    let length = 0;
    let time = 0;
    for (const part of parts) {
      legs.push(...part.trip.legs);
      decodedGeometry.push(...parseDirectionsGeometry(part));
      length += part.trip.summary.length;
      time += part.trip.summary.time;
    }
    return {
      ...parts[0],
      trip: {
        ...parts[0]!.trip,
        legs,
        summary: { ...parts[0]!.trip.summary, length, time },
        warnings: [],
      },
      decodedGeometry,
      source: 'client',
      hasVerifiedLine: true,
    } as unknown as ProvenancedRoute;
  };

  if (chunks.length === 1) {
    try {
      return asClientRoute(
        await requestRoute(
          activeWaypoints,
          currentProfile,
          rawSettings,
          dateTime,
          language
        )
      );
    } catch (error) {
      const legs: ParsedDirectionsGeometry[] = [];
      for (let i = 0; i < activeWaypoints.length - 1; i++) {
        try {
          legs.push(
            await requestRoute(
              [activeWaypoints[i]!, activeWaypoints[i + 1]!],
              currentProfile,
              rawSettings,
              dateTime,
              language
            )
          );
        } catch {}
      }
      if (!legs.length) throw error;
      return mergeParts(legs);
    }
  }

  const parts = [];
  for (const chunk of chunks) {
    parts.push(
      await requestRoute(chunk, currentProfile, rawSettings, dateTime, language)
    );
  }

  return mergeParts(parts);
}

export function useDirectionsQuery() {
  const showLoading = useCommonStore((state) => state.showLoading);
  const zoomTo = useCommonStore((state) => state.zoomTo);
  const receiveRouteResults = useDirectionsStore(
    (state) => state.receiveRouteResults
  );
  const clearRoutes = useDirectionsStore((state) => state.clearRoutes);

  return useQuery({
    queryKey: ['directions'],
    queryFn: async () => {
      showLoading(true);
      try {
        const data = await fetchDirections();
        if (data) {
          receiveRouteResults({ data });
          if (hasUsableLine(data)) {
            zoomTo(data.decodedGeometry);
          }
        }
        return data;
      } catch (error) {
        clearRoutes();
        if (error instanceof Error) {
          toast.warning('Error', {
            description: error.message,
            position: 'bottom-center',
            duration: 5000,
            closeButton: true,
          });
        }
        throw error;
      } finally {
        setTimeout(() => showLoading(false), 500);
      }
    },
    enabled: false,
    retry: false,
  });
}

export function useSetWaypointFromCoords() {
  const receiveGeocodeResults = useDirectionsStore(
    (state) => state.receiveGeocodeResults
  );
  const updateTextInput = useDirectionsStore((state) => state.updateTextInput);
  const addEmptyWaypointToEnd = useDirectionsStore(
    (state) => state.addEmptyWaypointToEnd
  );
  const updatePlaceholderAddressAtIndex = useDirectionsStore(
    (state) => state.updatePlaceholderAddressAtIndex
  );

  const setWaypointFromCoords = async (
    lng: number,
    lat: number,
    index: number,
    options?: { isPermalink?: boolean }
  ) => {
    if (options?.isPermalink) {
      const waypointCount = useDirectionsStore.getState().waypoints.length;
      const missingWaypoints = index + 1 - waypointCount;

      for (let i = 0; i < missingWaypoints; i++) {
        addEmptyWaypointToEnd();
      }
    }

    updatePlaceholderAddressAtIndex(index, lng, lat);

    const lngLat: [number, number] = [lng, lat];
    const address: ActiveWaypoint = {
      title: `${lng.toFixed(6)}, ${lat.toFixed(6)}`,
      key: 0,
      selected: true,
      addresslnglat: lngLat,
      sourcelnglat: lngLat,
      displaylnglat: lngLat,
      addressindex: 0,
    };
    const addresses = [address];
    receiveGeocodeResults({ addresses, index });
    updateTextInput({
      inputValue: address.title,
      index,
      addressindex: 0,
    });
    return addresses;
  };

  return { setWaypointFromCoords };
}

async function fetchForwardGeocode(
  userInput: string,
  lngLat?: [number, number]
): Promise<ActiveWaypoint[]> {
  if (lngLat) {
    return [
      {
        title: lngLat.toString(),
        key: 0,
        selected: false,
        addresslnglat: lngLat,
        sourcelnglat: lngLat,
        displaylnglat: lngLat,
        addressindex: 0,
      },
    ];
  }

  const response = await forward_geocode(userInput);
  const addresses = parseGeocodeResponse(response.data);

  if (addresses.length === 0) {
    toast.warning('No addresses', {
      description: 'Sorry, no addresses can be found.',
      position: 'bottom-center',
      duration: 5000,
      closeButton: true,
    });
  }

  return addresses as ActiveWaypoint[];
}

export function useForwardGeocodeDirections() {
  const receiveGeocodeResults = useDirectionsStore(
    (state) => state.receiveGeocodeResults
  );

  const forwardGeocode = async (
    userInput: string,
    index: number,
    lngLat?: [number, number]
  ) => {
    try {
      const addresses = await fetchForwardGeocode(userInput, lngLat);
      receiveGeocodeResults({
        addresses,
        index,
      });
      return addresses;
    } catch (error) {
      console.error('Forward geocode error:', error);
      throw error;
    }
  };

  return { forwardGeocode };
}
