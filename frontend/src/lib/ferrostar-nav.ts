/** Ferrostar navigation integration for the Grodno tourist guide. */

import type {
  GeographicCoordinate,
  Route,
  RouteStep,
  SerializableNavigationControllerConfig,
  SerializableNavState,
  SpokenInstruction,
  TripState,
  UserLocation,
  VisualInstruction,
  NavigationSession,
} from '@stadiamaps/ferrostar';
import type {
  GuideManeuver,
  Leg,
  Maneuver,
  ParsedDirectionsGeometry,
} from '@/components/types';
import { metresBetween } from '@/components/parts/guide-format';

/** The WASM module's namespace, loaded once and shared by every session. */
type FerrostarModule = typeof import('@stadiamaps/ferrostar');

let modulePromise: Promise<FerrostarModule> | null = null;
let loadedModule: FerrostarModule | null = null;

/** Load the WASM core. */
const loadFerrostar = async (): Promise<FerrostarModule | null> => {
  try {
    modulePromise ??= import('@stadiamaps/ferrostar');
    loadedModule = await modulePromise;
    return loadedModule;
  } catch (error) {
    console.warn('Could not load Ferrostar WASM.', error);
    loadedModule = null;
    return null;
  }
};

/** Resolves when Ferrostar is usable, or with `null` when it never became usable. */
export const ferrostarReady: Promise<FerrostarModule | null> = loadFerrostar();

/** Is the WASM core already loaded? */
export const isFerrostarAvailable = (): boolean => loadedModule != null;

interface LatLon {
  lat: number;
  lon: number;
}

/** Cumulative-distance record for a polyline. */
interface LineGeometry {
  points: LatLon[];
  cum: number[];
  total: number;
}

const buildLineGeometry = (decoded: number[][]): LineGeometry | null => {
  const points: LatLon[] = [];
  for (const c of decoded) {
    const lat = c[0];
    const lon = c[1];
    if (
      typeof lat !== 'number' ||
      typeof lon !== 'number' ||
      !Number.isFinite(lat) ||
      !Number.isFinite(lon)
    ) {
      continue;
    }
    points.push({ lat, lon });
  }
  if (points.length < 2) return null;
  const cum = [0];
  for (let i = 1; i < points.length; i++) {
    cum.push((cum[i - 1] ?? 0) + metresBetween(points[i - 1]!, points[i]!));
  }
  const total = cum[cum.length - 1] ?? 0;
  return total > 0 ? { points, cum, total } : null;
};

/** Slice of the line between `fromCum` and `toCum` metres, read off cumulative distances. */
const stepGeometry = (
  line: LineGeometry,
  fromCum: number,
  toCum: number
): GeographicCoordinate[] => {
  const coords: GeographicCoordinate[] = [];
  for (let i = 0; i < line.points.length; i++) {
    const dist = line.cum[i] ?? 0;
    if (dist < fromCum - 0.5) continue;
    if (dist > toCum + 0.5) break;
    const point = line.points[i];
    if (point) coords.push({ lat: point.lat, lng: point.lon });
  }
  if (coords.length < 2) {
    coords.length = 0;
    coords.push(pointAtCum(line, fromCum), pointAtCum(line, toCum));
  }
  return coords;
};

const pointAtCum = (line: LineGeometry, dist: number): GeographicCoordinate => {
  const clamped = Math.min(Math.max(dist, 0), line.total);
  for (let i = 1; i < line.points.length; i++) {
    const from = line.cum[i - 1] ?? 0;
    const to = line.cum[i] ?? from;
    if (clamped > to) continue;
    const a = line.points[i - 1]!;
    const b = line.points[i]!;
    const t = to > from ? (clamped - from) / (to - from) : 0;
    return {
      lat: a.lat + (b.lat - a.lat) * t,
      lng: a.lon + (b.lon - a.lon) * t,
    };
  }
  const last = line.points[line.points.length - 1]!;
  return { lat: last.lat, lng: last.lon };
};

/** Generate spoken instructions for a step at fixed trigger distances. */
const generateSpokenInstructions = (
  instruction: string,
  stepIndex: number
): SpokenInstruction[] => {
  const triggers = [400, 200, 50, 0];
  return triggers.map((trigger) => ({
    text: instruction,
    ssml: undefined,
    triggerDistanceBeforeManeuver: trigger,
    utteranceId: `00000000-0000-4000-8000-${stepIndex
      .toString(16)
      .padStart(8, '0')}${trigger.toString(16).padStart(4, '0')}`,
  }));
};

export interface FerrostarRouteResult {
  /** Ferrostar Route ready to pass to FerrostarNavigator. */
  route: Route;
  /** Ordered maneuvers for the UI: `maneuvers[i]` describes `route.steps[i]`. */
  maneuvers: GuideManeuver[];
  /** Map from maneuver key (e.g. "0-1") → step index in `route.steps`. */
  stepKeyToIndex: Map<string, number>;
}

