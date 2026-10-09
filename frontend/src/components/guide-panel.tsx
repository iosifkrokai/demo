import {
  BedDouble,
  Bus,
  Coffee,
  Footprints,
  Navigation,
  Play,
  RotateCcw,
  TriangleAlert,
  UtensilsCrossed,
  Volume2,
  VolumeX,
  XIcon,
} from 'lucide-react';
import type { TFunction } from 'i18next';
import type { TripState } from '@stadiamaps/ferrostar';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  ActiveWaypoint,
  GuideManeuver,
  ParsedDirectionsGeometry,
} from '@/components/types';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';
import type { ServiceAlong } from '@/api/types';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getManeuverIcon } from '@/utils/get-maneuver-icon';
import {
  loadVisitOverrides,
  saveVisitOverrides,
  visitMinutesFor,
  type VisitOverrides,
} from '@/utils/visit-time';

import { useCommonStore } from '@/stores/common-store';
import { isSimulating, setSimPath } from '@/lib/geo-sim';
import { useServicesAlong } from '@/hooks/use-services-along';
import {
  cancelSpeech,
  decideTriggeredVoice,
  decideVoice,
  isNewManeuver,
  markSpoken,
  speak,
  type SpokenThresholds,
  type VoiceManeuver,
} from '@/lib/guide-voice';
import {
  buildFerrostarRoute,
  extractCourse,
  extractDistanceRemaining,
  extractDistanceToNextManeuver,
  extractRemainingSteps,
  extractSpokenInstruction,
  ferrostarReady,
  isCompletelyOffRoute,
  FerrostarNavigator,
  type FerrostarRouteResult,
} from '@/lib/ferrostar-nav';
import { GuideEmpty } from './parts/guide-empty';
import { courseAlongLine, courseAtPoint } from './parts/guide-course';
import { fmtDist, metresBetween } from './parts/guide-format';
import { mergeMicroManeuvers } from './parts/guide-maneuvers-stub';
import { guideModeFor } from './parts/guide-mode';
import { FerrostarNavigationHud } from './parts/ferrostar-navigation-hud';
import { GuideProgress } from './parts/guide-progress';
import { GuideStopList } from './parts/guide-stop-list';

/** One stop of the built route, as the guide walks it. */
export interface GuideStop {
  id: string;
  name: string;
  lat: number;
  lon: number;
  placeId?: number;
  category?: string | null;
  visitMinutes?: number | null;
}

/**
 * A contextual POI offered along the way. Purely a proposal: the panel never
 * edits the route on its own — «добавить» only hands the choice to the caller,
 * and skipping one leaves the route untouched.
 */
export interface GuideSuggestion {
  id: string;
  name: string;
  /** «отклонение 4 мин», «на маршруте» — why it is being offered. */
  detail: string;
}

interface GuidePanelProps {
  stops: GuideStop[];
  /** Enter directly into the live navigator from the route's primary action. */
  startInMoving?: boolean;
  /** Show the route overview while the mobile sheet is fully expanded. */
  overviewOpen?: boolean;
  /**
   * Off-route re-plan override. The integration layer can wire this to
   * `POST /reroute`; without it the panel re-requests the same stops from the
   * current position through the existing directions query (see `reroute()`).
   */
  onReroute?: () => void;
  suggestions?: GuideSuggestion[];
  onAddSuggestion?: (id: string) => void;
  /**
   * The transport the plan was built for (Valhalla costing): «pedestrian»,
   * «bicycle», «auto» — or nothing while it is unknown, which the guide reads
   * as walking. The turn instructions already arrive in the costing's own
   * language; this makes the guide's own voice match them.
   */
  transport?: string | null;
  /**
   * How far the walk has got, reported whenever it changes.
   *
   * The panel owns the progress, but the *history* of the route is the caller's
   * business — this is how «пройдено 3 из 5» can still be shown after the guide
   * is closed. Derived from `effectiveVisited`, so a weak GPS fix never ticks a
   * stop off behind the tourist's back.
   */
  onWalked?: (progress: { visited: number; total: number }) => void;
  /**
   * Leaves navigation entirely — the guide is over, the planner comes back.
   * Required, because the panel that hosts this guide is hidden while walking,
   * so the exit cannot live in the panel's own header.
   */
  onExit: () => void;
}

const STORAGE_KEY = 'grodno-guide-progress';
/** You are "at" a stop when you are this close to it. */
const ARRIVAL_RADIUS_M = 40;
/** Above this accuracy the fix is too coarse for a confident «через 30 м». */
const WEAK_ACCURACY_M = 50;
/** A fix older than this is treated as lost, not as a position. */
const STALE_FIX_MS = 20_000;
/** Beyond this distance from the line the tourist is off route. */
const OFF_ROUTE_M = 60;
/** Consecutive off-route fixes before the prompt appears (kills GPS flicker). */
const OFF_ROUTE_FIXES = 2;
/** How far the walk may slide back before we freeze progress (jump guard). */
const BACKWARD_TOLERANCE_M = 15;
/** Show a nearby POI hint when it is within this many metres ahead on the route. */
const NEARBY_HINT_AHEAD_M = 120;

/**
 * Fingerprint of the route the walk belongs to.
 *
 * The parent uses it as the React key: a rebuilt route remounts the guide, which
 * is what starts a fresh walk (see sidebar.tsx). Structural, not nominal: the
 * planner knows the stops it just built (place id, name, coordinates) before
 * they become `GuideStop`s, and it computes the *same* key — that is how a
 * route's history entry finds the walk that belongs to it.
 */
export const guideRouteKey = (
  stops: ReadonlyArray<{
    placeId?: number | null;
    name: string;
    lat: number;
    lon: number;
  }>
) =>
  stops
    .map(
      (s) => `${s.placeId ?? s.name}@${s.lat.toFixed(4)},${s.lon.toFixed(4)}`
    )
    .join('|');

interface StoredProgress {
  route: string;
  visited: string[];
  startedAt: number;
}

const loadProgress = (key: string): StoredProgress => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as StoredProgress;
      if (parsed.route === key) return parsed;
    }
  } catch {
    // unreadable storage — start fresh rather than break the walk
  }
  return { route: key, visited: [], startedAt: Date.now() };
};

const saveProgress = (progress: StoredProgress) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
  } catch {
    // storage unavailable: progress stays in memory for this session
  }
};

// ── Geometry ────────────────────────────────────────────────────────────────
//
// Everything below works on the line Valhalla returned for this route (the
// same response the map draws), never on a second request. When there is no
// line yet — a route without geometry — the guide falls back to straight-line
// distances between stops and simply shows no turn-by-turn banner.

interface LatLon {
  lat: number;
  lon: number;
}

interface Fix extends LatLon {
  /** Metres of 68 % confidence; null when the browser did not say. */
  accuracy: number | null;
  at: number;
  /**
   * The direction the handset points, in degrees; null when the browser did
   * not say. Carried on the fix itself rather than pushed straight into the
   * store, so one effect owns everything the map is told about the position
   * (see the `setGuideFix` publication below).
   */
  heading: number | null;
}

type FixQuality = 'unavailable' | 'waiting' | 'stale' | 'poor' | 'good';

interface LineGeometry {
  points: LatLon[];
  /** Cumulative metres at each vertex. */
  cum: number[];
  total: number;
}

