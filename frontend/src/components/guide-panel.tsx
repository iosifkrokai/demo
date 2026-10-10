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

/** A contextual POI offered along the way. */
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
  /** Off-route re-plan override. */
  onReroute?: () => void;
  suggestions?: GuideSuggestion[];
  onAddSuggestion?: (id: string) => void;
  /** The transport the plan was built for (Valhalla costing): «pedestrian», «bicycle», «auto» — or nothing while it is unknown, which the guide reads as walking. */
  transport?: string | null;
  /** How far the walk has got, reported whenever it changes. */
  onWalked?: (progress: { visited: number; total: number }) => void;
  /** Leaves navigation entirely — the guide is over, the planner comes back. */
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

/** Fingerprint of the route the walk belongs to. */
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
  } catch {}
  return { route: key, visited: [], startedAt: Date.now() };
};

const saveProgress = (progress: StoredProgress) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
  } catch {}
};

interface LatLon {
  lat: number;
  lon: number;
}

interface Fix extends LatLon {
  /** Metres of 68 % confidence; null when the browser did not say. */
  accuracy: number | null;
  at: number;
  /** The direction the handset points, in degrees; null when the browser did not say. */
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

/** Project `p` onto the segment a→b in a local metre plane. */
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

/** Mode 2 — the guide: walk the route stop by stop. */
/** The guide's own honesty about where the position came from: with `?sim=walk` the fix is replayed, and every screen that shows a distance must say so. */
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
  /** How the guide speaks about movement: on foot, on a bike, or driving. */
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
  /** How the guide enters — the tourist's own call, not a required detour. */
  const [mode, setMode] = useState<'review' | 'moving'>(() =>
    startInMoving ? 'moving' : 'review'
  );
  useEffect(() => {
    if (overviewOpen) setMode('review');
    else if (startInMoving) setMode('moving');
  }, [startInMoving, overviewOpen]);
  /** Details stay open until the tourist deliberately collapses them. */
  const [detailsOpen, setDetailsOpen] = useState(true);
  useEffect(() => {
    if (!startInMoving && hasTrustedFix && !overviewOpen) setMode('moving');
  }, [hasTrustedFix, overviewOpen, startInMoving]);
  /** The panel is NOT closed on entering moving mode. */
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

  /** The live session for the current route; null until the WASM core is up. */
  const [ferroNav, setFerroNav] = useState<FerrostarNavigator | null>(null);
  /** The last TripState the session produced — Idle | Navigating | Complete. */
  const [ferroState, setFerroState] = useState<TripState | null>(null);
  /** Route metadata aligned with Ferrostar's remaining step list. */
  const [ferroRoute, setFerroRoute] = useState<FerrostarRouteResult | null>(
    null
  );
  /** Set the moment Ferrostar cannot be used — no WASM core, or a route it will not build. */
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

  const servicesAlong = useServicesAlong(routeData, {
    enabled: mode === 'moving',
    profile: 'pedestrian',
  });

  /** The nearest upcoming service that is close enough ahead on the route, has not been hinted yet, and is not a stop already on the route. */
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

  const line = useMemo(() => buildLine(routeData), [routeData]);
  const maneuvers = useMemo(
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
  const simulated = isSimulating();
  const setGuideFix = useCommonStore((s) => s.setGuideFix);

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

  const effectiveVisited = useMemo(() => {
    const seen = new Set(progress.visited);
    if (precise && fix) {
      const reachedIndex = stops.findIndex(
        (stop) => metresBetween(fix, stop) <= ARRIVAL_RADIUS_M
      );
      if (reachedIndex >= 0) {
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

  const walked = effectiveVisited.length;
  useEffect(() => {
    onWalked?.({ visited: walked, total: stops.length });
  }, [onWalked, walked, stops.length]);
  const located = useMemo(
    () => (fix && line ? locateOnLine(fix, line) : null),
    [fix, line]
  );

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

  /** Hand the turn distance to the map, which uses it as a navigator does: it closes in for the turn and keeps the tourist on screen. */
  const setGuideTurnDistanceM = useCommonStore((s) => s.setGuideTurnDistanceM);
  useEffect(() => {
    if (ferrostarActive) return;
    setGuideTurnDistanceM(
      mode === 'moving' && maneuverDistance != null
        ? Math.round(maneuverDistance / 10) * 10
        : null
    );
  }, [mode, maneuverDistance, setGuideTurnDistanceM, ferrostarActive]);

  /** Turn the map by the ROUTE's course, not by the phone's heading. */
  /** The one publication of the tourist's position to the map. */
  useEffect(() => {
    if (ferrostarActive) return;
    if (!fix) {
      setGuideFix(null);
      return;
    }
    setGuideFix({
      lat: fix.lat,
      lng: fix.lon,
      heading: fix.heading,
      course:
        courseAlongLine(line, traveled) ??
        courseAtPoint(line, fix.lat, fix.lon),
      at: fix.at,
    });
  }, [fix, line, traveled, setGuideFix, ferrostarActive]);

  /** Remember which turn was last announced — on its own, keyed only on the turn itself. */
  useEffect(() => {
    if (ferrostarActive) return;
    if (!isNewManeuver(prevManeuverRef.current, activeManeuver)) return;
    cancelSpeech();
    spokenThresholdsRef.current = new Map();
    prevManeuverRef.current = activeManeuver;
  }, [activeManeuver, ferrostarActive]);

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

  /** Length of the line the map draws — the denominator for distanceRemaining. */
  const lineTotal = line?.total ?? 0;

  /** Build a session when movement starts, once the WASM core is up. */
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

  /** Every fix through the session, and the four publications the old effects made. */
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
    const atStop = nextStop
      ? metresBetween(fix, nextStop) <= ARRIVAL_RADIUS_M * 2
      : false;
    if (mode === 'moving' && off === true && !atStop) {
      ferroOffRouteRef.current += 1;
      if (ferroOffRouteRef.current >= OFF_ROUTE_FIXES) setOffRoute(true);
    } else {
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

  /** Voice, driven by Ferrostar's own triggers instead of our thresholds. */
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

  const announcement = !activeManeuver
    ? ''
    : precise && maneuverDistance != null
      ? t('guide.announceIn', {
          distance: fmtDist(Math.round(maneuverDistance / 50) * 50),
          instruction: activeManeuver.instruction,
        })
      : activeManeuver.instruction;

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
    } catch {}
  }, [onReroute, fix, t]);

  const wasHiddenRef = useRef(false);

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
        if (wasHiddenRef.current) {
          wasHiddenRef.current = false;
          toast(t('guide.continuingNavigation'), { duration: 2000 });
        }
      } else {
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

interface ManeuverBannerProps {
  instruction: string;
  Icon: ReturnType<typeof getManeuverIcon>;
  distance: number | null;
  precise: boolean;
  quality: FixQuality;
}

/** The big arrow: what to do next, and how far — but only when the fix can carry a number. */
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
              <div
                data-testid="guide-maneuver-distance"
                className="text-2xl font-bold leading-none tracking-tight text-primary md:text-xl"
              >
                {t('guide.turnInAheadStandalone', {
                  distance: fmtDist(distance),
                })}
              </div>
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

/** A navigator-style inline hint: «кафе в 40 м по пути». */
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

/** Panel title + the two quiet controls (sound, reset), kept quiet on purpose. */
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
