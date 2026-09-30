import {
  Flag,
  Footprints,
  LocateFixed,
  MapPin,
  Navigation,
  Play,
  RotateCcw,
  TriangleAlert,
  Volume2,
  VolumeX,
  WifiOff,
} from 'lucide-react';
import type { TFunction } from 'i18next';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  ActiveWaypoint,
  ParsedDirectionsGeometry,
} from '@/components/types';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';
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
import {
  cancelSpeech,
  decideVoice,
  isNewManeuver,
  markSpoken,
  speak,
  type SpokenThresholds,
  type VoiceManeuver,
} from '@/lib/guide-voice';
import { GuideEmpty } from './parts/guide-empty';
import { fmtDist, metresBetween } from './parts/guide-format';
import { guideModeFor } from './parts/guide-mode';
import { GuideNextStop } from './parts/guide-next-stop';
import { GuideProgress } from './parts/guide-progress';
import { GuideRouteDone } from './parts/guide-route-done';
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
}

const STORAGE_KEY = 'grodno-guide-progress';
const VOICE_MUTE_KEY = 'grodno-voice-muted';
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

const mapsUrl = (lat: number, lon: number) =>
  `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;

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
}

type FixQuality = 'unavailable' | 'waiting' | 'stale' | 'poor' | 'good';

interface LineGeometry {
  points: LatLon[];
  /** Cumulative metres at each vertex. */
  cum: number[];
  total: number;
}

interface GuideManeuver {
  key: string;
  type: number;
  instruction: string;
  /** Metres from the start of the line to the manoeuvre's begin point. */
  along: number;
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

/** «14:35» in the device's local time — ETA is an estimate, so minutes only. */
const fmtClock = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
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
  onReroute,
  suggestions = [],
  onAddSuggestion,
  transport = null,
  onWalked,
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
  const [mode, setMode] = useState<'review' | 'moving'>('review');
  const [fix, setFix] = useState<Fix | null>(null);
  const [geoState, setGeoState] = useState<'idle' | 'ok' | 'denied'>(() =>
    typeof navigator === 'undefined' || !('geolocation' in navigator)
      ? 'denied'
      : 'idle'
  );
  const [now, setNow] = useState(() => Date.now());
  const [traveled, setTraveled] = useState(0);
  const [offRoute, setOffRoute] = useState(false);
  const [skippedSuggestions, setSkippedSuggestions] = useState<string[]>([]);
  const [voiceMuted, setVoiceMuted] = useState<boolean>(() => {
    try {
      return localStorage.getItem(VOICE_MUTE_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const wakeLockRef = useRef<{ release: () => Promise<void> } | null>(null);
  const offRouteFixesRef = useRef(0);
  const spokenThresholdsRef = useRef<SpokenThresholds>(new Map());
  const prevManeuverRef = useRef<VoiceManeuver | null>(null);

  const routeData = useDirectionsStore((state) => state.results.data);

  // The route the map draws: one line, one set of manoeuvres.
  const line = useMemo(() => buildLine(routeData), [routeData]);
  const maneuvers = useMemo(
    () => buildManeuvers(routeData, line),
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
    setOffRoute(false);
  }, [key]);

  const toggleVoiceMute = useCallback(() => {
    setVoiceMuted((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(VOICE_MUTE_KEY, String(next));
      } catch {
        // storage unavailable — value stays in memory for this session
      }
      return next;
    });
  }, []);

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
        setFix({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy:
            typeof pos.coords.accuracy === 'number'
              ? pos.coords.accuracy
              : null,
          at,
        });
        // The map follows this: the guide is a navigator, not a list next to a
        // still map the tourist has to find themselves on.
        const heading = pos.coords.heading;
        setGuideFix({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          heading:
            typeof heading === 'number' && Number.isFinite(heading)
              ? heading
              : null,
          at,
        });
      },
      () => setGeoState('denied'),
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 15_000 }
    );
    return () => {
      geo.clearWatch?.(watch);
      setGuideFix(null);
    };
  }, [stopCount, setGuideFix]);

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
      for (const stop of stops) {
        if (seen.has(stop.id)) continue;
        if (metresBetween(fix, stop) > ARRIVAL_RADIUS_M) continue;
        seen.add(stop.id);
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
    const withinReach = stops.find(
      (stop) =>
        !progress.visited.includes(stop.id) &&
        metresBetween(fix, stop) <= ARRIVAL_RADIUS_M
    );
    if (withinReach) setVisited(withinReach.id, true);
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
  const nextIndex = useMemo(
    () => (nextStop ? stops.findIndex((s) => s.id === nextStop.id) : -1),
    [stops, nextStop]
  );

  // ── Progress along the line, frozen against backward jumps ───────────────
  const located = useMemo(
    () => (fix && line ? locateOnLine(fix, line) : null),
    [fix, line]
  );

  // ── Off route, but only after it persists across fixes ───────────────────
  //
  // Both effects mirror an external stream (the device's GPS fixes) rather than
  // deriving from props, which is exactly what setState-in-effect is for.
  /* eslint-disable react-hooks/set-state-in-effect -- GPS-derived state, not derived-from-render state */
  useEffect(() => {
    if (!precise || !located) return;
    setTraveled((prev) => Math.max(prev, located.along - BACKWARD_TOLERANCE_M));
  }, [precise, located]);

  useEffect(() => {
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
  }, [mode, precise, located, fix, nextStop]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ── Active manoeuvre + remaining line progress ───────────────────────────
  // The turn ahead of the (frozen) progress point; a weak fix never advances
  // this, because `traveled` only moves on a trusted fix.
  const activeManeuver = useMemo(
    () => maneuvers.find((m) => m.along > traveled + 5) ?? null,
    [maneuvers, traveled]
  );

  const maneuverDistance =
    precise && activeManeuver
      ? Math.max(0, activeManeuver.along - traveled)
      : null;

  // ── Voice: announce maneuvers on distance thresholds ────────────────────────────
  useEffect(() => {
    if (mode !== 'moving') return;

    // When the maneuver changes, cancel anything in progress and reset spoken
    // thresholds so the new maneuver starts from scratch.
    if (isNewManeuver(prevManeuverRef.current, activeManeuver)) {
      cancelSpeech();
      spokenThresholdsRef.current = new Map();
      prevManeuverRef.current = activeManeuver;
    }

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
  // Travel time, not walking time: `speed` is Valhalla's own for this route, so
  // on a bike or in a car this is already the right number.
  const travelMinutes =
    walkSeconds != null && walkSeconds > 0
      ? Math.max(1, Math.round(walkSeconds / 60))
      : null;

  // ETA is a pure function of the ticking clock (`now`) and the route's own
  // speed — no clock read during render, no state of its own.
  const etaLabel =
    walkSeconds != null && precise
      ? `≈ ${fmtClock(now + walkSeconds * 1000)}`
      : null;

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

  const geoLine = useMemo(() => {
    if (quality === 'unavailable') return t('guide.geoUnavailable');
    if (quality === 'waiting') return t('guide.geoLocating');
    if (quality === 'stale') return t('guide.geoStale');
    if (quality === 'poor')
      return t('guide.geoPoor', { metres: Math.round(fix?.accuracy ?? 0) });
    return toNextMetres != null
      ? t('guide.geoDistanceToNext', { distance: fmtDist(toNextMetres) })
      : t('guide.geoOnRoute');
  }, [quality, fix, toNextMetres, t]);

  const activeSuggestions = suggestions.filter(
    (s) => !skippedSuggestions.includes(s.id)
  );

  if (stops.length === 0) {
    return (
      <section data-testid="guide-panel" className="flex flex-col gap-3">
        <GuideHeader
          onReset={reset}
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
      <section
        data-testid="guide-panel"
        data-mode="moving"
        className="flex min-h-full flex-col gap-3"
      >
        <SimulatedBadge active={simulated} />
        {/* The turn the tourist is walking into — the one big thing on screen. */}
        <ManeuverBanner
          instruction={
            activeManeuver?.instruction ??
            (nextStop
              ? t('guide.goToStop', {
                  imperative: travel.imperative,
                  name: nextStop.name,
                })
              : t('guide.followRoute', { imperative: travel.imperative }))
          }
          Icon={ManeuverIcon}
          distance={maneuverDistance}
          precise={precise}
          quality={quality}
        />

        {offRoute && (
          <OffRoutePrompt
            metres={located ? Math.round(located.offRoute) : null}
            onReroute={reroute}
            onDismiss={() => {
              offRouteFixesRef.current = 0;
              setOffRoute(false);
            }}
          />
        )}

        {nextStop ? (
          <GuideNextStop
            key={nextStop.id}
            number={nextIndex + 1}
            name={nextStop.name}
            category={nextStop.category ?? null}
            visitMinutes={visitMinutesOf(nextStop)}
            visitOverride={visitOverrides[nextStop.id] ?? null}
            estimateMinutes={nextStop.visitMinutes ?? null}
            onVisitMinutesChange={(minutes) =>
              setStopVisitMinutes(nextStop.id, minutes)
            }
            distance={toNextMetres}
            travelMinutes={travelMinutes}
            etaLabel={etaLabel}
            mode={travel}
            mapsHref={mapsUrl(nextStop.lat, nextStop.lon)}
          />
        ) : (
          <GuideRouteDone total={stops.length} />
        )}

        <GuideProgress
          done={done}
          total={stops.length}
          minutesLeft={minutesLeft}
          metresDone={metresDone}
          metresTotal={metresTotal}
          remainingMinutes={remainingMinutes}
          mode={travel}
        />

        <p
          data-testid="guide-geo-status"
          className="flex items-center gap-1.5 text-meta text-muted-foreground"
        >
          <QualityIcon quality={quality} />
          {geoLine}
        </p>

        <div className="flex gap-2">
          <button
            type="button"
            data-testid="guide-advance"
            onClick={() => nextStop && setVisited(nextStop.id, true)}
            disabled={!nextStop}
            className="flex h-12 flex-1 items-center justify-center gap-2 rounded-xl bg-primary font-semibold text-primary-foreground transition hover:brightness-[0.97] active:scale-[0.99] disabled:opacity-40"
          >
            <MapPin className="h-4 w-4" />
            {t('guide.advance')}
          </button>
          <button
            type="button"
            data-testid="guide-finish"
            onClick={() => setMode('review')}
            className="flex h-12 items-center justify-center gap-2 rounded-xl bg-secondary px-4 font-semibold text-secondary-foreground transition hover:brightness-[0.97] active:scale-[0.99]"
          >
            <Flag className="h-4 w-4" />
            {t('guide.finish')}
          </button>
        </div>

        {activeSuggestions.length > 0 && (
          <SuggestionList
            suggestions={activeSuggestions}
            onAdd={(id) => onAddSuggestion?.(id)}
            onSkip={(id) => setSkippedSuggestions((prev) => [...prev, id])}
          />
        )}

        <GuideStopList
          stops={listStops}
          visited={effectiveVisited}
          nextId={nextStop?.id ?? null}
          nextDistance={toNextMetres}
          onToggle={toggle}
          onVisitMinutesChange={setStopVisitMinutes}
          collapsible
          defaultOpen={false}
        />

        <p aria-live="polite" role="status" className="sr-only">
          {announcement}
        </p>
      </section>
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
        voiceMuted={voiceMuted}
        onVoiceMuteToggle={toggleVoiceMute}
      />
      <SimulatedBadge active={simulated} />

      {/* The next stop, or a quiet «all done» card once there is none. */}
      {nextStop ? (
        // key: remounting on a new stop replays the small fade+slide instead of
        // swapping the text in place (DESIGN.md, Motion).
        <GuideNextStop
          key={nextStop.id}
          number={nextIndex + 1}
          name={nextStop.name}
          category={nextStop.category ?? null}
          visitMinutes={visitMinutesOf(nextStop)}
          visitOverride={visitOverrides[nextStop.id] ?? null}
          estimateMinutes={nextStop.visitMinutes ?? null}
          onVisitMinutesChange={(minutes) =>
            setStopVisitMinutes(nextStop.id, minutes)
          }
          distance={toNextMetres}
          travelMinutes={travelMinutes}
          etaLabel={etaLabel}
          mode={travel}
          mapsHref={mapsUrl(nextStop.lat, nextStop.lon)}
        />
      ) : (
        <GuideRouteDone total={stops.length} />
      )}

      <GuideProgress
        done={done}
        total={stops.length}
        minutesLeft={minutesLeft}
        metresDone={metresDone}
        metresTotal={metresTotal}
        remainingMinutes={remainingMinutes}
        mode={travel}
      />

      <p
        data-testid="guide-geo-status"
        className="flex items-center gap-1.5 text-meta text-muted-foreground"
      >
        <QualityIcon quality={quality} />
        {geoLine}
      </p>

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
    <div
      data-testid="guide-maneuver"
      className="sticky top-0 z-10 rounded-2xl border border-border bg-card p-4 shadow-float"
    >
      <div className="flex items-start gap-3">
        <span className="flex size-12 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Icon className="size-6" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div
            data-testid="guide-maneuver-instruction"
            className="text-stat font-semibold leading-tight"
          >
            {instruction}
          </div>
          <div className="mt-1 text-label text-muted-foreground">
            {precise && distance != null ? (
              <span data-testid="guide-maneuver-distance">
                {t('guide.maneuverIn', { distance: fmtDist(distance) })}
              </span>
            ) : (
              <span data-testid="guide-maneuver-unprecise">
                {quality === 'unavailable'
                  ? t('guide.noSignal')
                  : t('guide.distanceHidden')}
              </span>
            )}
          </div>
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

interface SuggestionListProps {
  suggestions: GuideSuggestion[];
  onAdd: (id: string) => void;
  onSkip: (id: string) => void;
}

/** Contextual POIs. Adding one hands the choice on; it never edits the route. */
const SuggestionList = ({
  suggestions,
  onAdd,
  onSkip,
}: SuggestionListProps) => {
  const { t } = useTranslation();

  return (
    <div
      data-testid="guide-suggestions"
      className="rounded-2xl border border-border bg-card p-3 shadow-card"
    >
      <div className="px-1 text-meta font-medium text-muted-foreground">
        {t('guide.suggestionsTitle')}
      </div>
      {suggestions.map((s) => (
        <div key={s.id} className="mt-2 flex items-center gap-2 px-1">
          <div className="min-w-0 flex-1">
            <div className="truncate text-body">{s.name}</div>
            <div className="text-meta text-muted-foreground">{s.detail}</div>
          </div>
          <button
            type="button"
            data-testid={`guide-suggestion-add-${s.id}`}
            onClick={() => onAdd(s.id)}
            className="h-8 shrink-0 rounded-full border border-border px-3 text-meta transition-colors hover:bg-muted"
          >
            {t('guide.suggestionAdd')}
          </button>
          <button
            type="button"
            data-testid={`guide-suggestion-skip-${s.id}`}
            onClick={() => onSkip(s.id)}
            className="h-8 shrink-0 rounded-full px-2 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {t('guide.suggestionSkip')}
          </button>
        </div>
      ))}
    </div>
  );
};

const QualityIcon = ({ quality }: { quality: FixQuality }) => {
  if (quality === 'unavailable' || quality === 'stale') {
    return <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (quality === 'waiting' || quality === 'poor') {
    return <LocateFixed className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  return <Navigation className="h-3.5 w-3.5" aria-hidden="true" />;
};

interface GuideHeaderProps {
  onReset: () => void;
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
