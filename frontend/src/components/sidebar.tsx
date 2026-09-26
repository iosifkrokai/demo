import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  Bike,
  Car,
  ChevronDown,
  Clock,
  Compass,
  Footprints,
  History,
  Loader2,
  LocateFixed,
  MapPin,
  Minus,
  Plus,
  RotateCcw,
  Route as RouteIcon,
  Search,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from '@/components/ui/sheet';
import { useCommonStore, type Profile } from '@/stores/common-store';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type PlaceDetails,
  type RouteHistoryEntry,
  type Waypoint,
} from '@/stores/directions-store';
import { useDirectionsQuery } from '@/hooks/use-directions-queries';
import { GuidePanel, guideRouteKey, type GuideStop } from './guide-panel';
import { WaypointList } from './waypoint-list';
import { Chip } from './parts/chip';
import { Segmented, type SegmentedItem } from './parts/segmented';
import { StatTile, StatTiles } from './parts/stat-tiles';
import { StopsSkeleton, SummarySkeleton } from './parts/skeletons';
import type { AmenityStrength, ResultMode } from './types';
import {
  PANEL_SHEET_CLASS,
  SHEET_SNAP_CLASS,
  SheetDragHandle,
  useSheetSnap,
} from './parts/sheet-snap';
import { forward_geocode } from '@/utils/nominatim';

// Same-origin by default: the webapp's nginx proxies /routes/ to the agent
// (see frontend/nginx.conf), so the UI keeps working when it is opened through
// a port-forwarded URL (Codespaces, tunnels) where "localhost" would resolve to
// the visitor's own machine. Set VITE_AGENT_URL only to point at a remote agent.
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

interface AgentPoint {
  id: number;
  name: string;
  category?: string | null;
  lat: number;
  lon: number;
  blurb?: string | null;
  fun_fact?: string | null;
  fun_facts?: string[];
  links?: Array<{ title: string; url: string }>;
  visit_minutes?: number | null;
  opening_hours?: string | null;
  ticket_price?: string | null;
  town?: string | null;
  district?: string | null;
}

interface AgentBudget {
  budget_minutes: number | null;
  total_minutes: number;
  walk_minutes: number;
  visit_minutes: number;
  fits: boolean;
}

/**
 * Time presets. 0 = «без ограничения»: no `time_budget_minutes` is sent at all
 * and the agent builds the full route. Anything >= 15 min is a real constraint.
 */
const TIME_BUDGET_OPTIONS = [
  { value: 30, label: '30 мин' },
  { value: 60, label: '1 ч' },
  { value: 120, label: '2 ч' },
  { value: 240, label: 'полдня' },
  { value: 0, label: 'без ограничения' },
];

/**
 * Transport the tourist has. Empty value = "как удобно": nothing is sent and the
 * agent picks the costing that fits the query (a walk inside a town, a drive
 * across the область). Picking one sends it — transport is a real constraint.
 */
const TRANSPORT_OPTIONS: Array<
  SegmentedItem<'' | Profile> & { costing?: string }
> = [
  {
    value: 'pedestrian',
    label: 'пешком',
    icon: Footprints,
    costing: 'pedestrian',
  },
  { value: 'bicycle', label: 'велосипед', icon: Bike, costing: 'bicycle' },
  { value: 'car', label: 'машина', icon: Car, costing: 'auto' },
  { value: '', label: 'как удобно', icon: Sparkles },
];

const MODES: SegmentedItem<'plan' | 'guide'>[] = [
  { value: 'plan', label: 'Планирование' },
  { value: 'guide', label: 'Проводник' },
];

/** Placeholder of the ask field. Also what the empty state tells the user. */
const QUERY_PLACEHOLDER = 'Что хотите посмотреть?';

/** One-tap starters: they fill the ask field, the tourist decides when to go. */
const HINT_CHIPS = ['замки', 'костёлы', 'монастыри', 'где поесть'];

/**
 * Options of the advanced filters. `code` is the canonical backend category
 * code (backend/agent/constants.CATEGORIES) — the value that goes to the
 * agent. The Russian label lives here only so W7 can centralize all strings
 * later; the sidebar keeps no translation dictionary of its own.
 */
interface FilterOption {
  code: string;
  label: string;
}

/** Result type: a ready itinerary, or a grouped catalogue to choose from. */
const RESULT_MODE_OPTIONS: SegmentedItem<ResultMode>[] = [
  { value: 'route', label: 'маршрут' },
  { value: 'catalogue', label: 'каталог' },
];

/** Themes: what the tourist wants more of (soft — they never force a detour). */
const INTEREST_OPTIONS: readonly FilterOption[] = [
  { code: 'замок', label: 'замки' },
  { code: 'костёл', label: 'костёлы' },
  { code: 'церковь', label: 'церкви' },
  { code: 'монастырь', label: 'монастыри' },
  { code: 'музей', label: 'музеи' },
  { code: 'усадьба', label: 'усадьбы' },
  { code: 'парк', label: 'парки' },
  { code: 'памятник', label: 'памятники' },
];

/**
 * Amenities a walk may need, each with a strength: «обязательно» goes out as a
 * hard service the route must serve, «желательно» as a soft interest.
 */
const AMENITY_OPTIONS: readonly FilterOption[] = [
  { code: 'туалет', label: 'туалет' },
  { code: 'кафе', label: 'кафе / перерыв' },
];

/** Categories to keep out of the route. */
const AVOID_OPTIONS: readonly FilterOption[] = [
  { code: 'кладбище', label: 'кладбища' },
  { code: 'инфраструктура', label: 'инфраструктура' },
  { code: 'гостиница', label: 'гостиницы' },
];

const ALL_FILTER_OPTIONS: readonly FilterOption[] = [
  ...INTEREST_OPTIONS,
  ...AMENITY_OPTIONS,
  ...AVOID_OPTIONS,
];

const filterLabel = (code: string): string =>
  ALL_FILTER_OPTIONS.find((o) => o.code === code)?.label ?? code;

/**
 * Ages the tourist actually typed ("4, 7" → [4, 7]). Free text is parsed for
 * digits only: an age nobody named stays absent, it is never invented (spec §7,
 * "возраст только если известен").
 */
const parseChildAges = (raw: string): number[] =>
  raw
    .split(/[^0-9]+/)
    .map((part) => Number.parseInt(part, 10))
    .filter((age) => Number.isInteger(age) && age >= 0 && age <= 18);

/**
 * Map a /routes/generate failure to a short Russian line the tourist can act on.
 * Client-thrown Russian messages (validation) pass through unchanged; browser
 * English like "Failed to fetch" and raw backend JSON bodies do not.
 */
