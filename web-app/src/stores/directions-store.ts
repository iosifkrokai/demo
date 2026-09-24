import type {
  ActiveWaypoint,
  ParsedDirectionsGeometry,
} from '@/components/types';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

export interface Waypoint {
  id: string;
  geocodeResults: ActiveWaypoint[];
  userInput: string;
  // Set when the point came from the agent (or from a saved route): lets the
  // map look the place up in `placeDetails` to render its blurb / fun fact.
  placeId?: number;
}

interface HighlightSegment {
  startIndex: number;
  endIndex: number;
  alternate: number;
}

interface ZoomObj {
  index: number;
  timeNow: number;
}

interface RouteResult {
  data: ParsedDirectionsGeometry | null;
  show: Record<string, boolean>;
}

interface InclineDeclineTotal {
  [key: string]: unknown;
}

interface LatLng {
  lng: number;
  lat: number;
}

// Route history entry
export interface RouteHistoryEntry {
  id: string;
  query: string;
  timeBudget: number;
  places: RouteHistoryPlace[];
  createdAt: number; // timestamp
}

export interface PlaceLink {
  title: string;
  url: string;
}

export interface RouteHistoryPlace {
  id: number;
  name: string;
  category: string | null;
  lat: number;
  lon: number;
  blurb?: string | null;
  funFact?: string | null;
  funFacts?: string[];
  links?: PlaceLink[];
  visitMinutes?: number | null;
}

// What the agent knows about a place, keyed by the DB `places.id`. Populated
// from /routes/generate and reused when a route is restored from history.
export interface PlaceDetails {
  name: string;
  category: string | null;
  blurb: string | null;
  funFact: string | null;
  funFacts: string[];
  links: PlaceLink[];
  visitMinutes: number | null;
}

const STORAGE_KEY = 'grodno-route-history';
const MAX_HISTORY = 10;

const loadHistoryFromStorage = (): RouteHistoryEntry[] => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
};

const saveHistoryToStorage = (history: RouteHistoryEntry[]) => {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(history.slice(0, MAX_HISTORY))
    );
  } catch {
    // localStorage might be full or unavailable
  }
};

const createEmptyWaypoint = (id: string): Waypoint => ({
  id,
  geocodeResults: [],
  userInput: '',
});

export const defaultWaypoints: Waypoint[] = [
  createEmptyWaypoint('0'),
  createEmptyWaypoint('1'),
];

const getNextWaypointId = (waypoints: Waypoint[]): string => {
  const maxIndex = Math.max(...waypoints.map((wp) => parseInt(wp.id, 10)));
  return (isFinite(maxIndex) ? maxIndex + 1 : 0).toString();
};

const hasActiveRoute = (waypoints: Waypoint[]): boolean =>
  waypoints.filter(
    (wp) =>
      wp.geocodeResults.length > 0 && wp.geocodeResults.some((r) => r.selected)
  ).length >= 2;

export interface DirectionsState {
  successful: boolean;
  highlightSegment: HighlightSegment;
  waypoints: Waypoint[];
  zoomObj: ZoomObj;
  selectedAddresses: string | (Waypoint | null)[];
  results: RouteResult;
  inclineDeclineTotal?: InclineDeclineTotal;
  isOptimized: boolean;
  activeRouteIndex: number;
  // Route history
  routeHistory: RouteHistoryEntry[];
  // Curated info (blurb / fun fact) about agent-generated stops, by places.id.
  placeDetails: Record<number, PlaceDetails>;
}

interface DirectionsActions {
  updateInclineDecline: (inclineDeclineTotal: InclineDeclineTotal) => void;
  toggleShowOnMap: (params: { show: boolean; idx: number }) => void;
  clearRoutes: () => void;
  receiveRouteResults: (params: { data: ParsedDirectionsGeometry }) => void;
  receiveGeocodeResults: (params: {
    index: number;
    addresses: ActiveWaypoint[];
  }) => void;
  updateTextInput: (params: {
    inputValue: string;
    index: number;
    addressindex?: number;
  }) => void;
  clearWaypoints: () => void;
  emptyWaypoint: (params: { index: number }) => void;
  setWaypoint: (waypoints: Waypoint[]) => void;
  addWaypointAtIndex: (params: { index: number; placeholder?: LatLng }) => void;
  addEmptyWaypointToEnd: () => void;
  doRemoveWaypoint: (params: { index: number }) => void;
  highlightManeuver: (fromTo: HighlightSegment) => void;
  zoomToManeuver: (zoomObj: ZoomObj) => void;
  updatePlaceholderAddressAtIndex: (
    index: number,
    lng: number,
    lat: number
  ) => void;
  setIsOptimized: (isOptimized: boolean) => void;
  setActiveRouteIndex: (index: number) => void;
  // Route history actions
  addToHistory: (entry: Omit<RouteHistoryEntry, 'id' | 'createdAt'>) => void;
  removeFromHistory: (id: string) => void;
  clearHistory: () => void;
  loadHistory: () => void;
  setPlaceDetails: (details: Record<number, PlaceDetails>) => void;
}

