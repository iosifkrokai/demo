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

// ── Agent route hand-over (spec 002 §7: one route, one source) ──────────────
//
// The backend answers POST /routes/generate with the plan it verified: ordered
// `points`, a GeoJSON `shape` and the `summary` of exactly that line. Before
// this, the map threw that geometry away and asked Valhalla for a second one —
// which can disagree with the stops the backend verified.
//
// Whoever performs the request hands the verified line over here (the sidebar
// does, right after /routes/generate: `setAgentRoute({ shape, summary, costing })`).
// The map then draws that line and nothing else.

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

/**
 * Hand the agent's verified line over to the map, or `null` to clear it (a
 * reset, an undo, a hand-built route).
 */
export function setAgentRoute(route: AgentRoute | null) {
  agentRoute = route ? { ...route, stops: activeStopCoordinates() } : null;
}

/** The agent line, but only while the stops on screen are still the ones it was verified for. */
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

/**
 * The agent's verified line as a route result. `hasVerifiedLine` is false when
 * the agent could not draw one — the stops still show, the line does not, and
 * nothing is substituted for it.
 */
function buildAgentRoute(route: AgentRoute): ProvenancedRoute {
  const decodedGeometry = agentCoordinates(route);
  const hasGeometry = decodedGeometry.length > 1;

  return {
    id: 'agent_route',
    // The agent answers with one ordered line, not alternatives.
    trip: {
      locations: [],
      legs: [],
      summary: {
        ...boundsOf(decodedGeometry),
        has_time_restrictions: false,
        has_toll: false,
        has_highway: false,
        has_ferry: false,
        // The summary of the line that is on screen, in the units the rest of
        // the app expects: kilometres and seconds.
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

    // A plan across a whole region is tens of stops and hundreds of kilometres:
    // walking costing refuses it ("Path distance exceeds the max distance limit",
    // error 154) and the stops would sit on the map with no line. When the route
    // is simply too long for the current profile, draw it for a car instead of
    // showing nothing.
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

      // Append context for route-specific error
      if (retried.error_code === 154) {
        errorMsg += ` for route.`;
      }

      throw new Error(errorMsg);
    }
  }

  const data: ValhallaRouteResponse = await response.json();

  // Parse geometry for main route
  (data as ParsedDirectionsGeometry).decodedGeometry =
    parseDirectionsGeometry(data);

  // Parse geometry for alternates
  data.alternates?.forEach((alternate, i) => {
    if (alternate) {
      (data.alternates![i] as ParsedDirectionsGeometry).decodedGeometry =
        parseDirectionsGeometry(alternate);
    }
  });

  showValhallaWarnings(data.trip.warnings);

  return data as ParsedDirectionsGeometry;
}

/**
 * Draw the route for an arbitrary number of stops. Valhalla caps a single
 * request at 20 locations, so a longer list (a region-wide plan from the agent)
 * is fetched as chained chunks and merged into one response — otherwise the
 * stops would appear on the map with no line between them.
 *
 * That chunking is a client-side concern and applies to routes the tourist
 * builds by hand. An agent route is not re-routed here at all: the line the
 * backend verified is the line that gets drawn.
 */
async function fetchDirections() {
  const waypoints = useDirectionsStore.getState().waypoints;
  const profile = router.state.location.search.profile;
  const { dateTime, settings: rawSettings } = useCommonStore.getState();

  const activeWaypoints = getActiveWaypoints(waypoints);
  if (activeWaypoints.length < 2) {
    return null;
  }

  // The verified agent line wins, whenever one was handed over for exactly
  // these stops. No second geometry, no second costing, no disagreement with
  // the plan the backend checked.
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

  // One merged response with the first chunk's metadata: everything downstream
  // (route features, summary strip, zoom-to-route) keeps working unchanged.
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
    // The merged summary is the sum of the legs that were actually fetched, so
    // it describes the line on screen — including any leg Valhalla refused.
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
      // A single stop can sit on an edge island — a fort in a field, a gated
      // courtyard — and Valhalla then answers 499 ("Could not find candidate
      // edge used for destination label") for the WHOLE request, so the map
      // loses the line entirely. Walking the stops pairwise keeps every leg
      // that does route; the unreachable one simply leaves a gap.
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
        } catch {
          // unreachable on foot: skip this leg, keep the rest of the route
        }
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
          // Nothing to fit the bounds to when the agent route carries no line:
          // leave the map where the tourist put it, with the stops on it.
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
    // For permalink loading, add waypoint if needed
    if (options?.isPermalink) {
      const waypointCount = useDirectionsStore.getState().waypoints.length;
      const missingWaypoints = index + 1 - waypointCount;

      for (let i = 0; i < missingWaypoints; i++) {
        addEmptyWaypointToEnd();
      }
    }

    // Set placeholder immediately
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