const routeSubmitErrorText = (err: unknown): string => {
  if (!(err instanceof Error)) return String(err);
  const msg = err.message;
  // fetch() network failures: Chromium "Failed to fetch", Firefox NetworkError,
  // Safari "Load failed". TypeError is the usual name for those.
  if (
    msg === 'Failed to fetch' ||
    msg === 'Load failed' ||
    msg === 'NetworkError when attempting to fetch resource.' ||
    err.name === 'TypeError'
  ) {
    return 'нет связи с агентом — проверьте сеть и попробуйте ещё раз';
  }
  return msg;
};

/** The tourist asked for a toilet stop — RU туалет / санузел, or English toilet. */
const queryAsksForToilet = (q: string): boolean =>
  /туалет|санузел|toilet/i.test(q);

const TOILET_MISSING_WARNING =
  'В базе не нашлось туалетов — маршрут построен без них.';

const fmtMin = (m: number) => {
  const mins = Math.max(0, Math.round(m));
  return mins >= 60
    ? `${Math.floor(mins / 60)} ч ${mins % 60 ? `${mins % 60} мин` : ''}`.trim()
    : `${mins} мин`;
};

const fmtKm = (km: number) =>
  km >= 10 ? `${Math.round(km)} км` : `${km.toFixed(1)} км`;

/** A waypoint that simply says "I am here". */
const meWaypoint = (lat: number, lon: number): Waypoint => {
  const lngLat: [number, number] = [lon, lat];
  return {
    id: ME_WAYPOINT_ID,
    userInput: 'Моё местоположение',
    geocodeResults: [
      {
        title: 'Моё местоположение',
        description: 'старт маршрута',
        selected: true,
        displaylnglat: lngLat,
        sourcelnglat: lngLat,
        key: 0,
        addressindex: 0,
      },
    ],
  };
};

/**
 * The planning panel that replaces the upstream RoutePlanner:
 *   1. ask — the query field, time budget, transport; on submit it hits
 *      /routes/generate and pushes the agent's ordered points into the
 *      directions store (with my own position as the start when known).
 *   2. waypoints — WaypointList (drag/drop, remove, pin).
 *   3. manual add — small Nominatim lookup → append to the list.
 *   4. history — previous routes, restored with their descriptions.
 *
 * Layout follows DESIGN.md: a 380px column on desktop, a bottom sheet with two
 * snap points under 768px, header and main action outside the scroll area.
 */