/** Build a Ferrostar `Route` from our Valhalla response data. */
export const buildFerrostarRoute = (
  data: ParsedDirectionsGeometry | null
): FerrostarRouteResult | null => {
  const decoded = data?.decodedGeometry;
  if (!decoded || decoded.length < 2) return null;

  const line = buildLineGeometry(decoded);
  if (!line) return null;

  const legs = data?.trip?.legs ?? [];
  if (legs.length === 0) return null;

  const steps: RouteStep[] = [];
  const maneuvers: GuideManeuver[] = [];
  const stepKeyToIndex = new Map<string, number>();

  let stepIndex = 0;
  let legBase = 0;

  legs.forEach((leg: Leg, legIndex: number) => {
    const legManeuvers = leg.maneuvers ?? [];

    legManeuvers.forEach((mnv: Maneuver, mnvIndex: number) => {
      const key = `${legIndex}-${mnvIndex}`;
      const beginIdx = legBase + (mnv.begin_shape_index ?? 0);
      const endIdx =
        legBase + (mnv.end_shape_index ?? mnv.begin_shape_index ?? 0);

      const beginDist = line.cum[Math.min(beginIdx, line.cum.length - 1)] ?? 0;
      const endDist =
        line.cum[Math.min(endIdx, line.cum.length - 1)] ?? beginDist;

      const instruction = mnv.instruction ?? '';
      const roadName = mnv.street_names?.[0];

      const stepCoords = stepGeometry(line, beginDist, endDist);
      const distanceM = Math.max(0, endDist - beginDist);
      const durationS = mnv.time ?? 0;

      const spokenInstructions = generateSpokenInstructions(
        instruction,
        stepIndex
      );

      const safeStepCoords =
        stepCoords.length >= 2
          ? stepCoords
          : [pointAtCum(line, beginDist), pointAtCum(line, endDist)];

      steps.push({
        geometry: safeStepCoords,
        distance: distanceM,
        duration: durationS,
        roadName: roadName ?? undefined,
        exits: [],
        instruction,
        spokenInstructions,
        visualInstructions: [],
        annotations: undefined,
        incidents: [],
        drivingSide: undefined,
        roundaboutExitNumber: undefined,
      });

      stepKeyToIndex.set(key, stepIndex);
      stepIndex++;

      maneuvers.push({
        key,
        type: mnv.type ?? 0,
        instruction,
        along: beginDist,
      });
    });

    const lastManeuver = legManeuvers[legManeuvers.length - 1];
    legBase += lastManeuver?.end_shape_index ?? 0;
  });

  if (steps.length === 0) return null;

  const locations = data?.trip?.locations ?? [];
  const waypoints = locations.map((loc) => ({
    coordinate: { lat: loc.lat, lng: loc.lon },
    kind: 'Break' as const,
    properties: undefined,
  }));

  if (waypoints.length < 2) {
    const first = line.points[0]!;
    const last = line.points[line.points.length - 1]!;
    waypoints.length = 0;
    waypoints.push(
      {
        coordinate: { lat: first.lat, lng: first.lon },
        kind: 'Break',
        properties: undefined,
      },
      {
        coordinate: { lat: last.lat, lng: last.lon },
        kind: 'Break',
        properties: undefined,
      }
    );
  }

  let minLat = line.points[0]!.lat;
  let maxLat = minLat;
  let minLon = line.points[0]!.lon;
  let maxLon = minLon;
  for (const point of line.points) {
    if (point.lat < minLat) minLat = point.lat;
    if (point.lat > maxLat) maxLat = point.lat;
    if (point.lon < minLon) minLon = point.lon;
    if (point.lon > maxLon) maxLon = point.lon;
  }
  const bbox = {
    sw: { lat: minLat, lng: minLon },
    ne: { lat: maxLat, lng: maxLon },
  };

  const route: Route = {
    geometry: line.points.map((p) => ({ lat: p.lat, lng: p.lon })),
    bbox,
    distance: line.total,
    waypoints,
    steps,
  };

  return { route, maneuvers, stepKeyToIndex };
};

/** Ferrostar navigation configuration matching the spec. */
const DEFAULT_CONFIG: SerializableNavigationControllerConfig = {
  stepAdvanceCondition: {
    DistanceEntryExit: {
      distanceToEndOfStep: 30,
      distanceAfterEndStep: 5,
      minimumHorizontalAccuracy: 25,
      hasReachedEndOfCurrentStep: false,
    },
  },
  arrivalStepAdvanceCondition: {
    DistanceToEndOfStep: { distance: 30, minimumHorizontalAccuracy: 25 },
  },
  routeDeviationTracking: {
    StaticThreshold: {
      minimumHorizontalAccuracy: 25,
      maxAcceptableDeviation: 25,
    },
  },
  snappedLocationCourseFiltering: 'SnapToRoute',
  waypointAdvance: { WaypointWithinRange: 40 },
};

