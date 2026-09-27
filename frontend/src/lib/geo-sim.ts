/**
 * A walking tourist, without a phone.
 *
 * The guide («проводник») is driven by `navigator.geolocation.watchPosition`,
 * which makes it the one screen that cannot be checked from a desk: at 1.4 m/s
 * the interesting moments (a stop counted as reached, the next one announced,
 * the off-route warning) are minutes apart and only happen in Grodno. This
 * module answers `?sim=walk` by putting a fake position source where the browser
 * would have one and walking the current route's stops at a speed you choose.
 *
 * Switched on ONLY by that explicit query parameter — it can never turn itself
 * on — and the panel shows a badge while it is on, so a simulated position is
 * never mistaken for a real one. Speeds: `?sim=walk&sim-speed=20` walks twenty
 * times faster than a person; the default is 1.4 m/s, and a route crosses the
 * interesting radii in a few seconds either way.
 */

export type SimPoint = readonly [number, number]; // [lat, lon]

/** Walking speed in metres per second — the number the ETA is built from. */
const WALKING_SPEED_MPS = 1.4;

const DEFAULT_SPEED_MULTIPLIER = 1;

interface SimState {
  active: boolean;
  speed: number;
  /** Stops to walk, in order. Empty until a route is built. */
  path: SimPoint[];
  /** Cumulative distance of every path vertex, in metres. */
  marks: number[];
  /** How far along the path we are, in metres. */
  travelled: number;
  fix: { lat: number; lon: number; heading: number | null };
  subscribers: Set<(fix: SimFix) => void>;
  timer: ReturnType<typeof setInterval> | null;
}

export interface SimFix {
  lat: number;
  lon: number;
  heading: number | null;
}

const state: SimState = {
  active: false,
  speed: DEFAULT_SPEED_MULTIPLIER,
  path: [],
  marks: [],
  travelled: 0,
  fix: { lat: 0, lon: 0, heading: null },
  subscribers: new Set(),
  timer: null,
};

/** Metres between two coordinates — the same equirectangular approximation the
 *  guide uses for its arrival radius, so the simulation cannot disagree with it. */
export const metres = (aLat: number, aLon: number, bLat: number, bLon: number): number => {
  const R = 6371000;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLon = ((bLon - aLon) * Math.PI) / 180;
  const midLat = ((aLat + bLat) / 2) * (Math.PI / 180);
  return Math.sqrt((dLat * R) ** 2 + (dLon * R * Math.cos(midLat)) ** 2);
};

/** Is the simulation asked for, and how fast? Pure: the URL is the argument. */
export const readSimOptions = (
  search: string
): { enabled: boolean; speed: number } => {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return { enabled: false, speed: DEFAULT_SPEED_MULTIPLIER };
  }
  const mode = params.get('sim');
  if (mode !== 'walk') return { enabled: false, speed: DEFAULT_SPEED_MULTIPLIER };
  const raw = Number.parseFloat(params.get('sim-speed') ?? '');
  const speed = Number.isFinite(raw) && raw > 0 ? Math.min(raw, 200) : DEFAULT_SPEED_MULTIPLIER;
  return { enabled: true, speed };
};

export const isSimulating = (): boolean => state.active;

export const simSpeed = (): number => state.speed;

/**
 * Where the tourist stands before there is a route to walk: the centre of
 * Grodno, the point the map opens on.
 *
 * This is not a nicety. The panel asks for a position *before* a route exists —
 * that answer is the request's origin — and a simulation that stays silent
 * until it has a path to walk leaves the build without a starting point: the
 * backend answers "no route with ≥ 2 stops" and the guide is never reached.
 * Stand somewhere sensible from the very first call, then walk the route.
 */
const START_POINT: SimFix = { lat: 53.6778, lon: 23.8295, heading: null };

/** Where the simulated tourist stands right now. */
export const currentFix = (): SimFix | null => {
  if (!state.active) return null;
  return state.path.length > 0 ? state.fix : START_POINT;
};

const accumulate = (path: SimPoint[]): number[] => {
  const marks = [0];
  for (let i = 1; i < path.length; i += 1) {
    const [aLat, aLon] = path[i - 1] as [number, number];
    const [bLat, bLon] = path[i] as [number, number];
    marks.push(marks[i - 1]! + metres(aLat, aLon, bLat, bLon));
  }
  return marks;
};

