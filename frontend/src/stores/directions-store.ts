import type {
  ActiveWaypoint,
  ParsedDirectionsGeometry,
} from '@/components/types';
import type { Photo } from '@/api/types';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

export interface Waypoint {
  id: string;
  geocodeResults: ActiveWaypoint[];
  userInput: string;
  placeId?: number;
  pinned?: boolean;
}

/** Id of the waypoint that marks the tourist's own position (the route start the sidebar pins when the browser hands us coordinates). */
export const ME_WAYPOINT_ID = 'me';

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

export interface RouteHistoryEntry {
  id: string;
  query: string;
  timeBudget: number;
  places: RouteHistoryPlace[];
  createdAt: number;
  /** Fingerprint of the route's stops (see `guideRouteKey`). */
  routeKey?: string;
  /** How far the walk got, once it was started at all. */
  walk?: RouteHistoryWalk;
}

/** How far the tourist got through a route's walk. */
export interface RouteHistoryWalk {
  visited: number;
  total: number;
  /** When the walk last moved (ms epoch, the same clock as `createdAt`). */
  at: number;
  /** Every stop of the route was marked visited. */
  completed: boolean;
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
  openingHours?: string | null;
  ticketPrice?: string | null;
  town?: string | null;
  district?: string | null;
  /** The point's picture with its credit — absent when the point has none. */
  photo?: Photo | null;
}

export interface PlaceDetails {
  name: string;
  category: string | null;
  blurb: string | null;
  funFact: string | null;
  funFacts: string[];
  links: PlaceLink[];
  visitMinutes: number | null;
  openingHours?: string | null;
  ticketPrice?: string | null;
  town?: string | null;
  district?: string | null;
  /** The point's picture with its credit — absent when the point has none. */
  photo?: Photo | null;
}

/** One turn of the refinement log: what the user asked and what it changed. */
export interface RefinementEntry {
  id: string;
  instruction: string;
  added: string[];
  removed: string[];
  createdAt: number;
}

/** Everything a refinement turn can change. */
export interface RouteSnapshot {
  waypoints: Waypoint[];
  placeDetails: Record<number, PlaceDetails>;
  excludedPlaceIds: number[];
  refinementLog: RefinementEntry[];
}

const MAX_SNAPSHOTS = 10;

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
  } catch {}
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
  routeHistory: RouteHistoryEntry[];
  placeDetails: Record<number, PlaceDetails>;
  excludedPlaceIds: number[];
  refinementLog: RefinementEntry[];
  routeSnapshots: RouteSnapshot[];
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
  addToHistory: (entry: Omit<RouteHistoryEntry, 'id' | 'createdAt'>) => void;
  /** Record how far the walk of a route has got. */
  markWalked: (walk: {
    routeKey: string;
    visited: number;
    total: number;
  }) => void;
  removeFromHistory: (id: string) => void;
  clearHistory: () => void;
  loadHistory: () => void;
  setPlaceDetails: (details: Record<number, PlaceDetails>) => void;
  /** Remove stops by DB id and remember them as excluded from future turns. */
  excludeStops: (params: { placeIds: number[] }) => void;
  /** Un-exclude a stop (the user brought it back / asked for it explicitly). */
  includeStop: (params: { placeId: number }) => void;
  pushRefinement: (entry: Omit<RefinementEntry, 'id' | 'createdAt'>) => void;
  /** Snapshot the route before a refine or a manual edit. */
  snapshotRoute: () => void;
  /** Restore the newest snapshot; no-op when the stack is empty. */
  undoRefinement: () => void;
  /** Full reset: stops, details, exclusions, log, history of edits, line. */
  resetRoute: () => void;
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
      excludedPlaceIds: [],
      refinementLog: [],
      routeSnapshots: [],

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

      addToHistory: (entry) =>
        set(
          (state) => {
            const previous = state.routeHistory.find(
              (e) => e.query === entry.query
            );
            const newEntry: RouteHistoryEntry = {
              ...entry,
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
              createdAt: Date.now(),
            };
            if (previous?.routeKey && previous.routeKey === entry.routeKey) {
              newEntry.walk = previous.walk;
            }
            state.routeHistory = [
              newEntry,
              ...state.routeHistory.filter((e) => e.query !== entry.query),
            ].slice(0, MAX_HISTORY);
            saveHistoryToStorage(state.routeHistory);
          },
          undefined,
          'addToHistory'
        ),

      markWalked: ({ routeKey, visited, total }) =>
        set(
          (state) => {
            const entry = state.routeHistory.find(
              (e) => e.routeKey === routeKey
            );
            if (!entry) return;
            entry.walk = {
              visited,
              total,
              at: Date.now(),
              completed: total > 0 && visited >= total,
            };
            state.routeHistory = [...state.routeHistory];
            saveHistoryToStorage(state.routeHistory);
          },
          undefined,
          'markWalked'
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

      excludeStops: ({ placeIds }) =>
        set(
          (state) => {
            const drop = new Set(placeIds);
            state.waypoints = state.waypoints.filter(
              (wp) => wp.placeId === undefined || !drop.has(wp.placeId)
            );
            const excluded = new Set(state.excludedPlaceIds);
            drop.forEach((id) => excluded.add(id));
            state.excludedPlaceIds = [...excluded];
            state.isOptimized = false;

            if (!hasActiveRoute(state.waypoints)) {
              state.successful = false;
              state.results.data = null;
            }
          },
          undefined,
          'excludeStops'
        ),

      includeStop: ({ placeId }) =>
        set(
          (state) => {
            state.excludedPlaceIds = state.excludedPlaceIds.filter(
              (id) => id !== placeId
            );
          },
          undefined,
          'includeStop'
        ),

      pushRefinement: (entry) =>
        set(
          (state) => {
            state.refinementLog = [
              ...state.refinementLog,
              {
                ...entry,
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                createdAt: Date.now(),
              },
            ];
          },
          undefined,
          'pushRefinement'
        ),

      snapshotRoute: () =>
        set(
          (state) => {
            state.routeSnapshots = [
              ...state.routeSnapshots,
              {
                waypoints: state.waypoints.map((wp) => ({
                  ...wp,
                  geocodeResults: wp.geocodeResults.map((r) => ({ ...r })),
                })),
                placeDetails: { ...state.placeDetails },
                excludedPlaceIds: [...state.excludedPlaceIds],
                refinementLog: [...state.refinementLog],
              },
            ].slice(-MAX_SNAPSHOTS);
          },
          undefined,
          'snapshotRoute'
        ),

      undoRefinement: () =>
        set(
          (state) => {
            const snap = state.routeSnapshots.at(-1);
            if (!snap) {
              return;
            }
            state.waypoints = snap.waypoints;
            state.placeDetails = snap.placeDetails;
            state.excludedPlaceIds = snap.excludedPlaceIds;
            state.refinementLog = snap.refinementLog;
            state.routeSnapshots = state.routeSnapshots.slice(0, -1);
          },
          undefined,
          'undoRefinement'
        ),

      resetRoute: () =>
        set(
          (state) => {
            state.waypoints = [...defaultWaypoints];
            state.placeDetails = {};
            state.excludedPlaceIds = [];
            state.refinementLog = [];
            state.routeSnapshots = [];
            state.successful = false;
            state.inclineDeclineTotal = undefined;
            state.results = { data: null, show: { '0': true } };
            state.activeRouteIndex = 0;
            state.isOptimized = false;
          },
          undefined,
          'resetRoute'
        ),
    })),
    { name: 'directions-store' }
  )
);