/** Fallback accuracy when the browser reported none: 25 m, the config's own bar. */
const DEFAULT_ACCURACY_M = 25;

/** Our wrapper around Ferrostar NavigationSession. */
export class FerrostarNavigator {
  private session: NavigationSession | null;
  private _navState: SerializableNavState | null = null;
  private _state: TripState | null = null;

  constructor(route: Route, mod: FerrostarModule) {
    this.session = new mod.NavigationSession(route, DEFAULT_CONFIG);
  }

  /** Feed a raw GPS fix into Ferrostar. */
  update(rawFix: {
    lat: number;
    lon: number;
    accuracy: number | null;
    at: number;
  }): TripState | null {
    if (!this.session) return null;

    const location: UserLocation = {
      coordinates: { lat: rawFix.lat, lng: rawFix.lon },
      horizontalAccuracy: rawFix.accuracy ?? DEFAULT_ACCURACY_M,
      courseOverGround: undefined,
      timestamp: {
        secs_since_epoch: Math.floor(rawFix.at / 1000),
        nanos_since_epoch: 0,
      },
      speed: undefined,
    };

    try {
      const current =
        this._navState ??
        (this.session.getInitialState(location) as SerializableNavState);
      const next = this.session.updateUserLocation(
        location,
        current
      ) as SerializableNavState;
      this._navState = next;
      this._state = next.tripState;
      return this._state;
    } catch (error) {
      console.error('Ferrostar could not process a location update.', error);
      return null;
    }
  }

  /** The latest TripState returned by the last update. */
  get state(): TripState | null {
    return this._state;
  }

  /** Advance to the next step manually (used by the "я на месте" button). */
  advanceToNextStep(): void {
    if (this.session && this._navState) {
      this._navState = this.session.advanceToNextStep(
        this._navState
      ) as SerializableNavState;
      this._state = this._navState.tripState;
    }
  }

  /** Release WASM resources. */
  destroy(): void {
    this.session?.free();
    this.session = null;
    this._navState = null;
    this._state = null;
  }
}

/** Create a Ferrostar navigator for `route`, or null when the WASM core is unavailable. */
export const createFerrostarNavigator = async (
  route: Route
): Promise<FerrostarNavigator | null> => {
  const mod = await ferrostarReady;
  if (!mod) return null;
  try {
    return new FerrostarNavigator(route, mod);
  } catch (error) {
    console.error('Could not create a Ferrostar navigation session.', error);
    return null;
  }
};

/** The payload of the `Navigating` variant, or null for Idle/Complete. */
type NavigatingTripState = Extract<
  TripState,
  { Navigating: unknown }
>['Navigating'];

/** Extract Ferrostar's Navigating state; TripState is tagged by key, not `.tag`. */
const navigating = (
  state: TripState | null | undefined
): NavigatingTripState | null =>
  state && 'Navigating' in state ? state.Navigating : null;

/** Extract `courseOverGround.degrees` from a Navigating TripState. */
export const extractCourse = (state: TripState | null): number | null =>
  navigating(state)?.snappedUserLocation.courseOverGround?.degrees ?? null;

/** Extract distanceToNextManeuver from a Navigating TripState. */
export const extractDistanceToNextManeuver = (
  state: TripState | null
): number | null => navigating(state)?.progress.distanceToNextManeuver ?? null;

/** Extract distanceRemaining from a Navigating TripState. */
export const extractDistanceRemaining = (
  state: TripState | null
): number | null => navigating(state)?.progress.distanceRemaining ?? null;

/** Is the user completely off-route? */
export const isCompletelyOffRoute = (
  state: TripState | null
): boolean | null => {
  const deviation = navigating(state)?.deviation;
  if (!deviation) return null;
  if (deviation === 'NoDeviation') return false;
  return 'CompletelyOffRoute' in deviation.Deviation.kind;
};

/** The active spoken instruction from the Navigating state. */
export const extractSpokenInstruction = (
  state: TripState | null
): SpokenInstruction | null => navigating(state)?.spokenInstruction ?? null;

/** The next visual instruction (maneuver banner) from the Navigating state. */
export const extractVisualInstruction = (
  state: TripState | null
): VisualInstruction | null => navigating(state)?.visualInstruction ?? null;

/** Remaining steps from the Navigating state; `[0]` is the one being walked now. */
export const extractRemainingSteps = (state: TripState | null): RouteStep[] =>
  navigating(state)?.remainingSteps ?? [];
