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
import { router } from '@/routes';

const getActiveWaypoints = (waypoints: Waypoint[]): ActiveWaypoint[] =>
  waypoints.flatMap((wp) => wp.geocodeResults.filter((r) => r.selected));

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
 */
async function fetchDirections() {
  const waypoints = useDirectionsStore.getState().waypoints;
  const profile = router.state.location.search.profile;
  const { dateTime, settings: rawSettings } = useCommonStore.getState();

  const activeWaypoints = getActiveWaypoints(waypoints);
  if (activeWaypoints.length < 2) {
    return null;
  }

  const language = getDirectionsLanguage();
  const currentProfile = (profile || 'bicycle') as Profile;
  const chunks = chunkWaypoints(activeWaypoints);

  if (chunks.length === 1) {
    return await requestRoute(
      activeWaypoints,
      currentProfile,
      rawSettings,
      dateTime,
      language
    );
  }

  const parts = [];
  for (const chunk of chunks) {
    parts.push(
      await requestRoute(chunk, currentProfile, rawSettings, dateTime, language)
    );
  }

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

  // One merged response with the first chunk's metadata: everything downstream
  // (route features, summary strip, zoom-to-route) keeps working unchanged.
  return {
    ...parts[0],
    trip: {
      ...parts[0]!.trip,
      legs,
      summary: { ...parts[0]!.trip.summary, length, time },
      warnings: [],
    },
    decodedGeometry,
  } as unknown as ParsedDirectionsGeometry;
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
          zoomTo(data.decodedGeometry);
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