/** The point at `distance` metres along the path, with the heading of that leg. */
const pointAt = (distance: number): { fix: SimFix; atEnd: boolean } => {
  const { path, marks } = state;
  const total = marks[marks.length - 1] ?? 0;
  const clamped = Math.max(0, Math.min(distance, total));
  if (path.length === 1 || total === 0) {
    const [lat, lon] = path[0] as [number, number];
    return { fix: { lat, lon, heading: null }, atEnd: true };
  }
  let leg = 1;
  while (leg < marks.length - 1 && (marks[leg] ?? 0) < clamped) leg += 1;
  const [aLat, aLon] = path[leg - 1] as [number, number];
  const [bLat, bLon] = path[leg] as [number, number];
  const legStart = marks[leg - 1] ?? 0;
  const legLength = (marks[leg] ?? 0) - legStart;
  const t = legLength > 0 ? (clamped - legStart) / legLength : 0;
  const dLon = (bLon - aLon) * Math.cos((aLat * Math.PI) / 180);
  return {
    fix: {
      lat: aLat + (bLat - aLat) * t,
      lon: aLon + (bLon - aLon) * t,
      heading: (Math.atan2(dLon, bLat - aLat) * 180) / Math.PI,
    },
    atEnd: clamped >= total,
  };
};

const emit = (): void => {
  for (const subscriber of state.subscribers) subscriber(state.fix);
};

/** One tick of the walk: the tourist moves `speed × 1.4 m` further along. */
export const tick = (seconds = 1): void => {
  if (!state.active) return;
  if (state.path.length === 0) {
    // Standing still, but still a position the panel can use as its origin.
    state.fix = START_POINT;
    emit();
    return;
  }
  state.travelled += WALKING_SPEED_MPS * state.speed * seconds;
  const { fix, atEnd } = pointAt(state.travelled);
  state.fix = fix;
  emit();
  if (atEnd) {
    // Standing at the last stop: keep reporting it (a real GPS would) and stop
    // spending ticks on a route that has been walked to its end.
    state.travelled = state.marks[state.marks.length - 1] ?? 0;
  }
};

/** Give the simulation something to walk: the stops of the current route. */
export const setSimPath = (path: readonly SimPoint[]): void => {
  // The walk starts where the tourist actually is, not on the first stop: a
  // path that begins *at* stop 1 means nobody ever arrives there, and the guide
  // honestly reports "0 of 2 walked" while standing on the place. Passing the
  // origin first is also what a real walk looks like — you leave home, then
  // reach the first stop.
  state.path = path.map(([lat, lon]) => [lat, lon] as SimPoint);
  state.marks = accumulate(state.path);
  state.travelled = 0;
  if (state.path.length > 0) {
    state.fix = pointAt(0).fix;
    emit();
  }
};

export const resetSim = (): void => {
  state.subscribers.clear();
  if (state.timer !== null) clearInterval(state.timer);
  state.timer = null;
  state.active = false;
  state.path = [];
  state.marks = [];
  state.travelled = 0;
};

const position = (fix: SimFix, timestamp: number): GeolocationPosition =>
  ({
    coords: {
      latitude: fix.lat,
      longitude: fix.lon,
      accuracy: 8,
      altitude: null,
      altitudeAccuracy: null,
      heading: fix.heading,
      speed: WALKING_SPEED_MPS * state.speed,
    },
    timestamp,
  }) as GeolocationPosition;

/**
 * Put the fake source where the browser's is. Called once, at start-up, and only
 * when the URL asked for it; with the parameter absent nothing here runs and
 * `navigator.geolocation` is the real thing.
 */
export const installGeoSim = (search = window.location.search): boolean => {
  const { enabled, speed } = readSimOptions(search);
  if (!enabled) return false;
  if (state.active) return true;
  state.active = true;
  state.speed = speed;

  // A handle for QA scripts driving a recorded walk: reading «it walks» off a
  // video is guesswork, and this reports what the simulation actually has.
  // It exists only behind `?sim=walk`, which is never a shipped default.
  (window as unknown as Record<string, unknown>).__geoSim = {
    fix: () => currentFix(),
    travelled: () => state.travelled,
    pathLength: () => state.path.length,
    subscribers: () => state.subscribers.size,
    tick: (seconds = 1) => tick(seconds),
  };

  const fake: Geolocation = {
    watchPosition(success, error) {
      const subscriber = (fix: SimFix) => {
        success?.(position(fix, Date.now()));
      };
      state.subscribers.add(subscriber);
      if (state.path.length > 0) subscriber(state.fix);
      if (state.timer === null) {
        state.timer = setInterval(() => tick(), 1000);
      }
      void error;
      return state.subscribers.size;
    },
    clearWatch(id) {
      const subscribers = [...state.subscribers];
      const victim = subscribers[id - 1];
      if (victim) state.subscribers.delete(victim);
    },
    getCurrentPosition(success) {
      const fix = currentFix();
      if (fix) success(position(fix, Date.now()));
    },
  } as Geolocation;

  Object.defineProperty(window.navigator, 'geolocation', {
    value: fake,
    configurable: true,
  });
  return true;
};