const buildLine = (
  data: ParsedDirectionsGeometry | null
): LineGeometry | null => {
  const raw = data?.decodedGeometry;
  if (!raw || raw.length < 2) return null;
  const points: LatLon[] = [];
  for (const c of raw) {
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

/**
 * Project `p` onto the segment a→b in a local metre plane. Good enough at
 * neighbourhood scale and cheaper than turf, which we do not need here.
 */
const projectOnSegment = (p: LatLon, a: LatLon, b: LatLon) => {
  const latRef = (((a.lat + b.lat) / 2) * Math.PI) / 180;
  const kx = 111_320 * Math.cos(latRef);
  const ky = 111_320;
  const ax = a.lon * kx;
  const ay = a.lat * ky;
  const bx = b.lon * kx;
  const by = b.lat * ky;
  const px = p.lon * kx;
  const py = p.lat * ky;
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t =
    len2 === 0
      ? 0
      : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  return { t, dist: Math.hypot(px - cx, py - cy), along: t * Math.sqrt(len2) };
};

/** Where the tourist is on the line + how far the line is from them. */
const locateOnLine = (p: LatLon, line: LineGeometry) => {
  let along = 0;
  let offRoute = Number.POSITIVE_INFINITY;
  for (let i = 1; i < line.points.length; i++) {
    const a = line.points[i - 1]!;
    const b = line.points[i]!;
    const pr = projectOnSegment(p, a, b);
    if (pr.dist < offRoute) {
      offRoute = pr.dist;
      along = (line.cum[i - 1] ?? 0) + pr.along;
    }
  }
  return { along, offRoute };
};

const buildManeuvers = (
  data: ParsedDirectionsGeometry | null,
  line: LineGeometry | null
): GuideManeuver[] => {
  if (!data || !line) return [];
  const legs = data.trip?.legs ?? [];
  const out: GuideManeuver[] = [];
  let base = 0;
  legs.forEach((leg, legIndex) => {
    const legBase = base;
    const maneuvers = leg.maneuvers ?? [];
    maneuvers.forEach((mnv, j) => {
      const index = legBase + (mnv.begin_shape_index ?? 0);
      const clamped = Math.min(Math.max(index, 0), line.cum.length - 1);
      out.push({
        key: `${legIndex}-${j}`,
        type: mnv.type,
        instruction: mnv.instruction,
        along: line.cum[clamped] ?? 0,
      });
    });
    const last = maneuvers[maneuvers.length - 1];
    base = legBase + (last ? (last.end_shape_index ?? 0) : 0);
  });
  return out.sort((a, b) => a.along - b.along);
};

/** A waypoint that simply says «я здесь» — mirrors sidebar's meWaypoint. */
const meWaypoint = (lat: number, lon: number, t: TFunction): Waypoint => {
  const lngLat: [number, number] = [lon, lat];
  const label = t('guide.myLocation');
  const result: ActiveWaypoint = {
    title: label,
    description: t('guide.routeStart'),
    selected: true,
    displaylnglat: lngLat,
    sourcelnglat: lngLat,
    key: 0,
    addressindex: 0,
  };
  return {
    id: ME_WAYPOINT_ID,
    userInput: label,
    geocodeResults: [result],
  };
};

/**
 * Mode 2 — the guide: walk the route stop by stop.
 *
 * Review (before «Начать маршрут») shows the plan: the next stop, the progress
 * and the full stop list. Movement mode is the navigator: a large next manoeuvre
 * with its distance, the next-stop card with an ETA, progress along the route
 * line (walked part vs remaining) and an expandable stop list.
 *
 * Honesty rules, from the spec:
 *  - a poor or stale fix suppresses the confident «через 30 м» — the turn is
 *    announced without a number, and stops are not auto-completed;
 *  - denied geolocation leaves manual progression (`я на месте` and every row);
 *  - off route, the panel offers to re-plan from the current position, keeping
 *    every stop (mandatory points are never dropped by the re-plan);
 *  - suggestions are proposals that never change the route by themselves.
 */
/**
 * The guide's own honesty about where the position came from: with `?sim=walk`
 * the fix is replayed, and every screen that shows a distance must say so. One
 * component, used by all three states of the panel — a replayed position that
 * looks like a phone's is the one thing the simulation must not do.
 */
const SimulatedBadge = ({ active }: { active: boolean }) => {
  const { t } = useTranslation();
  if (!active) return null;
  return (
    <p
      data-testid="guide-simulated"
      className="flex items-center gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-meta font-medium text-amber-900"
    >
      {t('sidebar.status.simulatedFix')}
    </p>
  );
};

export const GuidePanel = ({
  stops,
  startInMoving = false,
  overviewOpen = false,
  onReroute,
  suggestions = [],
  onAddSuggestion,
  transport = null,
  onWalked,
  onExit,
}: GuidePanelProps) => {
  const { t, i18n } = useTranslation();
  const key = useMemo(() => guideRouteKey(stops), [stops]);
  /**
   * How the guide speaks about movement: on foot, on a bike, or driving.
   * Not memoized: the wording lives in the dictionary, and a plain call in
   * render picks the current language up on every re-render (which a language
   * change already triggers) — a memo keyed on the transport would keep the old
   * language's words.
   */
  const travel = guideModeFor(transport);
  const [visitOverrides, setVisitOverrides] = useState<VisitOverrides>(() =>
    loadVisitOverrides(key)
  );
  /** A rebuilt route remounts the panel, but a key change also resets this. */
  useEffect(() => {
    setVisitOverrides(loadVisitOverrides(key));
  }, [key]);
  const setStopVisitMinutes = useCallback(
    (id: string, minutes: number | null) => {
      setVisitOverrides((prev) => {
        const next = { ...prev };
        if (minutes == null) delete next[id];
        else next[id] = minutes;
        saveVisitOverrides(key, next);
        return next;
      });
    },
    [key]
  );
  /** The tourist's own minutes win over the dataset's estimate. */
  const visitMinutesOf = useCallback(
    (stop: GuideStop) =>
      visitMinutesFor(stop.id, stop.visitMinutes, visitOverrides),
    [visitOverrides]
  );
  /** What the timeline rows show: the effective minutes and the raw estimate. */
  const listStops = useMemo(
    () =>
      stops.map((stop) => ({
        id: stop.id,
        name: stop.name,
        category: stop.category ?? null,
        visitMinutes: visitMinutesOf(stop),
        visitOverride: visitOverrides[stop.id] ?? null,
        estimateMinutes: stop.visitMinutes ?? null,
      })),
    [stops, visitMinutesOf, visitOverrides]
  );
  const [progress, setProgress] = useState<StoredProgress>(() =>
    loadProgress(key)
  );
  const [fix, setFix] = useState<Fix | null>(null);
  const [geoState, setGeoState] = useState<'idle' | 'ok' | 'denied'>(() =>
    typeof navigator === 'undefined' || !('geolocation' in navigator)
      ? 'denied'
      : 'idle'
  );
  const [now, setNow] = useState(() => Date.now());

  /** A fix good enough to start guiding immediately without a second button. */
  const hasTrustedFix =
    geoState === 'ok' &&
    fix?.accuracy != null &&
    fix.accuracy <= WEAK_ACCURACY_M;
  /**
   * How the guide enters — the tourist's own call, not a required detour.
   *
   * When a trusted fix is already in hand, the guide opens straight into
   * `moving` so the navigator starts without a second button press.
   * Without a fix the guide opens in `review`; the button in that view starts
   * the walk once a fix arrives. Either way the tourist decides when to move.
   *
   * Initial state is always 'review' (useState initializer is synchronous and
   * cannot read a GPS fix that arrives asynchronously). The useEffect below
   * upgrades to 'moving' the moment a trusted fix is already present — which
   * happens in tests that push a fix before mount, and in production when the
   * browser already had a cached fix.
   */
  const [mode, setMode] = useState<'review' | 'moving'>(() =>
    startInMoving ? 'moving' : 'review'
  );
  useEffect(() => {
    if (startInMoving) setMode('moving');
    else if (overviewOpen) setMode('review');
  }, [startInMoving, overviewOpen]);
  /** Details stay open until the tourist deliberately collapses them. */
  const [detailsOpen, setDetailsOpen] = useState(true);
  useEffect(() => {
    if (!startInMoving && hasTrustedFix && !overviewOpen) setMode('moving');
  }, [hasTrustedFix, overviewOpen, startInMoving]);
  /**
   * The panel is NOT closed on entering moving mode. It used to be, to give the
   * map more room — and that unmounted the navigator with it: this panel lives
   * inside the sheet's Radix `Presence`, so closing the sheet took the whole
   * subtree down, and the HUD went with it even though it is portaled to
   * `document.body` (a portal is still part of its parent's React tree).
   * Walking therefore showed a bare map: no turn banner, no stop list, no
   * advance button.
   *
   * The room the walk wanted is already given by the shells instead: while
   * `guiding`, the sheet drops to a compact grab-handle strip (`MOBILE_GUIDE_HEIGHT` /
   * `GUIDE_SHEET_CLASS`) instead of a half-screen panel, and the HUD rides
   * above it on `--sheet-h`. The panel stays mounted and so does the guide.
   */
  /** Keep the details panel collapsed when entering moving mode so the view is clean. */
  useEffect(() => {
    if (mode === 'moving') setDetailsOpen(false);
  }, [mode]);
  const [traveled, setTraveled] = useState(0);
  const [offRoute, setOffRoute] = useState(false);
  const [skippedSuggestions, setSkippedSuggestions] = useState<string[]>([]);
  const voiceMuted = useCommonStore((state) => state.guideVoiceMuted);
  const setGuideVoiceMuted = useCommonStore(
    (state) => state.setGuideVoiceMuted
  );
  const wakeLockRef = useRef<{ release: () => Promise<void> } | null>(null);
  const offRouteFixesRef = useRef(0);
  const spokenThresholdsRef = useRef<SpokenThresholds>(new Map());
  const prevManeuverRef = useRef<VoiceManeuver | null>(null);
  /** Source URLs of nearby POIs already hinted — no repeat spam. */
  const [hintedServices, setHintedServices] = useState<Set<string>>(new Set());

  // ── Ferrostar navigation engine ────────────────────────────────────────
  /** The live session for the current route; null until the WASM core is up. */
  const [ferroNav, setFerroNav] = useState<FerrostarNavigator | null>(null);
  /** The last TripState the session produced — Idle | Navigating | Complete. */
  const [ferroState, setFerroState] = useState<TripState | null>(null);
  /** Route metadata aligned with Ferrostar's remaining step list. */
  const [ferroRoute, setFerroRoute] = useState<FerrostarRouteResult | null>(
    null
  );
  /**
   * Set the moment Ferrostar cannot be used — no WASM core, or a route it will
   * not build. It is the fallback switch: every old effect below it comes back
   * to life, permanently, for the rest of this mount.
   */
  const [ferroFailed, setFerroFailed] = useState(false);
  /** Kept in a ref as well, so unmount can free the session without a dep. */
  const ferroNavRef = useRef<FerrostarNavigator | null>(null);
  /** Consecutive CompletelyOffRoute fixes — the old effect's hysteresis. */
  const ferroOffRouteRef = useRef(0);
  /** The utteranceId we last spoke — Ferrostar hands a new one per trigger. */
  const ferroUtteranceRef = useRef<string | null>(null);

  const routeData = useDirectionsStore((state) => state.results.data);
  const placeDetails = useDirectionsStore((state) => state.placeDetails);
  /** Use the geometry engine when there is no Valhalla route to navigate. */
  const ferrostarActive = routeData != null && !ferroFailed;

  // Nearby POI suggestions along the route, fetched only while guiding.
  const servicesAlong = useServicesAlong(routeData, {
    enabled: mode === 'moving',
    profile: 'pedestrian',
  });

  /**
   * The nearest upcoming service that is close enough ahead on the route,
   * has not been hinted yet, and is not a stop already on the route.
   * Computed from `traveled` (frozen progress) so the hint stays stable.
   */
  const nearbyHint = useMemo<ServiceAlong | null>(() => {
    if (!servicesAlong || servicesAlong.state !== 'ready') return null;
    const upcoming = servicesAlong.items
      .filter(
        (s) =>
          s.along_m > traveled &&
          s.along_m <= traveled + NEARBY_HINT_AHEAD_M &&
          !hintedServices.has(s.source_url)
      )
      .sort((a, b) => a.along_m - b.along_m);
    return upcoming[0] ?? null;
  }, [servicesAlong, traveled, hintedServices]);

  // The route the map draws: one line, one set of manoeuvres.
  const line = useMemo(() => buildLine(routeData), [routeData]);
  const maneuvers = useMemo(
    // Micro-steps merged into the turn that matters; see parts/guide-maneuvers-stub.ts.
    () => mergeMicroManeuvers(buildManeuvers(routeData, line)),
    [routeData, line]
  );
  const summary = routeData?.trip?.summary ?? null;
  /** Metres per second along the route, straight from Valhalla's own numbers. */
  const speed = useMemo(() => {
    if (!summary || !(summary.length > 0) || !(summary.time > 0)) return null;
    return (summary.length * 1000) / summary.time;
  }, [summary]);

  const setVisited = useCallback(
    (id: string, value: boolean) => {
      setProgress((prev) => {
        const has = prev.visited.includes(id);
        if (has === value) return prev;
        const visited = value
          ? [...prev.visited, id]
          : prev.visited.filter((v) => v !== id);
        const next = { ...prev, route: key, visited };
        saveProgress(next);
        return next;
      });
    },
    [key]
  );

  const toggle = useCallback(
    (id: string) => {
      setProgress((prev) => {
        const visited = prev.visited.includes(id)
          ? prev.visited.filter((v) => v !== id)
          : [...prev.visited, id];
        const next = { ...prev, route: key, visited };
        saveProgress(next);
        return next;
      });
    },
    [key]
  );

  const reset = useCallback(() => {
    const fresh = { route: key, visited: [], startedAt: Date.now() };
    setProgress(fresh);
    saveProgress(fresh);
    setTraveled(0);
    offRouteFixesRef.current = 0;
    ferroOffRouteRef.current = 0;
    setOffRoute(false);
  }, [key]);

  const toggleVoiceMute = useCallback(() => {
    setGuideVoiceMuted(!voiceMuted);
  }, [setGuideVoiceMuted, voiceMuted]);

  const stopCount = stops.length;
  // Read once per mount: the flag cannot change while the app runs (it comes
  // from the URL), and a stale `true` would put a lie on screen.
  const simulated = isSimulating();
  const setGuideFix = useCommonStore((s) => s.setGuideFix);

  // ── Geolocation: keep watching as long as we are moving ──────────────────
  useEffect(() => {
    if (stopCount === 0) return;
    const geo = navigator.geolocation;
    if (!geo?.watchPosition) return;
    const watch = geo.watchPosition(
      (pos) => {
        setGeoState('ok');
        const at =
          typeof pos.timestamp === 'number' ? pos.timestamp : Date.now();
        const heading = pos.coords.heading;
        // The position is recorded here and nothing else: what the map is told
        // is published by the single effect further down, which owns the whole
        // fix (position, heading and the route's own course). Publishing from
        // both places meant whichever ran last decided what the map saw — and
        // the course-less half of it won often enough to turn the map's arrow
        // off for a frame on every fix.
        setFix({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy:
            typeof pos.coords.accuracy === 'number'
              ? pos.coords.accuracy
              : null,
          at,
          heading:
            typeof heading === 'number' && Number.isFinite(heading)
              ? heading
              : null,
        });
      },
      () => setGeoState('denied'),
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 15_000 }
    );
    return () => {
      geo.clearWatch?.(watch);
    };
  }, [stopCount]);

  // Staleness only matters while walking: a fix that stops updating must not
  // keep looking like a live position.
  useEffect(() => {
    if (mode !== 'moving') return;
    const timer = window.setInterval(() => setNow(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, [mode]);

  const quality = useMemo<FixQuality>(() => {
    if (geoState === 'denied') return 'unavailable';
    if (geoState === 'idle' || !fix) return 'waiting';
    if (now - fix.at > STALE_FIX_MS) return 'stale';
    if (fix.accuracy != null && fix.accuracy > WEAK_ACCURACY_M) return 'poor';
    return 'good';
  }, [geoState, fix, now]);

  /** Only a good fix earns an exact «через 30 м». */
  const precise = quality === 'good';

  // ── Stops the tourist is standing at count as walked ─────────────────────
  //
  // Derived, never stored: a fix worth trusting completes the stop it is
  // standing on. A weak fix never reaches this branch, so a bad signal cannot
  // silently tick stops off.
  //
  // Every stop is considered, not only the upcoming one: the loop used to break
  // out at the first stop farther than the arrival radius, which meant one stop
  // the tourist never came within 40 m of — a cut corner, a route drawn on the
  // other side of the street, a stop reached in a different order — silenced
  // every stop after it. They would walk right through the rest of the route
  // with the guide stuck on «пройдено 0 из N».
  const effectiveVisited = useMemo(() => {
    const seen = new Set(progress.visited);
    if (precise && fix) {
      const reachedIndex = stops.findIndex(
        (stop) => metresBetween(fix, stop) <= ARRIVAL_RADIUS_M
      );
      if (reachedIndex >= 0) {
        // A single reliable fix can legitimately move the tourist to a later stop
        // without proving every earlier stop was touched in sequence. Preserve the
        // route's current progress, but only advance through neighbouring stops in
        // the order we've already been following; this prevents a late or weak fix
        // from silently jumping a route ahead while still keeping contiguous stops
        // marked when the tourist walks the route normally.
        const lastVisitedIndex = stops.reduce(
          (maxIndex, stop, index) =>
            seen.has(stop.id) ? Math.max(maxIndex, index) : maxIndex,
          -1
        );
        const indicesToMark =
          lastVisitedIndex < 0
            ? [reachedIndex]
            : Array.from(
                { length: reachedIndex - lastVisitedIndex },
                (_, offset) => lastVisitedIndex + 1 + offset
              ).filter((index) => index <= reachedIndex);
        for (const index of indicesToMark) {
          const stop = stops[index];
          if (stop) seen.add(stop.id);
        }
      }
    }
    return stops.filter((s) => seen.has(s.id)).map((s) => s.id);
  }, [progress.visited, stops, fix, precise]);

  // An arrival is remembered, not only shown while the tourist stands there.
  // Without this the guide forgets each stop the moment they walk on, and
  // someone who walked the whole route still reads «пройдено 0 из N» — which is
  // exactly what a replayed walk showed. The gate is the same one the manual
  // fallback relies on (a fix worth trusting, inside the arrival radius), so a
  // weak signal still cannot tick stops off; and marking once is enough, since
  // the stop then lives in the stored progress.
  useEffect(() => {
    if (!precise || !fix) return;
    const reachedIndex = stops.findIndex(
      (stop) => metresBetween(fix, stop) <= ARRIVAL_RADIUS_M
    );
    if (reachedIndex < 0) return;
    const lastVisitedIndex = stops.reduce(
      (maxIndex, stop, index) =>
        progress.visited.includes(stop.id)
          ? Math.max(maxIndex, index)
          : maxIndex,
      -1
    );
    const indicesToMark =
      lastVisitedIndex < 0
        ? [reachedIndex]
        : Array.from(
            { length: reachedIndex - lastVisitedIndex },
            (_, offset) => lastVisitedIndex + 1 + offset
          ).filter((index) => index <= reachedIndex);
    for (const index of indicesToMark) {
      const stop = stops[index];
      if (!stop || progress.visited.includes(stop.id)) continue;
      setVisited(stop.id, true);
    }
  }, [precise, fix, stops, progress.visited, setVisited]);

  // The replayed walk follows the route's own geometry, not straight lines
  // between stops: a position that cuts across blocks is not a walk, and the map
  // drew the dot off the line because that is exactly where the simulation put
  // it. The stops remain the fallback for a route without usable geometry.
  useEffect(() => {
    if (!isSimulating()) return;
    const path: [number, number][] = line
      ? line.points.map((point) => [point.lat, point.lon])
      : stops.map((stop) => [stop.lat, stop.lon]);
    if (path.length > 1) setSimPath(path);
  }, [line, stops]);

  const nextStop = useMemo(
    () => stops.find((s) => !effectiveVisited.includes(s.id)) ?? null,
    [stops, effectiveVisited]
  );

  // Report the walk's progress upward: the history entry of this route is the
  // caller's to keep, and this is the only place that knows how far it got.
  const walked = effectiveVisited.length;
  useEffect(() => {
    onWalked?.({ visited: walked, total: stops.length });
  }, [onWalked, walked, stops.length]);
  // ── Progress along the line, frozen against backward jumps ───────────────
  const located = useMemo(
    () => (fix && line ? locateOnLine(fix, line) : null),
    [fix, line]
  );

  // ── Off route, but only after it persists across fixes ───────────────────
  //
  // Both effects mirror an external stream (the device's GPS fixes) rather than
  // deriving from props, which is exactly what setState-in-effect is for.
  useEffect(() => {
    if (ferrostarActive) return;
    if (!precise || !located) return;
    setTraveled((prev) => Math.max(prev, located.along - BACKWARD_TOLERANCE_M));
  }, [precise, located, ferrostarActive]);

  useEffect(() => {
    if (ferrostarActive) return;
    if (mode !== 'moving' || !precise || !located || !fix) {
      offRouteFixesRef.current = 0;
      setOffRoute(false);
      return;
    }
    // Standing at a POI a few metres off the line is not "off route".
    const atStop = nextStop
      ? metresBetween(fix, nextStop) <= ARRIVAL_RADIUS_M * 2
      : false;
    if (located.offRoute > OFF_ROUTE_M && !atStop) {
      offRouteFixesRef.current += 1;
      if (offRouteFixesRef.current >= OFF_ROUTE_FIXES) setOffRoute(true);
    } else {
      offRouteFixesRef.current = 0;
      setOffRoute(false);
    }
  }, [mode, precise, located, fix, nextStop, ferrostarActive]);

  // ── Active maneuver + remaining line progress ───────────────────────────
  const remainingSteps = useMemo(
    () => (ferrostarActive ? extractRemainingSteps(ferroState) : []),
    [ferrostarActive, ferroState]
  );
  const activeManeuver = useMemo(() => {
    if (!ferrostarActive) {
      return maneuvers.find((m) => m.along > traveled + 5) ?? null;
    }
    const step = remainingSteps[0];
    if (!step || !ferroRoute) return null;
    const index = ferroRoute.route.steps.length - remainingSteps.length;
    const maneuver = ferroRoute.maneuvers[index];
    return maneuver ? { ...maneuver, instruction: step.instruction } : null;
  }, [ferrostarActive, ferroRoute, maneuvers, remainingSteps, traveled]);

  const maneuverDistance = ferrostarActive
    ? precise
      ? extractDistanceToNextManeuver(ferroState)
      : null
    : precise && activeManeuver
      ? Math.max(0, activeManeuver.along - traveled)
      : null;

  /**
   * Hand the turn distance to the map, which uses it as a navigator does: it
   * closes in for the turn and keeps the tourist on screen. Rounded to 10 m,
   * because the camera cannot see a metre and re-rendering the map for one on
   * every fix is how following starts to stutter.
   */
  const setGuideTurnDistanceM = useCommonStore((s) => s.setGuideTurnDistanceM);
  useEffect(() => {
    if (ferrostarActive) return;
    setGuideTurnDistanceM(
      mode === 'moving' && maneuverDistance != null
        ? Math.round(maneuverDistance / 10) * 10
        : null
    );
  }, [mode, maneuverDistance, setGuideTurnDistanceM, ferrostarActive]);

  /**
   * Turn the map by the ROUTE's course, not by the phone's heading.
   *
   * `coords.heading` is the direction the handset points: browsers report it
   * only while the tourist is moving, it wobbles in a hand, and it says nothing
   * about where the route goes. The line always knows — read the bearing from
   * the tourist's position to 25 m further along it — so it is published beside
   * the fix the map already follows. On a route without geometry there is no
   * course, and the map keeps its own heading rather than inventing a turn.
   */
  /**
   * The one publication of the tourist's position to the map.
   *
   * It used to be written from two places — the geolocation watcher and this
   * effect — each with its own idea of the fix, so the last writer decided what
   * the map saw and a course-less half could land on top of a course-bearing
   * one. Everything the map needs is computed here from the same `fix`.
   */
  useEffect(() => {
    if (ferrostarActive) return;
    if (!fix) {
      // No fix any more (the route is gone, or the watch was cleared): the map
      // must stop drawing a dot rather than keep the last one forever.
      setGuideFix(null);
      return;
    }
    setGuideFix({
      lat: fix.lat,
      lng: fix.lon,
      heading: fix.heading,
      // Progress along the route only moves on a trusted fix, so the course
      // falls back to the line's nearest point: «по курсу» has to work the
      // moment the tourist asks for it, not only once the guide has decided
      // they are walking.
      course:
        courseAlongLine(line, traveled) ??
        courseAtPoint(line, fix.lat, fix.lon),
      at: fix.at,
    });
  }, [fix, line, traveled, setGuideFix, ferrostarActive]);

  /**
   * Remember which turn was last announced — on its own, keyed only on the turn
   * itself.
   *
   * It used to be written from inside the announcing effect below, which runs on
   * every fix, every quality change and every language change. Under StrictMode
   * that effect is invoked twice per commit, so the first pass moved the
   * bookkeeping and the second found «the same turn, nothing new» — a turn that
   * had just changed was therefore announced against the previous turn's spoken
   * thresholds and stayed silent for the rest of the walk. Deciding *whether* the
   * turn changed is one thing (here, from `activeManeuver` alone) and deciding
   * *what to say* is another (below); mixing them is what raced.
   */
  useEffect(() => {
    if (ferrostarActive) return;
    if (!isNewManeuver(prevManeuverRef.current, activeManeuver)) return;
    cancelSpeech();
    spokenThresholdsRef.current = new Map();
    prevManeuverRef.current = activeManeuver;
  }, [activeManeuver, ferrostarActive]);

  // ── Voice: announce maneuvers on distance thresholds ────────────────────────────
  useEffect(() => {
    if (ferrostarActive) return;
    if (mode !== 'moving') return;

    const voiceManeuver: VoiceManeuver | null = activeManeuver
      ? { key: activeManeuver.key, instruction: activeManeuver.instruction }
      : null;

    const decision = decideVoice({
      maneuver: voiceManeuver,
      distanceM: maneuverDistance,
      quality,
      offRoute,
      muted: voiceMuted,
      spoken: spokenThresholdsRef.current,
    });

    if (decision.type === 'announce' && activeManeuver) {
      spokenThresholdsRef.current = markSpoken(
        spokenThresholdsRef.current,
        activeManeuver.key,
        decision.threshold
      );
      // The phrase is assembled here, in the interface language, from the
      // maneuver's own instruction (which Valhalla already sends in the route
      // language) — the voice module itself stays free of any language.
      const phrase = decision.arrival
        ? decision.instruction || t('guide.voiceArrived')
        : t('guide.voiceDistance', {
            distance: decision.threshold,
            instruction: decision.instruction,
          }).trim();
      speak(phrase, i18n.language.startsWith('en') ? 'en-US' : 'ru-RU');
    }
  }, [
    mode,
    activeManeuver,
    maneuverDistance,
    quality,
    offRoute,
    voiceMuted,
    t,
    i18n,
    ferrostarActive,
  ]);

  // ── Ferrostar navigation engine ─────────────────────────────────────────
  //
  // The mirror of the five effects above, computed by Ferrostar instead of by
  // this file's geometry helpers. Same four publications, same honesty rules —
  // only the arithmetic moves from `locateOnLine` to a NavigationSession:
  //
  //   old effect                        →  here
  //   ───────────────────────────────     ────────────────────────────────────
  //   setTraveled (locateOnLine.along)  →  line.total − distanceRemaining
  //   setOffRoute (offRoute > 60 m)     →  isCompletelyOffRoute, 2 fixes running
  //   setGuideTurnDistanceM (10 m steps)→  distanceToNextManeuver, 10 m steps
  //   setGuideFix course (line bearing) →  extractCourse of the snapped location
  //   decideVoice (thresholds 0/10/30/80)→ Ferrostar's own utteranceId triggers
  //
  // Everything the engine cannot answer (an Idle or
  // Complete state, a session that died) leaves the value it had — the panel
  // freezes rather than inventing progress. And if Ferrostar is not available
  // at all, `ferroFailed` sends `ferrostarActive` back to false and every old
  // effect above comes back to life for good.

  /** Length of the line the map draws — the denominator for distanceRemaining. */
  const lineTotal = line?.total ?? 0;

  /**
   * Build a session when movement starts, once the WASM core is up.
   *
   * Recreated whenever movement starts or the route changes — the session is
   * bound to the route it was constructed with. A failure hands navigation to
   * the panel's geometry fallback for the rest of this mount.
   */
  useEffect(() => {
    ferroNavRef.current?.destroy();
    ferroNavRef.current = null;
    setFerroNav(null);
    setFerroRoute(null);
    setFerroState(null);
    ferroUtteranceRef.current = null;
    ferroOffRouteRef.current = 0;
    if (!ferrostarActive || mode !== 'moving' || !routeData) return;

    let cancelled = false;
    let createdNav: FerrostarNavigator | null = null;
    void ferrostarReady
      .then((mod) => {
        if (cancelled) return;
        if (!mod) {
          setFerroFailed(true);
          return;
        }
        try {
          const built = buildFerrostarRoute(routeData);
          if (!built) {
            console.warn(
              'Ferrostar could not build this route; using guide fallback.'
            );
            setFerroFailed(true);
            return;
          }
          const nav = new FerrostarNavigator(built.route, mod);
          createdNav = nav;
          ferroNavRef.current = nav;
          setFerroRoute(built);
          ferroOffRouteRef.current = 0;
          setFerroNav(nav);
        } catch (error) {
          console.warn(
            'Ferrostar session could not start; using guide fallback.',
            error
          );
          setFerroFailed(true);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        console.warn(
          'Ferrostar initialization failed; using guide fallback.',
          error
        );
        setFerroFailed(true);
      });
    return () => {
      cancelled = true;
      createdNav?.destroy();
      if (ferroNavRef.current === createdNav) ferroNavRef.current = null;
    };
  }, [mode, routeData, ferrostarActive]);

  /** Release the WASM session when the guide goes away — see ferrostar-nav.ts. */
  useEffect(
    () => () => {
      ferroNavRef.current?.destroy();
      ferroNavRef.current = null;
    },
    []
  );

  /**
   * Every fix through the session, and the four publications the old effects
   * made. Mirroring an external stream (the GPS) is what setState-in-effect is
   * for; the session itself is stateful and must see the fixes in order, which
   * is why this is an effect and not a render-time call.
   *
   * The map's fix and its course go out on *every* fix (the old course effect
   * did not wait for a trustworthy one either) — a tourist who asks «по курсу»
   * must get an answer immediately. Progress and off-route keep the old
   * `precise` gate: a weak fix must not move the walk forward.
   */
  useEffect(() => {
    if (!ferrostarActive) return;
    const nav = ferroNav;
    if (mode !== 'moving' || !nav) return;
    if (!fix) {
      setGuideFix(null);
      return;
    }

    const state = nav.update({
      lat: fix.lat,
      lon: fix.lon,
      accuracy: fix.accuracy,
      at: fix.at,
    });
    if (!state) {
      setFerroFailed(true);
      return;
    }
    setFerroState(state);

    setGuideFix({
      lat: fix.lat,
      lng: fix.lon,
      heading: null,
      course: extractCourse(state),
      at: fix.at,
    });

    const toManeuver = extractDistanceToNextManeuver(state);
    setGuideTurnDistanceM(
      toManeuver == null ? null : Math.round(toManeuver / 10) * 10
    );

    if (!precise) {
      ferroOffRouteRef.current = 0;
      setOffRoute(false);
      return;
    }

    const remaining = extractDistanceRemaining(state);
    if (remaining != null && line) {
      const along = Math.max(0, lineTotal - remaining - BACKWARD_TOLERANCE_M);
      setTraveled((prev) => Math.max(prev, along));
    }

    const off = isCompletelyOffRoute(state);
    // Standing at a POI a few metres off the line is not "off route" — the same
    // allowance the old effect made.
    const atStop = nextStop
      ? metresBetween(fix, nextStop) <= ARRIVAL_RADIUS_M * 2
      : false;
    if (mode === 'moving' && off === true && !atStop) {
      ferroOffRouteRef.current += 1;
      if (ferroOffRouteRef.current >= OFF_ROUTE_FIXES) setOffRoute(true);
    } else {
      // `off === null` (Idle / Complete) is «unknown», so it counts as on
      // route rather than as an off-route report the tourist cannot act on.
      ferroOffRouteRef.current = 0;
      setOffRoute(false);
    }
  }, [
    ferroNav,
    ferrostarActive,
    fix,
    precise,
    mode,
    line,
    lineTotal,
    nextStop,
    setGuideFix,
    setGuideTurnDistanceM,
  ]);

  /** Off moving mode the camera has nothing to close in on. */
  useEffect(() => {
    if (!ferrostarActive) return;
    if (mode !== 'moving') setGuideTurnDistanceM(null);
  }, [mode, setGuideTurnDistanceM, ferrostarActive]);

  /**
   * Voice, driven by Ferrostar's own triggers instead of our thresholds.
   *
   * Ferrostar decides *when*: it carries one utterance per trigger distance
   * (400 / 200 / 50 / 0 m) with a stable `utteranceId`, and announces it once
   * the tourist crosses that band. This effect decides *whether* and *what*,
   * by handing the fresh utterance to the trigger-aware voice gate
   * — so the mute switch, the «only on a good fix» rule and the off-route
   * silence still apply, and the phrase is still assembled in the interface
   * language. Ferrostar's trigger distance is not reinterpreted as a legacy
   * distance band.
   */
  useEffect(() => {
    if (!ferrostarActive) return;
    if (mode !== 'moving' || !ferroState) return;

    const spoken = extractSpokenInstruction(ferroState);
    const utteranceId = spoken?.utteranceId;
    if (!spoken || !utteranceId || utteranceId === ferroUtteranceRef.current) {
      return;
    }

    const decision = decideTriggeredVoice({
      instruction: spoken.text,
      distanceM: spoken.triggerDistanceBeforeManeuver,
      quality,
      offRoute,
      muted: voiceMuted,
    });
    if (decision.type !== 'announce') return;

    ferroUtteranceRef.current = utteranceId;
    const phrase = t('guide.voiceDistance', {
      distance: decision.distanceM,
      instruction: decision.instruction,
    }).trim();
    speak(phrase, i18n.language.startsWith('en') ? 'en-US' : 'ru-RU');
  }, [
    ferroState,
    ferrostarActive,
    mode,
    quality,
    offRoute,
    voiceMuted,
    t,
    i18n,
  ]);

  const nextAlong = useMemo(() => {
    if (!line || !nextStop) return null;
    return locateOnLine(nextStop, line).along;
  }, [line, nextStop]);

  const toNextMetres = nextStop
    ? precise && fix
      ? metresBetween(fix, nextStop)
      : null
    : null;

  /** Line-based distance still ahead — falls back to the straight line. */
  const remainingToNext =
    nextAlong != null
      ? Math.max(0, nextAlong - traveled)
      : precise && fix && nextStop
        ? metresBetween(fix, nextStop)
        : null;

  const walkSeconds =
    speed && remainingToNext != null ? remainingToNext / speed : null;

  const metresDone = line ? Math.min(traveled, line.total) : null;
  const metresTotal = line ? line.total : null;

  const done = effectiveVisited.length;
  const minutesLeft = stops
    .filter((s) => !effectiveVisited.includes(s.id))
    .reduce((sum, s) => sum + (visitMinutesOf(s) ?? 0), 0);
  const totalVisitLeft = minutesLeft;
  const remainingMinutes = useMemo(() => {
    if (walkSeconds == null && totalVisitLeft === 0) return null;
    const walk = walkSeconds != null ? walkSeconds / 60 : 0;
    return Math.round(walk + totalVisitLeft);
  }, [walkSeconds, totalVisitLeft]);

  // ── The announcement, throttled: a new turn, or the distance in 50 m steps.
  // Derived, so re-renders that change nothing announce nothing.
  const announcement = !activeManeuver
    ? ''
    : precise && maneuverDistance != null
      ? t('guide.announceIn', {
          distance: fmtDist(Math.round(maneuverDistance / 50) * 50),
          instruction: activeManeuver.instruction,
        })
      : activeManeuver.instruction;

  // ── Re-plan from where the tourist stands, keeping every stop ────────────
  //
  // The panel does not fetch routes: it moves the «моё местоположение» start to
  // the current fix (so the next plan begins here) and asks the integration
  // layer to re-plan — either through the `onReroute` prop or, when nothing is
  // wired, by emitting `grodno:guide-reroute` with the position. Either way the
  // stops (incl. mandatory ones) are left untouched.
  const reroute = useCallback(() => {
    const store = useDirectionsStore.getState();
    if (fix) {
      const mine = meWaypoint(fix.lat, fix.lon, t);
      const hasMe = store.waypoints.some((w) => w.id === ME_WAYPOINT_ID);
      store.setWaypoint(
        hasMe
          ? store.waypoints.map((w) => (w.id === ME_WAYPOINT_ID ? mine : w))
          : [mine, ...store.waypoints]
      );
    }
    setTraveled(0);
    offRouteFixesRef.current = 0;
    ferroOffRouteRef.current = 0;
    setOffRoute(false);
    onReroute?.();
    try {
      window.dispatchEvent(
        new CustomEvent('grodno:guide-reroute', {
          detail: fix ? { lat: fix.lat, lon: fix.lon } : null,
        })
      );
    } catch {
      // No CustomEvent (a bare render) — the store update above already moved
      // the start of the next plan.
    }
  }, [onReroute, fix, t]);

  const wasHiddenRef = useRef(false);

  // Keep the screen awake while walking; browsers may refuse — that is fine.
  // The lock is re-acquired on every return from background (visibilitychange →
  // visible) because the browser releases it automatically when the tab hides: a
  // lock nobody re-requests means the screen goes dark mid-walk, which is exactly
  // when the tourist needs it lit.
  useEffect(() => {
    if (mode !== 'moving') return;

    let cancelled = false;

    const acquire = () => {
      if (cancelled) return;
      const nav = navigator as Navigator & {
        wakeLock?: {
          request: (
            type: 'screen'
          ) => Promise<{ release: () => Promise<void> }>;
        };
      };
      nav.wakeLock
        ?.request('screen')
        .then((lock) => {
          if (cancelled) {
            void lock.release().catch(() => undefined);
            return;
          }
          wakeLockRef.current = lock;
        })
        .catch(() => undefined);
    };

    acquire();

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        acquire();
        // A ref, not state: the flag must be readable from the listener that is
        // ALREADY registered. In state it re-created the listener on every hide,
        // and the closure a test dispatches still read the value from the render
        // that created it — so the toast never fired.
        if (wasHiddenRef.current) {
          wasHiddenRef.current = false;
          toast(t('guide.continuingNavigation'), { duration: 2000 });
        }
      } else {
        // Release the lock now rather than waiting for garbage collection:
        // the browser releases it anyway when the tab goes hidden, and keeping a
        // dangling reference prevents re-acquisition in some browsers.
        wasHiddenRef.current = true;
        void wakeLockRef.current?.release().catch(() => undefined);
        wakeLockRef.current = null;
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', handleVisibility);
      void wakeLockRef.current?.release().catch(() => undefined);
      wakeLockRef.current = null;
    };
  }, [mode, t]);

  const activeSuggestions = suggestions.filter(
    (s) => !skippedSuggestions.includes(s.id)
  );

  if (stops.length === 0) {
    return (
      <section data-testid="guide-panel" className="flex flex-col gap-3">
        <GuideHeader
          onReset={reset}
          onExit={onExit}
          voiceMuted={voiceMuted}
          onVoiceMuteToggle={toggleVoiceMute}
        />
        <SimulatedBadge active={simulated} />
        <GuideEmpty />
      </section>
    );
  }

  if (mode === 'moving') {
    const ManeuverIcon = activeManeuver
      ? getManeuverIcon(activeManeuver.type)
      : Footprints;
    return (
      <FerrostarNavigationHud
        maneuverFallback={
          <ManeuverBanner
            instruction={
              activeManeuver?.instruction ??
              (nextStop
                ? t('guide.goToStop', {
                    imperative: travel.imperative,
                    name: nextStop.name,
                  })
                : t('guide.followRoute', {
                    imperative: travel.imperative,
                  }))
            }
            Icon={ManeuverIcon}
            distance={maneuverDistance}
            precise={precise}
            quality={quality}
          />
        }
        alerts={
          <>
            {nearbyHint && (
              <NearbyHint
                service={nearbyHint}
                distanceAhead={nearbyHint.along_m - traveled}
                onDismiss={() => {
                  setHintedServices(
                    (prev) => new Set([...prev, nearbyHint.source_url])
                  );
                }}
              />
            )}
            {offRoute && (
              <OffRoutePrompt
                metres={located ? Math.round(located.offRoute) : null}
                onReroute={reroute}
                onDismiss={() => {
                  offRouteFixesRef.current = 0;
                  ferroOffRouteRef.current = 0;
                  setOffRoute(false);
                }}
              />
            )}
          </>
        }
        announcement={announcement}
        onAdvance={() => {
          if (!nextStop) return;
          setVisited(nextStop.id, true);
          const state = ferroNav?.state;
          if (ferrostarActive && state && 'Navigating' in state) {
            ferroNav.advanceToNextStep();
            setFerroState(ferroNav.state);
          }
        }}
        // The panel is NOT closed on entering moving mode, and the HUD cannot
        // close it either: this subtree lives inside Radix `Presence`, so
        // closing the sheet unmounts the guide and takes the portaled HUD with
        // it — a bare map, no navigator. The panel hides itself instead (see
        // GUIDE_SHEET_CLASS in sidebar.tsx) and the way out moved into the HUD.
        onExit={onExit}
        onVoiceToggle={toggleVoiceMute}
        onDetailsToggle={() => setDetailsOpen((o) => !o)}
        voiceMuted={voiceMuted}
        advanceDisabled={!nextStop}
        detailsToggleLabel={t('guide.detailsToggle', {
          visited: done,
          total: stops.length,
        })}
        detailsOpen={detailsOpen}
        totalStops={stops.length}
        progressDone={done}
        progressMinutesLeft={minutesLeft}
        progressMetresDone={metresDone}
        progressMetresTotal={metresTotal}
        progressRemainingMinutes={remainingMinutes}
        stopListItems={listStops}
        stopListVisited={effectiveVisited}
        stopListNextId={nextStop?.id ?? null}
        stopListNextDistance={toNextMetres}
        onStopListToggle={toggle}
        onStopListVisitMinutesChange={setStopVisitMinutes}
        suggestions={activeSuggestions}
        onAddSuggestion={onAddSuggestion}
        onSkipSuggestion={(id) =>
          setSkippedSuggestions((prev) => [...prev, id])
        }
        nextStopName={nextStop?.name ?? null}
        nextStopId={nextStop?.id ?? null}
        nextPlaceDetails={
          nextStop?.placeId != null
            ? (placeDetails[nextStop.placeId] ?? null)
            : null
        }
      />
    );
  }

  return (
    <section
      data-testid="guide-panel"
      data-mode="review"
      className="flex flex-col gap-3"
    >
      <GuideHeader
        onReset={reset}
        onExit={onExit}
        voiceMuted={voiceMuted}
        onVoiceMuteToggle={toggleVoiceMute}
      />
      <SimulatedBadge active={simulated} />

      <GuideProgress
        done={done}
        total={stops.length}
        minutesLeft={minutesLeft}
        metresDone={metresDone}
        metresTotal={metresTotal}
        remainingMinutes={remainingMinutes}
        mode={travel}
      />

      <GuideStopList
        stops={listStops}
        visited={effectiveVisited}
        nextId={nextStop?.id ?? null}
        nextDistance={toNextMetres}
        onToggle={toggle}
        onVisitMinutesChange={setStopVisitMinutes}
      />

      <button
        type="button"
        data-testid="guide-start"
        onClick={() => setMode('moving')}
        disabled={!nextStop}
        className="flex h-12 items-center justify-center gap-2 rounded-xl bg-primary font-semibold text-primary-foreground transition hover:brightness-[0.97] active:scale-[0.99] disabled:opacity-40"
      >
        <Play className="h-4 w-4" />
        {t('guide.start')}
      </button>
    </section>
  );
};

// ── Parts local to the panel ────────────────────────────────────────────────

interface ManeuverBannerProps {
  instruction: string;
  Icon: ReturnType<typeof getManeuverIcon>;
  distance: number | null;
  precise: boolean;
  quality: FixQuality;
}

/**
 * The big arrow: what to do next, and how far — but only when the fix can
 * carry a number. A weak signal gets the instruction without the metres.
 *
 * Layout: distance (large, top) + instruction (medium, below) on mobile.
 * The distance is the one thing that must be readable at a glance while walking.
 */
const ManeuverBanner = ({
  instruction,
  Icon,
  distance,
  precise,
  quality,
}: ManeuverBannerProps) => {
  const { t } = useTranslation();

  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-float">
      <div className="flex items-start gap-3">
        <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground md:size-12">
          <Icon className="size-7 md:size-6" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          {precise && distance != null ? (
            <>
              {/* Distance — the dominant number while walking. */}
              <div
                data-testid="guide-maneuver-distance"
                className="text-2xl font-bold leading-none tracking-tight text-primary md:text-xl"
              >
                {t('guide.turnInAheadStandalone', {
                  distance: fmtDist(distance),
                })}
              </div>
              {/* Instruction — what to do. */}
              <div
                data-testid="guide-maneuver-instruction"
                className="mt-0.5 text-base font-semibold leading-tight text-foreground md:text-body"
              >
                {instruction}
              </div>
            </>
          ) : (
            <>
              <div
                data-testid="guide-maneuver-instruction"
                className="text-body font-semibold leading-tight"
              >
                {instruction}
              </div>
              <div className="mt-1 text-label text-muted-foreground">
                <span data-testid="guide-maneuver-unprecise">
                  {quality === 'unavailable'
                    ? t('guide.noSignal')
                    : t('guide.distanceHidden')}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

interface OffRoutePromptProps {
  metres: number | null;
  onReroute: () => void;
  onDismiss: () => void;
}

/** Offered when the tourist has clearly left the line — never silently. */
const OffRoutePrompt = ({
  metres,
  onReroute,
  onDismiss,
}: OffRoutePromptProps) => {
  const { t } = useTranslation();

  return (
    <div
      data-testid="guide-off-route"
      role="alert"
      className="rounded-2xl border border-border bg-card p-4 shadow-card"
    >
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <TriangleAlert className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-body font-semibold leading-tight">
            {t('guide.offRouteTitle')}
          </div>
          <p className="mt-0.5 text-label text-muted-foreground">
            {metres != null
              ? `${t('guide.offRouteAt', { distance: fmtDist(metres) })} `
              : ''}
            {t('guide.offRouteBody')}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="guide-reroute"
              onClick={onReroute}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-body font-semibold text-primary-foreground transition hover:brightness-[0.97] active:scale-[0.99]"
            >
              <Navigation className="h-4 w-4" />
              {t('guide.reroute')}
            </button>
            <button
              type="button"
              data-testid="guide-on-route"
              onClick={onDismiss}
              className="inline-flex h-10 items-center justify-center rounded-xl px-4 text-body text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {t('guide.onRoute')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/** Category name for the nearby POI hint, keyed through i18n. */
const nearbyServiceName = (category: string, t: (key: string) => string) => {
  const MAP: Record<string, string> = {
    туалет: t('guide.nearbyToilet'),
    кафе: t('guide.nearbyCafe'),
    ресторан: t('guide.nearbyRestaurant'),
    гостиница: t('guide.nearbyHotel'),
    'остановка транспорта': t('guide.nearbyBusStop'),
  };
  return MAP[category] ?? t('guide.nearbyGeneric');
};

interface NearbyHintProps {
  service: ServiceAlong;
  distanceAhead: number;
  onDismiss: () => void;
}

/**
 * A navigator-style inline hint: «кафе в 40 м по пути».
 * Shown only when a POI is within `NEARBY_HINT_AHEAD_M` ahead on the route,
 * dismissed once and never repeated for the same source URL.
 */
const NearbyHint = ({ service, distanceAhead, onDismiss }: NearbyHintProps) => {
  const { t } = useTranslation();

  const ServiceIcon =
    {
      кафе: Coffee,
      ресторан: UtensilsCrossed,
      гостиница: BedDouble,
      туалет: Bus,
      'остановка транспорта': Bus,
    }[service.category] ?? Bus;

  return (
    <div
      data-testid="guide-nearby-hint"
      className="rounded-2xl border border-border bg-card p-3 shadow-card"
    >
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400">
          <ServiceIcon className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <span className="text-body font-medium">
            {t('guide.nearbyService', {
              name: nearbyServiceName(service.category, t),
              distance: fmtDist(distanceAhead),
            })}
          </span>
          <span className="ml-1.5 text-body text-muted-foreground">
            — {service.name}
          </span>
        </div>
        <button
          type="button"
          data-testid="guide-nearby-hint-dismiss"
          onClick={onDismiss}
          className="shrink-0 rounded-full p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label={t('guide.suggestionSkip')}
        >
          <span className="sr-only">{t('guide.suggestionSkip')}</span>
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M10.5 3.5L3.5 10.5M3.5 3.5L10.5 10.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
};

interface GuideHeaderProps {
  onReset: () => void;
  onExit: () => void;
  voiceMuted: boolean;
  onVoiceMuteToggle: () => void;
}

/**
 * Panel title + the two quiet controls (sound, reset), kept quiet on purpose.
 *
 * The row must fit a 390px phone: at that width the old fixed row pushed the
 * reset button past the right edge (x=298 w=160 → 458 on a 390 viewport) and it
 * was clipped. The row now wraps instead of overflowing, the title block may
 * shrink (`min-w-0` + `truncate`) so it never forces the controls out, the
 * control group never shrinks (`shrink-0`), and the labels are compact («сбросить»,
 * not «сбросить прогресс», and `px-2.5` instead of `px-3`). If a long language
 * still cannot fit both on one line, the group drops to its own line rather than
 * running off screen — no button ever leaves the viewport or overlaps another.
 */
const GuideHeader = ({
  onReset,
  onExit,
  voiceMuted,
  onVoiceMuteToggle,
}: GuideHeaderProps) => {
  const { t } = useTranslation();

  return (
    <div
      data-testid="guide-header"
      className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Footprints className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="truncate text-body font-semibold leading-tight">
            {t('guide.title')}
          </div>
          <div className="truncate text-meta text-muted-foreground">
            {t('guide.subtitle')}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          data-testid="guide-exit-overview"
          onClick={onExit}
          title={t('guide.exit')}
          aria-label={t('guide.exit')}
          className="inline-flex size-9 shrink-0 items-center justify-center rounded-full text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <XIcon className="size-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={onVoiceMuteToggle}
          title={voiceMuted ? t('guide.enableSound') : t('guide.disableSound')}
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {voiceMuted ? (
            <VolumeX className="h-3.5 w-3.5" />
          ) : (
            <Volume2 className="h-3.5 w-3.5" />
          )}
          {voiceMuted ? t('guide.soundOff') : t('guide.soundOn')}
        </button>
        <button
          type="button"
          onClick={onReset}
          title={t('guide.resetTitle')}
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {t('guide.reset')}
        </button>
      </div>
    </div>
  );
};