export const Sidebar = () => {
  const panelOpen = useCommonStore((s) => s.directionsPanelOpen);
  const toggle = useCommonStore((s) => s.toggleDirections);
  const setWaypoint = useDirectionsStore((s) => s.setWaypoint);
  const addEmptyWaypointToEnd = useDirectionsStore(
    (s) => s.addEmptyWaypointToEnd
  );
  const waypoints = useDirectionsStore((s) => s.waypoints);
  const setPlaceDetails = useDirectionsStore((s) => s.setPlaceDetails);
  const refinementLog = useDirectionsStore((s) => s.refinementLog);
  const routeSnapshots = useDirectionsStore((s) => s.routeSnapshots);
  const excludedPlaceIds = useDirectionsStore((s) => s.excludedPlaceIds);
  const undoRefinement = useDirectionsStore((s) => s.undoRefinement);
  const resetRoute = useDirectionsStore((s) => s.resetRoute);
  const pushRefinement = useDirectionsStore((s) => s.pushRefinement);
  const { refetch: refetchDirections } = useDirectionsQuery();

  const resetSettings = useCommonStore((s) => s.resetSettings);
  const navigate = useNavigate({ from: '/$activeTab' });

  const [query, setQuery] = useState('');
  // Mode 1 — planning (build/refine the route), mode 2 — the guide that walks it.
  const [mode, setMode] = useState<'plan' | 'guide'>('plan');
  const placeDetails = useDirectionsStore((s) => s.placeDetails);
  const [timeBudget, setTimeBudget] = useState(0); // 0 = без ограничения
  // '' = «как удобно»: no transport constraint, the agent picks the costing
  // (walking inside a town, driving across a region). Only an explicit pick —
  // or the costing the agent answers with — is a real constraint.
  const [transport, setTransport] = useState<'' | Profile>('');
  const [mirroredCosting, setMirroredCosting] = useState<Profile | null>(null);

  // ── Advanced filters (spec 002) ──────────────────────────────────────────
  // Progressive disclosure: the panel stays closed until asked for, and every
  // value starts "not chosen" so nothing is invented for the tourist.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // null = the tourist did not say. Adults/children are quantities, not ages:
  // an age only travels when it was typed (see parseChildAges).
  const [partyAdults, setPartyAdults] = useState<number | null>(null);
  const [partyChildren, setPartyChildren] = useState<number | null>(null);
  const [childrenAgesText, setChildrenAgesText] = useState('');
  // «обязательно» → hard_services, «желательно» → interests. Absent = off.
  const [amenities, setAmenities] = useState<Record<string, AmenityStrength>>(
    {}
  );
  const [interests, setInterests] = useState<string[]>([]);
  const [avoid, setAvoid] = useState<string[]>([]);
  const [resultMode, setResultMode] = useState<ResultMode>('route');
  const [roundTrip, setRoundTrip] = useState(false);
  const [me, setMe] = useState<{ lat: number; lon: number } | null>(null);
  const [geoState, setGeoState] = useState<
    'idle' | 'locating' | 'ok' | 'denied'
  >('idle');
  const [geoReason, setGeoReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{
    kind: 'ok' | 'err' | 'warn';
    text: string;
  } | null>(null);
  const [summary, setSummary] = useState<{
    stops: number;
    km: number | null;
    /** summary.time_seconds — the time actually spent on the legs. */
    travelMinutes: number;
    /** budget.walk_minutes — the agent's own estimate of the walking time. */
    walkMinutes: number;
    visitMinutes: number;
    budgetMinutes: number | null;
    fits: boolean;
  } | null>(null);

  // The query of the last successful build: switching transport re-plans it, so
  // the stops, the drawn line and the "в пути" time all follow the new costing.
  const lastQueryRef = useRef<string | null>(null);
  const replanRef = useRef<() => void>(() => {});
  // Always-current transport: state can be stale inside async callbacks, the
  // ref cannot. The plan body and the re-plan both read it.
  const transportRef = useRef<'' | Profile>('');

  const [manualQuery, setManualQuery] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualErr, setManualErr] = useState<string | null>(null);

  const routeHistory = useDirectionsStore((s) => s.routeHistory);
  const addToHistory = useDirectionsStore((s) => s.addToHistory);
  const removeFromHistory = useDirectionsStore((s) => s.removeFromHistory);
  const clearHistory = useDirectionsStore((s) => s.clearHistory);

  const { snap, handleProps } = useSheetSnap();
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [query]);

  /**
   * Pick a transport. `''` means «как удобно» — then nothing is sent to the
   * agent unless it told us a costing, and the webapp keeps its own default.
   */
  const setTransportEverywhere = useCallback(
    (value: '' | Profile, replan = true) => {
      const changed = transportRef.current !== value;
      transportRef.current = value;
      setTransport(value);
      if (value === '') return;
      setMirroredCosting(value);
      // the app's own routing (isochrones, directions) needs the costing too
      resetSettings(value);
      // A transport is a real constraint: re-plan with it instead of leaving a
      // walking route (and its travel time) on screen for a drive.
      if (replan && changed && lastQueryRef.current) replanRef.current();
    },
    [resetSettings]
  );

  // The URL profile is what the webapp's own /route request rides on. It follows
  // the transport only once one is known: the tourist's pick, or the costing the
  // agent planned with (see the generate handler). Never on mount — «как удобно»
  // must not be silently translated into the webapp's bicycle default.
  useEffect(() => {
    if (!mirroredCosting) return;
    navigate({
      search: (prev) => ({ ...prev, profile: mirroredCosting }),
      replace: true,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mirroredCosting]);

  /**
   * Ask the browser where we are and pin it as the route start: the marker on
   * the map, the first waypoint and the agent's `origin` all come from here.
   */
  const locateMe = useCallback(
    async (silent = false): Promise<{ lat: number; lon: number } | null> => {
      if (!navigator.geolocation) {
        setGeoState('denied');
        setGeoReason('браузер не умеет геолокацию');
        return null;
      }
      setGeoState('locating');
      // The browser can neither grant nor deny (a permission prompt nobody
      // answers, a headless context): getCurrentPosition then never calls back.
      // Race it against our own timer so a query never hangs on geolocation.
      const GEO_WAIT_MS = 8000;
      try {
        const pos = await new Promise<GeolocationPosition | null>((resolve) => {
          const timer = setTimeout(() => resolve(null), GEO_WAIT_MS + 500);
          const settle = (value: GeolocationPosition | null) => {
            clearTimeout(timer);
            resolve(value);
          };
          navigator.geolocation.getCurrentPosition(
            (position) => settle(position),
            () => settle(null),
            {
              enableHighAccuracy: true,
              timeout: GEO_WAIT_MS,
              maximumAge: 60_000,
            }
          );
        });
        if (!pos) {
          setGeoState('denied');
          setGeoReason('не удалось определить');
          return null;
        }
        const coords = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
        };
        setMe(coords);
        setGeoState('ok');
        setGeoReason('');
        // Keep my position as waypoint 0 so the map shows it and the line the
        // webapp draws starts there too.
        const current = useDirectionsStore.getState().waypoints;
        setWaypoint([
          meWaypoint(coords.lat, coords.lon),
          ...current.filter((w) => w.id !== ME_WAYPOINT_ID),
        ]);
        if (!silent) refetchDirections();
        return coords;
      } catch (err) {
        // 1 === PERMISSION_DENIED in the Geolocation API
        const code = (err as GeolocationPositionError | undefined)?.code;
        setGeoState('denied');
        setGeoReason(
          code === 1 ? 'браузер запретил доступ' : 'не удалось определить'
        );
        return null;
      }
    },
    [refetchDirections, setWaypoint]
  );

  // Ask once on open: the tourist expects to see themselves on the map.
  useEffect(() => {
    if (geoState === 'idle') void locateMe(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ages the tourist named, in order, deduplicated. Empty = unknown, and then
  // `party_children_ages` is not sent at all.
  const childrenAges = useMemo(
    () => [...new Set(parseChildAges(childrenAgesText))],
    [childrenAgesText]
  );
  const hardServices = useMemo(
    () =>
      AMENITY_OPTIONS.filter((o) => amenities[o.code] === 'hard').map(
        (o) => o.code
      ),
    [amenities]
  );
  const softAmenities = useMemo(
    () =>
      AMENITY_OPTIONS.filter((o) => amenities[o.code] === 'soft').map(
        (o) => o.code
      ),
    [amenities]
  );
  // Soft amenities are interests like any theme: one list goes to the agent.
  const interestCodes = useMemo(
    () => [...interests, ...softAmenities],
    [interests, softAmenities]
  );

  /**
   * One line per condition the tourist actually set — the summary the panel
   * shows so no filter is applied invisibly. Its length is also the badge on
   * «ещё фильтры».
   */
  const filterSummary = useMemo(() => {
    const out: string[] = [];
    if (partyAdults != null) out.push(`${partyAdults} взр.`);
    if (partyChildren != null)
      out.push(partyChildren === 1 ? '1 ребёнок' : `${partyChildren} детей`);
    if (childrenAges.length > 0) out.push(`возраст ${childrenAges.join(', ')}`);
    for (const code of hardServices)
      out.push(`обязательно: ${filterLabel(code)}`);
    for (const code of softAmenities)
      out.push(`желательно: ${filterLabel(code)}`);
    for (const code of interests) out.push(`интерес: ${filterLabel(code)}`);
    for (const code of avoid) out.push(`без ${filterLabel(code)}`);
    if (resultMode === 'catalogue') out.push('каталог мест');
    if (roundTrip) out.push('круговой маршрут');
    return out;
  }, [
    partyAdults,
    partyChildren,
    childrenAges,
    hardServices,
    softAmenities,
    interests,
    avoid,
    resultMode,
    roundTrip,
  ]);

  /** Flip a code in a multi-select list. */
  const toggleCode = (
    setter: Dispatch<SetStateAction<string[]>>,
    code: string
  ) =>
    setter((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );

  /** Click the strength a row already has to clear it — off is a valid state. */
  const toggleAmenity = (code: string, next: AmenityStrength) =>
    setAmenities((prev) => {
      const copy = { ...prev };
      if (copy[code] === next) delete copy[code];
      else copy[code] = next;
      return copy;
    });

  const submitPrompt = async (text?: string) => {
    const q = (text ?? query).trim();
    if (!q || busy) return;
    setBusy(true);
    setStatus(null);
    setSummary(null);

    // A fresh position wins over the cached one; without it the route simply
    // starts at the first place.
    let origin = me;
    if (geoState !== 'denied') {
      origin = (await locateMe(true)) ?? me;
    }

    try {
      const body: {
        query: string;
        time_budget_minutes?: number;
        profile?: string;
        origin?: { lat: number; lon: number };
        // ── Explicit filters (spec 002) — each one is present only when the
        // tourist actually chose it; an absent field means "no constraint".
        party_adults?: number;
        party_children?: number;
        party_children_ages?: number[];
        hard_services?: string[];
        interests?: string[];
        avoid?: string[];
        result_mode?: ResultMode;
        round_trip?: boolean;
        context?: {
          instruction: string;
          revision: number;
          excluded_ids: number[];
          base_points: {
            id: number | null;
            name: string;
            lat: number;
            lon: number;
            pinned: boolean;
            source: 'agent' | 'user' | 'mine';
          }[];
        };
      } = { query: q };
      if (timeBudget >= 15) body.time_budget_minutes = timeBudget;
      const chosen = TRANSPORT_OPTIONS.find(
        (o) => o.value === transportRef.current
      );
      if (chosen?.costing) body.profile = chosen.costing;
      if (origin) body.origin = origin;

      // Party: counts only, and ages only when they were typed. «не указано»
      // (null) sends nothing rather than inventing a group.
      if (partyAdults != null) body.party_adults = partyAdults;
      if (partyChildren != null) body.party_children = partyChildren;
      if (childrenAges.length > 0) body.party_children_ages = childrenAges;
      if (hardServices.length > 0) body.hard_services = hardServices;
      if (interestCodes.length > 0) body.interests = interestCodes;
      if (avoid.length > 0) body.avoid = avoid;
      // «маршрут» is the backend default: only a catalogue changes the answer.
      if (resultMode === 'catalogue') body.result_mode = resultMode;
      if (roundTrip) body.round_trip = true;

      // Second and later turns are refinements: the agent receives the route as
      // it stands — stops, hand-pinned flags, hand-deleted ids — plus the delta
      // text, so «добавь кофейню» rebuilds on top of the current route instead
      // of starting over. Snapshot first: that is what «отменить уточнение»
      // rolls back to.
      const prior = useDirectionsStore.getState();
      const basePoints = prior.waypoints
        .map((wp) => {
          const geo =
            wp.geocodeResults.find((g) => g.selected) ?? wp.geocodeResults[0];
          if (!geo) return null;
          // Older saved routes carry only displaylnglat; skip anything without
          // usable coordinates rather than sending NaN to the agent.
          const [lon, lat] = geo.sourcelnglat ?? geo.displaylnglat ?? [];
          if (lat === undefined || lon === undefined) return null;
          const isMe = wp.id === ME_WAYPOINT_ID;
          return {
            id: wp.placeId ?? null,
            name: wp.userInput || geo.title || 'точка',
            lat,
            lon,
            // A stop the user placed by hand (no placeId) survives any refine.
            pinned: wp.pinned ?? (!isMe && wp.placeId == null),
            source: (isMe ? 'mine' : wp.placeId == null ? 'user' : 'agent') as
              | 'agent'
              | 'user'
              | 'mine',
          };
        })
        .filter((p): p is NonNullable<typeof p> => p !== null);
      const isRefinement = basePoints.some((p) => p.source !== 'mine');
      if (isRefinement) {
        prior.snapshotRoute();
        body.context = {
          instruction: q,
          revision: prior.refinementLog.length + 1,
          excluded_ids: prior.excludedPlaceIds,
          base_points: basePoints,
        };
      }

      const r = await fetch(`${AGENT_URL}/routes/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        // Status only: raw JSON/detail from the agent is not actionable in the UI.
        throw new Error(
          `агент ответил ошибкой ${r.status} — попробуйте ещё раз`
        );
      }
      const data = (await r.json()) as {
        points?: AgentPoint[];
        budget?: AgentBudget;
        costing?: string | null;
        summary?: { length_km?: number | null; time_seconds?: number | null };
      };
      const pts = data.points ?? [];
      if (pts.length < 2) {
        throw new Error('нашёл меньше 2 мест — попробуйте уточнить запрос');
      }

      // The agent tells us which transport it planned for; adopt it (unless the
      // tourist picked one) so the map's own line uses the same costing.
      if (!transportRef.current && data.costing) {
        const match = TRANSPORT_OPTIONS.find((o) => o.costing === data.costing);
        if (match) setTransportEverywhere(match.value, false);
      }

      const start: Waypoint[] = origin
        ? [meWaypoint(origin.lat, origin.lon)]
        : [];
      const placeWaypoints: Waypoint[] = pts.map((p, i) => ({
        id: i.toString(),
        userInput: p.name,
        placeId: p.id,
        geocodeResults: [
          {
            title: p.name,
            description: p.category ?? undefined,
            selected: true,
            displaylnglat: [p.lon, p.lat],
            sourcelnglat: [p.lon, p.lat],
            key: i,
            addressindex: 0,
          },
        ],
      }));
      setWaypoint([...start, ...placeWaypoints]);

      // The map reads blurb / fun facts from here when a marker is clicked.
      setPlaceDetails(
        Object.fromEntries(
          pts.map((p) => [
            p.id,
            {
              name: p.name,
              category: p.category ?? null,
              blurb: p.blurb ?? null,
              funFact: p.fun_fact ?? null,
              funFacts: p.fun_facts ?? [],
              links: p.links ?? [],
              visitMinutes: p.visit_minutes ?? null,
              openingHours: p.opening_hours ?? null,
              ticketPrice: p.ticket_price ?? null,
              town: p.town ?? null,
              district: p.district ?? null,
            } satisfies PlaceDetails,
          ])
        )
      );
      refetchDirections();

      const visitMins =
        data.budget?.visit_minutes ??
        pts.reduce((s, p) => s + (p.visit_minutes ?? 15), 0);
      const travelMins = Math.round((data.summary?.time_seconds ?? 0) / 60);
      const walkMins = data.budget?.walk_minutes ?? travelMins;
      lastQueryRef.current = q;
      setSummary({
        stops: pts.length,
        km: data.summary?.length_km ?? null,
        travelMinutes: travelMins,
        walkMinutes: walkMins,
        visitMinutes: visitMins,
        budgetMinutes: data.budget?.budget_minutes ?? null,
        fits: data.budget?.fits ?? true,
      });

      addToHistory({
        query: q,
        timeBudget: data.budget?.budget_minutes ?? timeBudget,
        places: pts.map((p) => ({
          id: p.id,
          name: p.name,
          category: p.category ?? null,
          lat: p.lat,
          lon: p.lon,
          blurb: p.blurb ?? null,
          funFact: p.fun_fact ?? null,
          funFacts: p.fun_facts ?? [],
          links: p.links ?? [],
          visitMinutes: p.visit_minutes ?? null,
          openingHours: p.opening_hours ?? null,
          ticketPrice: p.ticket_price ?? null,
          town: p.town ?? null,
          district: p.district ?? null,
        })),
      });

      // Tell the user what the refinement actually changed — a silent swap is
      // what makes a replanned route feel random.
      if (isRefinement) {
        const beforeIds = new Set(
          prior.waypoints
            .map((wp) => wp.placeId)
            .filter((v): v is number => v != null)
        );
        const afterIds = new Set(pts.map((p) => p.id));
        pushRefinement({
          instruction: q,
          added: pts
            .filter((pt) => !beforeIds.has(pt.id))
            .map((pt) => pt.name)
            .slice(0, 8),
          removed: prior.waypoints
            .filter(
              (wp) =>
                wp.id !== ME_WAYPOINT_ID &&
                wp.placeId != null &&
                !afterIds.has(wp.placeId)
            )
            .map((wp) => wp.userInput)
            .slice(0, 8),
        });
      }

      // Non-blocking: the route stands; only say the toilet ask could not be met.
      // Either the text asked for it or the tourist marked «туалет» обязательным.
      if (
        (queryAsksForToilet(q) || hardServices.includes('туалет')) &&
        !pts.some((p) => p.category === 'туалет')
      ) {
        setStatus({ kind: 'warn', text: TOILET_MISSING_WARNING });
      }

      setQuery('');
    } catch (e) {
      setStatus({ kind: 'err', text: routeSubmitErrorText(e) });
    } finally {
      setBusy(false);
    }
  };

  // Re-run the last query as-is: the body picks up the current transport/budget.
  replanRef.current = () => {
    const q = lastQueryRef.current;
    if (q) void submitPrompt(q);
  };

  const reset = () => {
    lastQueryRef.current = null;
    const empties: Waypoint[] = [
      { id: '0', userInput: '', geocodeResults: [] },
      { id: '1', userInput: '', geocodeResults: [] },
    ];
    setWaypoint(me ? [meWaypoint(me.lat, me.lon), ...empties] : empties);
    setStatus(null);
    setSummary(null);
    refetchDirections();
  };

  const manualAdd = async () => {
    const q = manualQuery.trim();
    if (!q || manualBusy) return;
    setManualBusy(true);
    setManualErr(null);
    try {
      const { data } = await forward_geocode(q);
      const top = data?.[0];
      if (!top) {
        setManualErr('ничего не нашлось');
        return;
      }
      const lat = parseFloat(top.lat);
      const lon = parseFloat(top.lon);
      const title = top.display_name.split(',')[0]?.trim() || q;
      const next = [
        ...waypoints,
        {
          id: waypoints.length.toString(),
          userInput: title,
          geocodeResults: [
            {
              title,
              selected: true,
              displaylnglat: [lon, lat],
              sourcelnglat: [lon, lat],
              key: waypoints.length,
              addressindex: 0,
            },
          ],
        } satisfies Waypoint,
      ];
      setWaypoint(next);
      refetchDirections();
      setManualQuery('');
    } catch (e) {
      setManualErr(e instanceof Error ? e.message : String(e));
    } finally {
      setManualBusy(false);
    }
  };

  // The guide walks the same stops the planner built (the «моё местоположение»
  // start is not a stop).
  const guideStops = useMemo<GuideStop[]>(
    () =>
      waypoints
        .filter((w) => w.id !== ME_WAYPOINT_ID)
        .map((w): GuideStop | null => {
          const geo =
            w.geocodeResults.find((g) => g.selected) ?? w.geocodeResults[0];
          const lnglat = geo?.sourcelnglat ?? geo?.displaylnglat;
          if (!lnglat) return null;
          const [lon, lat] = lnglat;
          if (lat === undefined || lon === undefined) return null;
          const details =
            w.placeId != null ? placeDetails[w.placeId] : undefined;
          return {
            id: w.id,
            name: w.userInput || geo?.title || `точка ${w.id}`,
            lat,
            lon,
            placeId: w.placeId,
            category: details?.category ?? null,
            visitMinutes: details?.visitMinutes ?? null,
          };
        })
        .filter((stop): stop is GuideStop => stop !== null),
    [waypoints, placeDetails]
  );

  const stopCount = waypoints.filter(
    (w) => w.id !== ME_WAYPOINT_ID && w.geocodeResults.length > 0
  ).length;
  const hasRoute = stopCount > 0;
  const geoBadge = useMemo(() => {
    switch (geoState) {
      case 'ok':
        return { text: 'старт — моё местоположение', tone: 'text-emerald-600' };
      case 'locating':
        return { text: 'ищу вас…', tone: 'text-muted-foreground' };
      case 'denied':
        return { text: `геолокация: ${geoReason}`, tone: 'text-amber-600' };
      default:
        return { text: 'старт не задан', tone: 'text-muted-foreground' };
    }
  }, [geoState, geoReason]);

  return (
    <Sheet open={panelOpen} modal={false}>
      <SheetContent
        side="left"
        className={cn(PANEL_SHEET_CLASS, SHEET_SNAP_CLASS[snap])}
      >
        <div className="flex h-full min-h-0 flex-col">
          {/* ── Header: mode switch, title, quiet close row. All three sit in
              normal flow, so nothing can ever slide under the close button. ── */}
          <header className="shrink-0 border-b border-border px-4 pb-2.5">
            <SheetDragHandle snap={snap} handleProps={handleProps} />
            <div className="flex items-center gap-2">
              <Segmented
                items={MODES}
                value={mode}
                onChange={setMode}
                label="режим"
                className="min-w-0 flex-1"
                testId={(value) => `mode-${value}`}
              />
              <button
                type="button"
                onClick={toggle}
                aria-label="закрыть панель"
                title="закрыть панель"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
            <div className="mt-2.5 flex min-w-0 items-center gap-2">
              <RouteIcon
                className="h-4 w-4 shrink-0 text-primary"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <SheetTitle className="truncate text-body">
                  AI-гид по Гродно
                </SheetTitle>
                {/* Radix wants a description for the dialog; the visible line
                    below is the same sentence, so keep it out of the a11y tree. */}
                <SheetDescription className="sr-only">
                  Планировщик маршрутов по Гродно и области
                </SheetDescription>
                <p className="truncate text-meta text-muted-foreground">
                  {mode === 'plan'
                    ? 'Опишите запрос — соберу маршрут по дорогам'
                    : 'Отмечайте пройденные остановки'}
                </p>
              </div>
            </div>
          </header>

          {/* ── Body: the only part that scrolls. ── */}
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-6 pt-3">
            {mode === 'guide' ? (
              // key: a rebuilt route remounts the guide, so the walk restarts
              // instead of carrying progress from the route that no longer exists
              <GuidePanel key={guideRouteKey(guideStops)} stops={guideStops} />
            ) : (
              <>
                {/* === Ask === */}
                <section className="flex flex-col gap-2">
                  <div className="rounded-2xl border border-border bg-card px-3 py-2.5 shadow-card transition-colors focus-within:border-ring">
                    <div className="flex items-center gap-2.5">
                      <Search
                        className="h-[18px] w-[18px] shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <Textarea
                        ref={taRef}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            submitPrompt();
                          }
                        }}
                        placeholder={
                          hasRoute
                            ? 'Что уточнить? «добавь кофейню»'
                            : QUERY_PLACEHOLDER
                        }
                        aria-label="что хотите посмотреть"
                        className="min-h-0 flex-1 resize-none border-0 bg-transparent p-0 text-body leading-6 shadow-none focus-visible:ring-0"
                        rows={1}
                        disabled={busy}
                      />
                    </div>
                  </div>
                  <div
                    role="group"
                    aria-label="подсказки"
                    className="flex flex-wrap gap-1.5"
                  >
                    {HINT_CHIPS.map((hint) => (
                      <Chip
                        key={hint}
                        onClick={() => {
                          setQuery(hint);
                          taRef.current?.focus();
                        }}
                        data-testid={`hint-${hint}`}
                      >
                        {hint}
                      </Chip>
                    ))}
                  </div>
                </section>

                {/* === Constraints: time + transport. Both are the user's call —
                    nothing is invented for them. === */}
                <section className="flex flex-col gap-2.5">
                  <div className="flex flex-col gap-1.5">
                    <span
                      id="time-budget-label"
                      className="text-meta text-muted-foreground"
                    >
                      сколько есть времени
                    </span>
                    <div
                      role="group"
                      aria-labelledby="time-budget-label"
                      className="flex flex-wrap gap-1.5"
                    >
                      {TIME_BUDGET_OPTIONS.map((option) => (
                        <Chip
                          key={option.value}
                          selected={timeBudget === option.value}
                          onClick={() => setTimeBudget(option.value)}
                          data-testid={`budget-${option.value}`}
                        >
                          {option.label}
                        </Chip>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <span className="text-meta text-muted-foreground">
                      на чём
                    </span>
                    <Segmented
                      items={TRANSPORT_OPTIONS}
                      value={transport}
                      onChange={setTransportEverywhere}
                      label="на чём"
                      stacked
                      disabled={busy}
                      testId={(value) => `transport-${value || 'any'}`}
                    />
                  </div>

                  <button
                    type="button"
                    onClick={() => void locateMe()}
                    disabled={geoState === 'locating'}
                    className="flex w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-meta transition-colors hover:bg-muted disabled:opacity-60"
                    title="переопределить, откуда начинается маршрут"
                  >
                    <LocateFixed
                      className={
                        geoState === 'ok'
                          ? 'h-3.5 w-3.5 shrink-0 text-emerald-600'
                          : 'h-3.5 w-3.5 shrink-0 text-muted-foreground'
                      }
                    />
                    <span className={`truncate ${geoBadge.tone}`}>
                      {geoBadge.text}
                    </span>
                    <span className="ml-auto shrink-0 text-muted-foreground">
                      {geoState === 'locating' ? '…' : 'обновить'}
                    </span>
                  </button>

                  {/* Progressive disclosure (spec 002): the two controls above
                      are always visible; everything else waits behind this. */}
                  <button
                    type="button"
                    data-testid="more-filters"
                    aria-expanded={advancedOpen}
                    aria-controls="advanced-filters"
                    onClick={() => setAdvancedOpen((v) => !v)}
                    className="flex w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-meta transition-colors hover:bg-muted"
                  >
                    <SlidersHorizontal
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span>ещё фильтры</span>
                    {filterSummary.length > 0 && (
                      <span
                        data-testid="filters-count"
                        className="rounded-full bg-muted px-1.5 py-0.5 text-badge font-semibold text-foreground"
                      >
                        {filterSummary.length}
                      </span>
                    )}
                    <ChevronDown
                      className={cn(
                        'ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform',
                        advancedOpen && 'rotate-180'
                      )}
                      aria-hidden="true"
                    />
                  </button>

                  {/* The summary is always visible once anything is chosen, so
                      no condition is applied invisibly. */}
                  {filterSummary.length > 0 && (
                    <div
                      data-testid="filters-summary"
                      role="status"
                      className="flex flex-wrap items-center gap-1 rounded-xl bg-muted px-3 py-2 text-meta text-muted-foreground"
                    >
                      <span>учитываю:</span>
                      {filterSummary.map((item) => (
                        <span
                          key={item}
                          className="rounded-full bg-card px-2 py-0.5 text-foreground"
                        >
                          {item}
                        </span>
                      ))}
                      {query.trim() !== '' && (
                        <span
                          data-testid="filters-precedence"
                          className="basis-full pt-0.5"
                        >
                          применю фильтры поверх текста запроса — они важнее
                        </span>
                      )}
                    </div>
                  )}

                  {advancedOpen && (
                    <div
                      id="advanced-filters"
                      data-testid="advanced-filters"
                      className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-3"
                    >
                      {/* Party: counts, plus ages only when they were typed. */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          кто идёт
                        </span>
                        <Stepper
                          label="взрослые"
                          testId="party-adults"
                          value={partyAdults}
                          min={1}
                          max={50}
                          onChange={setPartyAdults}
                        />
                        <Stepper
                          label="дети"
                          testId="party-children"
                          value={partyChildren}
                          min={1}
                          max={20}
                          onChange={setPartyChildren}
                        />
                        {partyChildren != null && partyChildren > 0 && (
                          <div className="flex flex-col gap-1">
                            <label
                              htmlFor="children-ages"
                              className="text-meta text-muted-foreground"
                            >
                              возраст детей, если знаете
                            </label>
                            <Input
                              id="children-ages"
                              data-testid="children-ages"
                              value={childrenAgesText}
                              onChange={(e) =>
                                setChildrenAgesText(e.target.value)
                              }
                              placeholder="4, 7"
                              className="h-9 text-label"
                            />
                          </div>
                        )}
                      </div>

                      {/* Amenities, each «обязательно» or «желательно». */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          удобства в пути
                        </span>
                        {AMENITY_OPTIONS.map((option) => (
                          <div
                            key={option.code}
                            className="flex items-center gap-2"
                          >
                            <span className="text-label">{option.label}</span>
                            <div className="ml-auto flex gap-1">
                              <Chip
                                selected={amenities[option.code] === 'hard'}
                                onClick={() =>
                                  toggleAmenity(option.code, 'hard')
                                }
                                data-testid={`amenity-${option.code}-hard`}
                                className="h-7 px-2 text-meta"
                              >
                                обязательно
                              </Chip>
                              <Chip
                                selected={amenities[option.code] === 'soft'}
                                onClick={() =>
                                  toggleAmenity(option.code, 'soft')
                                }
                                data-testid={`amenity-${option.code}-soft`}
                                className="h-7 px-2 text-meta"
                              >
                                желательно
                              </Chip>
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Themes: soft by nature, they never force a detour. */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          интересы
                        </span>
                        <div
                          role="group"
                          aria-label="интересы"
                          className="flex flex-wrap gap-1.5"
                        >
                          {INTEREST_OPTIONS.map((option) => (
                            <Chip
                              key={option.code}
                              selected={interests.includes(option.code)}
                              onClick={() =>
                                toggleCode(setInterests, option.code)
                              }
                              data-testid={`interest-${option.code}`}
                            >
                              {option.label}
                            </Chip>
                          ))}
                        </div>
                      </div>

                      {/* Keep out. */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          избегать
                        </span>
                        <div
                          role="group"
                          aria-label="избегать"
                          className="flex flex-wrap gap-1.5"
                        >
                          {AVOID_OPTIONS.map((option) => (
                            <Chip
                              key={option.code}
                              selected={avoid.includes(option.code)}
                              onClick={() => toggleCode(setAvoid, option.code)}
                              data-testid={`avoid-${option.code}`}
                            >
                              {option.label}
                            </Chip>
                          ))}
                        </div>
                      </div>

                      {/* What to return, and whether to come back to the start. */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          что показать
                        </span>
                        <Segmented
                          items={RESULT_MODE_OPTIONS}
                          value={resultMode}
                          onChange={setResultMode}
                          label="тип результата"
                          disabled={busy}
                          testId={(value) => `result-mode-${value}`}
                        />
                        <Chip
                          selected={roundTrip}
                          onClick={() => setRoundTrip((v) => !v)}
                          data-testid="round-trip"
                          className="mt-0.5 self-start"
                        >
                          круговой маршрут
                        </Chip>
                      </div>
                    </div>
                  )}
                </section>

                {/* === Route === */}
                <section className="flex flex-col gap-2.5">
                  {hasRoute && (
                    <div className="flex items-center justify-between gap-2">
                      <h2 className="text-label font-semibold">Маршрут</h2>
                      <button
                        type="button"
                        onClick={reset}
                        className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title="очистить маршрут"
                      >
                        <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                        сброс
                      </button>
                    </div>
                  )}

                  {summary ? (
                    <>
                      <StatTiles>
                        <StatTile value={summary.stops} label="точек" />
                        <StatTile
                          value={summary.km != null ? fmtKm(summary.km) : '—'}
                          label="длина"
                        />
                        <StatTile
                          value={summary.travelMinutes}
                          label="мин в пути"
                        />
                      </StatTiles>
                      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                          {`в пути ~${fmtMin(summary.walkMinutes)}`}
                        </span>
                        <span>{`осмотр ~${fmtMin(summary.visitMinutes)}`}</span>
                        <span>
                          {summary.budgetMinutes
                            ? `лимит ${fmtMin(summary.budgetMinutes)}`
                            : 'без лимита'}
                        </span>
                        {!summary.fits && (
                          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700">
                            не влезло в лимит
                          </span>
                        )}
                      </p>
                    </>
                  ) : (
                    busy && <SummarySkeleton />
                  )}

                  {busy ? (
                    <StopsSkeleton />
                  ) : hasRoute ? (
                    <WaypointList onChanged={() => setStatus(null)} />
                  ) : (
                    /* Never a bare blank panel: say what to do instead. The hint
                       chips under the ask field are the empty state's chips. */
                    <div
                      data-testid="plan-empty"
                      className="flex flex-col items-center gap-2 rounded-2xl border border-border bg-card px-4 py-7 text-center"
                    >
                      <Compass
                        className="h-7 w-7 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <p className="text-label text-muted-foreground">
                        Здесь появятся остановки маршрута — или соберите его из
                        точек вручную
                      </p>
                    </div>
                  )}

                  {status && (
                    <div
                      role={status.kind === 'err' ? 'alert' : 'status'}
                      aria-live={status.kind === 'err' ? 'assertive' : 'polite'}
                      data-testid={
                        status.kind === 'warn'
                          ? 'toilet-missing-warning'
                          : undefined
                      }
                      className={[
                        'rounded-xl px-3 py-2 text-meta',
                        status.kind === 'ok'
                          ? 'bg-primary/10 text-primary'
                          : status.kind === 'warn'
                            ? 'bg-amber-500/15 text-amber-800'
                            : 'bg-destructive/10 text-destructive',
                      ].join(' ')}
                    >
                      {status.text}
                    </div>
                  )}

                  {/* What the refinement turns changed, and the two ways out of
                      them. Stays visible for the whole route, not just while
                      there is a log: the manual deletions outlive it. */}
                  {hasRoute && (
                    <div className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-3">
                      {(refinementLog.length > 0 ||
                        excludedPlaceIds.length > 0) && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          {refinementLog.map((entry) => (
                            <span
                              key={entry.id}
                              className="inline-flex flex-wrap items-center gap-1"
                            >
                              <span
                                title={
                                  [
                                    entry.added.length
                                      ? `добавил: ${entry.added.join(', ')}`
                                      : '',
                                    entry.removed.length
                                      ? `убрал: ${entry.removed.join(', ')}`
                                      : '',
                                  ]
                                    .filter(Boolean)
                                    .join(' · ') || 'без изменений'
                                }
                                className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-meta text-muted-foreground"
                              >
                                {entry.instruction}
                              </span>
                              {entry.added.length > 0 && (
                                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-meta text-primary">
                                  добавлено {entry.added.length}
                                </span>
                              )}
                              {entry.removed.length > 0 && (
                                <span className="rounded-full bg-muted px-2 py-0.5 text-meta text-muted-foreground">
                                  убрано {entry.removed.length}
                                </span>
                              )}
                              {entry.added.length === 0 &&
                                entry.removed.length === 0 && (
                                  <span className="rounded-full bg-muted px-2 py-0.5 text-meta text-muted-foreground">
                                    без изменений
                                  </span>
                                )}
                            </span>
                          ))}
                          {excludedPlaceIds.length > 0 && (
                            <span
                              data-testid="excluded-chip"
                              className="rounded-full bg-destructive/10 px-2.5 py-0.5 text-meta text-destructive"
                            >
                              убрано вручную: {excludedPlaceIds.length}
                            </span>
                          )}
                        </div>
                      )}
                      <div className="flex items-center gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={() => {
                            undoRefinement();
                            setStatus({
                              kind: 'ok',
                              text: 'вернул предыдущий маршрут',
                            });
                            refetchDirections();
                          }}
                          disabled={routeSnapshots.length === 0}
                          className="h-9 rounded-full px-3 text-label disabled:opacity-40"
                        >
                          <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
                          отменить уточнение
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => {
                            resetRoute();
                            reset();
                          }}
                          className="h-9 rounded-full px-3 text-label font-normal text-muted-foreground hover:text-foreground"
                        >
                          <RotateCcw
                            className="h-3.5 w-3.5"
                            aria-hidden="true"
                          />
                          новый маршрут
                        </Button>
                      </div>
                    </div>
                  )}
                </section>

                {/* === Manual add === */}
                <section className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-3 shadow-card">
                  <h2 className="text-label font-semibold">Добавить точку</h2>
                  <div className="flex gap-2">
                    <Input
                      value={manualQuery}
                      onChange={(e) => setManualQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          manualAdd();
                        }
                      }}
                      placeholder="Каложская церковь, Гродно"
                      aria-label="добавить точку в маршрут"
                      className="h-10 flex-1 text-body"
                      disabled={manualBusy}
                    />
                    <Button
                      type="button"
                      onClick={manualAdd}
                      disabled={manualBusy || !manualQuery.trim()}
                      size="icon"
                      className="h-10 w-10 shrink-0 rounded-full"
                      aria-label="найти и добавить точку"
                    >
                      {manualBusy ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                  {manualErr && (
                    <p className="text-meta text-destructive">{manualErr}</p>
                  )}
                  <button
                    type="button"
                    onClick={addEmptyWaypointToEnd}
                    className="inline-flex items-center gap-1 self-start rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />{' '}
                    пустая точка (выбрать кликом по карте)
                  </button>
                </section>

                {/* === Route History === */}
                <section className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-3 shadow-card">
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="flex items-center gap-1.5 text-label font-semibold">
                      <History
                        className="h-3.5 w-3.5 text-muted-foreground"
                        aria-hidden="true"
                      />
                      История
                      {routeHistory.length > 0 && (
                        <span className="rounded-full bg-muted px-1.5 py-0.5 text-badge font-normal text-muted-foreground">
                          {routeHistory.length}
                        </span>
                      )}
                    </h2>
                    {routeHistory.length > 0 && (
                      <button
                        type="button"
                        onClick={clearHistory}
                        className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-destructive"
                        title="очистить историю"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        очистить
                      </button>
                    )}
                  </div>

                  {routeHistory.length === 0 ? (
                    <p className="text-meta text-muted-foreground">
                      Построенные маршруты появятся здесь
                    </p>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {routeHistory.map((entry) => (
                        <HistoryItem
                          key={entry.id}
                          entry={entry}
                          onLoad={() => {
                            const restored: Waypoint[] = entry.places.map(
                              (p, i) => ({
                                id: i.toString(),
                                userInput: p.name,
                                placeId: p.id,
                                geocodeResults: [
                                  {
                                    title: p.name,
                                    description: p.category ?? undefined,
                                    selected: true,
                                    displaylnglat: [p.lon, p.lat] as [
                                      number,
                                      number,
                                    ],
                                    sourcelnglat: [p.lon, p.lat] as [
                                      number,
                                      number,
                                    ],
                                    key: i,
                                    addressindex: 0,
                                  },
                                ],
                              })
                            );
                            setWaypoint(
                              me
                                ? [meWaypoint(me.lat, me.lon), ...restored]
                                : restored
                            );
                            setPlaceDetails(
                              Object.fromEntries(
                                entry.places.map((p) => [
                                  p.id,
                                  {
                                    name: p.name,
                                    category: p.category,
                                    blurb: p.blurb ?? null,
                                    funFact: p.funFact ?? null,
                                    funFacts: p.funFacts ?? [],
                                    links: p.links ?? [],
                                    visitMinutes: p.visitMinutes ?? null,
                                    openingHours: p.openingHours ?? null,
                                    ticketPrice: p.ticketPrice ?? null,
                                    town: p.town ?? null,
                                    district: p.district ?? null,
                                  } satisfies PlaceDetails,
                                ])
                              )
                            );
                            setSummary(null);
                            refetchDirections();
                          }}
                          onRemove={() => removeFromHistory(entry.id)}
                        />
                      ))}
                    </div>
                  )}
                </section>
              </>
            )}
          </div>

          {/* ── Sticky footer: the one action the panel exists for. Outside the
              scroll area, so it is reachable at either snap point. ── */}
          {mode === 'plan' && (
            <footer className="shrink-0 border-t border-border bg-background px-4 py-3">
              {/* Polite live region: button text alone is not reliably announced. */}
              <span className="sr-only" aria-live="polite" aria-atomic="true">
                {busy ? 'Строю маршрут…' : ''}
              </span>
              <Button
                type="button"
                onClick={() => submitPrompt()}
                disabled={busy || !query.trim()}
                className="h-12 w-full rounded-xl bg-primary text-body font-semibold text-primary-foreground transition hover:bg-primary hover:brightness-[0.97] active:scale-[0.99] motion-reduce:active:scale-100 disabled:opacity-40"
              >
                {busy && (
                  <Loader2
                    className="h-4 w-4 animate-spin"
                    aria-hidden="true"
                  />
                )}
                {busy ? 'Строю маршрут…' : 'Построить маршрут'}
              </Button>
            </footer>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
};

// Party size stepper
interface StepperProps {
  label: string;
  testId: string;
  /** null = the tourist has not named a number yet. */
  value: number | null;
  min: number;
  max: number;
  onChange: (next: number | null) => void;
}

/**
 * A small −/+ stepper for a party count. It starts unset («—»): a number the
 * tourist never picked must not go to the agent, and stepping one below the
 * minimum clears the field instead of inventing a 0-person group.
 */
const Stepper = ({
  label,
  testId,
  value,
  min,
  max,
  onChange,
}: StepperProps) => (
  <div className="flex items-center gap-2">
    <span className="text-label">{label}</span>
    <div className="ml-auto flex items-center gap-1">
      <button
        type="button"
        aria-label={`убавить: ${label}`}
        data-testid={`${testId}-dec`}
        disabled={value == null}
        onClick={() =>
          onChange(value == null || value <= min ? null : value - 1)
        }
        className="flex h-7 w-7 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
      >
        <Minus className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <span
        data-testid={`${testId}-value`}
        className="w-5 text-center text-label font-semibold tabular-nums"
      >
        {value ?? '—'}
      </span>
      <button
        type="button"
        aria-label={`прибавить: ${label}`}
        data-testid={`${testId}-inc`}
        onClick={() => onChange(value == null ? min : Math.min(value + 1, max))}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  </div>
);

// History item component
interface HistoryItemProps {
  entry: RouteHistoryEntry;
  onLoad: () => void;
  onRemove: () => void;
}

const HistoryItem = ({ entry, onLoad, onRemove }: HistoryItemProps) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-border p-2.5 text-label">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onLoad}
          className="min-w-0 flex-1 truncate text-left font-medium transition-colors hover:text-primary"
          title={entry.query}
        >
          {entry.query.length > 35
            ? entry.query.slice(0, 35) + '…'
            : entry.query}
        </button>
        <div className="flex shrink-0 items-center gap-1">
          <span className="flex items-center gap-0.5 text-meta text-muted-foreground">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {entry.timeBudget <= 0 ? 'без лимита' : fmtMin(entry.timeBudget)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-destructive"
            title="удалить"
            aria-label="удалить из истории"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="mt-1 inline-flex items-center gap-1 text-meta text-muted-foreground transition-colors hover:text-foreground"
      >
        <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
        {entry.places.length} мест
        <ChevronDown
          className={`h-3.5 w-3.5 transition-transform ${
            expanded ? 'rotate-180' : ''
          }`}
          aria-hidden="true"
        />
      </button>
      {expanded && (
        <ol className="mt-1.5 flex flex-col gap-0.5 pl-4">
          {entry.places.map((p, i) => (
            <li
              key={p.id}
              className="flex items-center gap-1.5 text-meta text-muted-foreground"
            >
              <span className="h-4 w-4 rounded-full bg-muted text-center text-badge font-semibold leading-4 text-foreground">
                {i + 1}
              </span>
              <span className="truncate">{p.name}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};