type DirectionsStore = DirectionsState & DirectionsActions;

export const useDirectionsStore = create<DirectionsStore>()(
  devtools(
    immer((set) => ({
      successful: false,
      highlightSegment: { startIndex: -1, endIndex: -1, alternate: -1 },
      waypoints: defaultWaypoints,
      zoomObj: { index: -1, timeNow: -1 },
      selectedAddresses: '',
      results: { data: null, show: { '0': true } },
      isOptimized: false,
      activeRouteIndex: 0,
      routeHistory: loadHistoryFromStorage(),
      placeDetails: {},

      updateInclineDecline: (inclineDeclineTotal) =>
        set(
          (state) => {
            state.inclineDeclineTotal = inclineDeclineTotal;
          },
          undefined,
          'updateInclineDecline'
        ),

      toggleShowOnMap: ({ idx, show }) =>
        set(
          (state) => {
            state.results.show[idx] = show;
          },
          undefined,
          'toggleShowOnMap'
        ),

      clearRoutes: () =>
        set(
          (state) => {
            state.successful = false;
            state.inclineDeclineTotal = undefined;
            state.results.data = null;
            state.activeRouteIndex = 0;
            state.placeDetails = {};
          },
          undefined,
          'clearRoutes'
        ),

      receiveRouteResults: ({ data }) =>
        set(
          (state) => {
            const show: Record<string, boolean> = { '0': true };
            data.alternates?.forEach((_, i) => (show[i + 1] = true));

            state.successful = true;
            state.inclineDeclineTotal = undefined;
            state.results = { data, show };
            state.activeRouteIndex = 0;
          },
          undefined,
          'receiveRouteResults'
        ),

      receiveGeocodeResults: ({ index, addresses }) =>
        set(
          (state) => {
            if (state.waypoints[index]) {
              state.waypoints[index].geocodeResults = addresses;
              state.isOptimized = false;
            }
          },
          undefined,
          'receiveGeocodeResults'
        ),

      updateTextInput: ({ inputValue, index, addressindex }) =>
        set(
          (state) => {
            state.selectedAddresses = state.waypoints.flatMap((wp) =>
              wp.geocodeResults.map((_, i) => (i === addressindex ? wp : null))
            );

            if (state.waypoints[index]) {
              state.waypoints[index].userInput = inputValue;
              state.waypoints[index].geocodeResults = state.waypoints[
                index
              ].geocodeResults.map((result, j) => ({
                ...result,
                selected: j === addressindex,
              }));
              state.isOptimized = false;
            }
          },
          undefined,
          'updateTextInput'
        ),

      clearWaypoints: () =>
        set(
          (state) => {
            state.waypoints = [...defaultWaypoints];
            state.isOptimized = false;
          },
          undefined,
          'clearWaypoints'
        ),

      emptyWaypoint: ({ index }) =>
        set(
          (state) => {
            if (state.waypoints[index]) {
              state.waypoints[index].userInput = '';
              state.waypoints[index].geocodeResults = [];
              state.isOptimized = false;
            }
          },
          undefined,
          'emptyWaypoint'
        ),

      setWaypoint: (waypoints) =>
        set(
          (state) => {
            state.waypoints = waypoints;
          },
          undefined,
          'setWaypoint'
        ),

      addWaypointAtIndex: ({ index, placeholder }) =>
        set(
          (state) => {
            const id = getNextWaypointId(state.waypoints);

            const newWaypoint: Waypoint = placeholder
              ? {
                  id,
                  geocodeResults: [
                    {
                      title: '',
                      displaylnglat: [placeholder.lng, placeholder.lat],
                      sourcelnglat: [placeholder.lng, placeholder.lat],
                      key: index,
                      addressindex: index,
                    },
                  ],
                  userInput: `${placeholder.lng.toFixed(6)}, ${placeholder.lat.toFixed(6)}`,
                }
              : createEmptyWaypoint(id);

            state.waypoints.splice(index, 0, newWaypoint);
            state.isOptimized = false;
          },
          undefined,
          'addWaypointAtIndex'
        ),

      addEmptyWaypointToEnd: () =>
        set(
          (state) => {
            state.waypoints.push(
              createEmptyWaypoint((state.waypoints.length + 1).toString())
            );
            state.isOptimized = false;
          },
          undefined,
          'addEmptyWaypointToEnd'
        ),

      doRemoveWaypoint: ({ index }) =>
        set(
          (state) => {
            if (state.waypoints.length > 2) {
              state.waypoints.splice(index, 1);
            } else if (state.waypoints[index]) {
              state.waypoints[index].userInput = '';
              state.waypoints[index].geocodeResults = [];
            }

            state.isOptimized = false;

            if (!hasActiveRoute(state.waypoints)) {
              state.successful = false;
              state.inclineDeclineTotal = undefined;
              state.results.data = null;
            }
          },
          undefined,
          'doRemoveWaypoint'
        ),

      highlightManeuver: (fromTo) =>
        set(
          (state) => {
            const { startIndex, endIndex } = state.highlightSegment;
            const isToggleOff =
              startIndex === fromTo.startIndex && endIndex === fromTo.endIndex;

            state.highlightSegment = isToggleOff
              ? { startIndex: -1, endIndex: -1, alternate: fromTo.alternate }
              : fromTo;
          },
          undefined,
          'highlightManeuver'
        ),

      zoomToManeuver: (zoomObj) =>
        set(
          (state) => {
            state.zoomObj = zoomObj;
          },
          undefined,
          'zoomToManeuver'
        ),

      updatePlaceholderAddressAtIndex: (index, lng, lat) =>
        set(
          (state) => {
            if (state.waypoints[index]) {
              state.waypoints[index].geocodeResults = [
                {
                  title: '',
                  displaylnglat: [lng, lat],
                  sourcelnglat: [lng, lat],
                  key: index,
                  addressindex: index,
                  selected: true,
                },
              ];
              state.waypoints[index].userInput =
                `${lng.toFixed(6)}, ${lat.toFixed(6)}`;
              state.isOptimized = false;
            }
          },
          undefined,
          'updatePlaceholderAddressAtIndex'
        ),

      setIsOptimized: (isOptimized) =>
        set(
          (state) => {
            state.isOptimized = isOptimized;
          },
          undefined,
          'setIsOptimized'
        ),

      setActiveRouteIndex: (index) =>
        set(
          (state) => {
            state.activeRouteIndex = index;
          },
          undefined,
          'setActiveRouteIndex'
        ),

      // Route history actions
      addToHistory: (entry) =>
        set(
          (state) => {
            const newEntry: RouteHistoryEntry = {
              ...entry,
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
              createdAt: Date.now(),
            };
            // Remove duplicate queries
            state.routeHistory = [
              newEntry,
              ...state.routeHistory.filter((e) => e.query !== entry.query),
            ].slice(0, MAX_HISTORY);
            saveHistoryToStorage(state.routeHistory);
          },
          undefined,
          'addToHistory'
        ),

      removeFromHistory: (id) =>
        set(
          (state) => {
            state.routeHistory = state.routeHistory.filter((e) => e.id !== id);
            saveHistoryToStorage(state.routeHistory);
          },
          undefined,
          'removeFromHistory'
        ),

      clearHistory: () =>
        set(
          (state) => {
            state.routeHistory = [];
            saveHistoryToStorage([]);
          },
          undefined,
          'clearHistory'
        ),

      loadHistory: () =>
        set(
          (state) => {
            state.routeHistory = loadHistoryFromStorage();
          },
          undefined,
          'loadHistory'
        ),

      setPlaceDetails: (details) =>
        set(
          (state) => {
            state.placeDetails = details;
          },
          undefined,
          'setPlaceDetails'
        ),
    })),
    { name: 'directions-store' }
  )
);
