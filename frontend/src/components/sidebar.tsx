import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
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
  Minus,
  Plus,
  RotateCcw,
  Route as RouteIcon,
  Search,
  SlidersHorizontal,
  Sparkles,
  Undo2,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { cn } from '@/lib/utils';
import { meWaypoint, storedMeCoords } from '@/utils/me-waypoint';
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
import { HistoryTab } from './parts/history-tab';
import { PlanVerdict } from './parts/plan-verdict';
import { ItinerariesTab } from './parts/itineraries-tab';
import { useItineraries } from '@/hooks/use-itineraries';
import type { Itinerary } from '@/api/types';
import { guideModeFor } from './parts/guide-mode';
import { decimalRu } from '@/utils/plural';
import { newProgressId } from '@/api/progress';
import { WaypointList } from './waypoint-list';
import { Chip } from './parts/chip';
import { agentErrorMessage } from './parts/guide-format';
import { useRouteProgress } from '@/hooks/use-route-progress';
import {
  LONG_WAIT_SECONDS,
  routeElapsedSeconds,
  routeServerStageKey,
  type RouteStage,
} from './parts/route-progress';
import { Segmented, type SegmentedItem } from './parts/segmented';
import { PanelResizeHandle, usePanelWidth } from './parts/panel-resize';
import { LanguageSwitcher } from './parts/language-switcher';
import { StatTile, StatTiles } from './parts/stat-tiles';
import { StopsSkeleton, SummarySkeleton } from './parts/skeletons';
import type { AgentRouteResponse, AmenityStrength, ResultMode } from './types';
import {
  GUIDE_SHEET_CLASS,
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

/**
 * Time presets. 0 = «без ограничения»: no `time_budget_minutes` is sent at all
 * and the agent builds the full route. Anything >= 15 min is a real constraint.
 */
const buildTimeBudgetOptions = (t: TFunction) => [
  { value: 30, label: t('sidebar.budgets.b30') },
  { value: 45, label: t('sidebar.budgets.b45') },
  { value: 60, label: t('sidebar.budgets.b60') },
  { value: 120, label: t('sidebar.budgets.b120') },
  { value: 180, label: t('sidebar.budgets.b180') },
  { value: 240, label: t('sidebar.budgets.b240') },
  { value: 480, label: t('sidebar.budgets.b480') },
  { value: 0, label: t('sidebar.budgets.none') },
];

/**
 * Transport the tourist has. Empty value = "как удобно": nothing is sent and the
 * agent picks the costing that fits the query (a walk inside a town, a drive
 * across the область). Picking one sends it — transport is a real constraint.
 */
const buildTransportOptions = (
  t: TFunction
): Array<SegmentedItem<'' | Profile> & { costing?: string }> => [
  {
    value: 'pedestrian',
    label: t('sidebar.transport.pedestrian'),
    icon: Footprints,
    costing: 'pedestrian',
  },
  {
    value: 'bicycle',
    label: t('sidebar.transport.bicycle'),
    icon: Bike,
    costing: 'bicycle',
  },
  {
    value: 'car',
    label: t('sidebar.transport.car'),
    icon: Car,
    costing: 'auto',
  },
  { value: '', label: t('sidebar.transport.any'), icon: Sparkles },
];

/**
 * Panel views — the content a tourist browses.
 *
 * The guide is deliberately *not* one of them. As a fourth tab it read as «ещё
 * один раздел панели», while walking a route is a different activity with its
 * own screen: in a navigator you enter it with one explicit action and the
 * navigation UI takes over, tabs and all. So `guiding` is a separate state (see
 * the panel header and the footer), not a view.
 */
export type PanelView = 'plan' | 'history' | 'itineraries';

/**
 * The strip's tabs, in the order the tourist actually works: plan a route, take
 * a ready-made one, look back at history. Built from the dictionary rather than
 * kept as a constant, so switching the interface language moves the labels with
 * it — the order itself is the point and must not drift.
 */
const buildViews = (t: TFunction): SegmentedItem<PanelView>[] => [
  {
    value: 'plan',
    label: t('tabs.plan'),
    short: t('tabs.planShort'),
    icon: RouteIcon,
  },
  {
    value: 'itineraries',
    label: t('tabs.itineraries'),
    short: t('tabs.itinerariesShort'),
    icon: Sparkles,
  },
  { value: 'history', label: t('tabs.history'), icon: History },
];

/** One line under the panel title, per view. */
const buildViewSubtitles = (t: TFunction): Record<PanelView, string> => ({
  plan: t('tabSubtitles.plan'),
  itineraries: t('tabSubtitles.itineraries'),
  history: t('tabSubtitles.history'),
});

/**
 * One-tap starters.
 *
 * The label *is* what gets sent: the tourist sees «замки» or «castles» and that
 * word goes to the agent, which reads either language. Showing English but
 * sending Russian would be a lie about what was asked.
 */
interface HintChip {
  /** Stable id: the chip's text is a whole sentence, and it may be reworded. */
  id: string;
  text: string;
}

/**
 * The chips offered before anything is planned — how a tourist would start.
 */
const buildHintChips = (t: TFunction): HintChip[] => [
  { id: 'old-town', text: t('ask.chips.oldTown') },
  { id: 'castles-churches', text: t('ask.chips.castlesChurches') },
  { id: 'food', text: t('ask.chips.food') },
  { id: 'evening', text: t('ask.chips.evening') },
  { id: 'with-children', text: t('ask.chips.withChildren') },
];

/**
 * The chips offered once a route exists. Planning is over: from here the
 * tourist edits what they have, so a chip has to read like an instruction to
 * change the current route («убери музей»), not like a fresh request. Showing
 * the starting five again would invite them to start over by accident.
 */
const buildRefineHintChips = (t: TFunction): HintChip[] => [
  { id: 'refine-add-cafe', text: t('ask.chipsRefine.addCafe') },
  { id: 'refine-remove-museum', text: t('ask.chipsRefine.removeMuseum') },
  { id: 'refine-shorter', text: t('ask.chipsRefine.shorter') },
  { id: 'refine-only-churches', text: t('ask.chipsRefine.onlyChurches') },
  { id: 'refine-with-children', text: t('ask.chipsRefine.withChildren') },
];

/**
 * Options of the advanced filters. `code` is the canonical backend category
 * code (backend/agent/constants.CATEGORIES) — the value that goes to the
 * agent. The Russian label lives here only so W7 can centralize all strings
 * later; the sidebar keeps no translation dictionary of its own.
 */
interface FilterOption {
  /**
   * What the chip is called and remembered by. For a group («всё религиозное»)
   * this is an id of its own, not a category: the codes that actually go to the
   * agent are in `codes`.
   */
  code: string;
  /** Codes the option stands for; absent means just `code`. */
  codes?: string[];
  label: string;
}

/** Codes an option sends: a single category, or every code a group covers. */
const optionCodes = (option: FilterOption): string[] =>
  option.codes ?? [option.code];

/** A group counts as chosen only when all of its codes are. */
const optionSelected = (option: FilterOption, selected: string[]): boolean =>
  optionCodes(option).every((code) => selected.includes(code));

/** Choosing adds or clears the whole group — half of «всё религиозное» is a bug. */
const toggleOption = (
  set: Dispatch<SetStateAction<string[]>>,
  option: FilterOption
): void =>
  set((prev) =>
    optionSelected(option, prev)
      ? prev.filter((code) => !optionCodes(option).includes(code))
      : [...new Set([...prev, ...optionCodes(option)])]
  );

/** Result type: a ready itinerary, or a grouped catalogue to choose from. */
const buildResultModeOptions = (t: TFunction): SegmentedItem<ResultMode>[] => [
  { value: 'route', label: t('sidebar.resultModes.route') },
  { value: 'catalogue', label: t('sidebar.resultModes.catalogue') },
];

/** Themes: what the tourist wants more of (soft — they never force a detour). */
const buildInterestOptions = (t: TFunction): FilterOption[] => [
  { code: 'замок', label: t('sidebar.interests.castles') },
  { code: 'дворец', label: t('sidebar.interests.palaces') },
  { code: 'усадьба', label: t('sidebar.interests.estates') },
  { code: 'костёл', label: t('sidebar.interests.catholic') },
  { code: 'церковь', label: t('sidebar.interests.orthodox') },
  { code: 'монастырь', label: t('sidebar.interests.monasteries') },
  { code: 'музей', label: t('sidebar.interests.museums') },
  { code: 'памятник', label: t('sidebar.interests.monuments') },
  { code: 'архитектура', label: t('sidebar.interests.architecture') },
  { code: 'парк', label: t('sidebar.interests.parks') },
  // A shortcut for one traveller's whole taste: it sends four real codes rather
  // than inventing a parent category the data does not have.
  {
    code: 'религиозное',
    codes: ['костёл', 'церковь', 'храм', 'монастырь'],
    label: t('sidebar.interests.religious'),
  },
];

/**
 * Amenities a walk may need, each with a strength: «обязательно» goes out as a
 * hard service the route must serve, «желательно» as a soft interest.
 */
const buildAmenityOptions = (t: TFunction): FilterOption[] => [
  { code: 'туалет', label: t('sidebar.amenities.toilet') },
  { code: 'кафе', label: t('sidebar.amenities.cafe') },
  { code: 'ресторан', label: t('sidebar.amenities.restaurant') },
  { code: 'гостиница', label: t('sidebar.amenities.hotel') },
  { code: 'остановка транспорта', label: t('sidebar.amenities.transitStop') },
];

/** Categories to keep out of the route. */
const buildAvoidOptions = (t: TFunction): FilterOption[] => [
  { code: 'музей', label: t('sidebar.avoid.museums') },
  { code: 'кладбище', label: t('sidebar.avoid.cemeteries') },
  { code: 'инфраструктура', label: t('sidebar.avoid.infrastructure') },
  { code: 'гостиница', label: t('sidebar.avoid.hotels') },
  {
    code: 'религиозное',
    codes: ['костёл', 'церковь', 'храм', 'монастырь'],
    label: t('sidebar.avoid.religious'),
  },
];

const buildAllFilterOptions = (t: TFunction): FilterOption[] => [
  ...buildInterestOptions(t),
  ...buildAmenityOptions(t),
  ...buildAvoidOptions(t),
];

/** The visible name of a filter code; the code itself is never translated. */
const filterLabel = (code: string, t: TFunction): string =>
  buildAllFilterOptions(t).find(
    (o) => o.code === code || optionCodes(o).includes(code)
  )?.label ?? code;

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
const routeSubmitErrorText = (err: unknown, t: TFunction): string => {
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
    return t('sidebar.status.offline');
  }
  return msg;
};

/** The tourist asked for a toilet stop — RU туалет / санузел, or English toilet. */
const queryAsksForToilet = (q: string): boolean =>
  /туалет|санузел|toilet/i.test(q);

const toiletMissingWarning = (t: TFunction) => t('sidebar.status.noToilets');

/** «запрос отменён» — an abort is the tourist's own action, not a failure. */
const requestCancelled = (t: TFunction) => t('sidebar.status.cancelled');

/** A fetch aborted through AbortController (or a cancelled XHR). */
const isAbortError = (err: unknown): boolean =>
  err instanceof Error &&
  (err.name === 'AbortError' ||
    /aborted|abort/i.test(err.message) ||
    err.name === 'CanceledError');

const fmtMin = (m: number, t: TFunction) => {
  const mins = Math.max(0, Math.round(m));
  if (mins < 60) return t('sidebar.units.minutes', { count: mins });
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  // «1 ч 20 мин» / «1 hr 20 min»: the unit words come from the dictionary, the
  // numbers do not.
  const hourPart = t('sidebar.units.hours', { count: hours });
  return rest
    ? `${hourPart} ${t('sidebar.units.minutes', { count: rest })}`
    : hourPart;
};

/** «1,3 км» — Russian uses a comma as the decimal separator, never a dot. */
const fmtKm = (km: number, t: TFunction) => {
  const value = km >= 10 ? String(Math.round(km)) : decimalRu(km);
  return t('sidebar.units.km', { value });
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
/**
 * Fingerprint of the route as the guide will walk it.
 *
 * Taken from the waypoints — place id plus the *source* coordinates the guide
 * itself reads — and never from the API response. A key that differs by a metre
 * from the one the guide computes leaves a walked route with no history entry
 * to mark, which is exactly the bug this closes.
 */
const walkKey = (waypoints: readonly Waypoint[]) =>
  guideRouteKey(
    waypoints
      .filter((w) => w.id !== ME_WAYPOINT_ID)
      .map((w) => {
        const geo =
          w.geocodeResults.find((g) => g.selected) ?? w.geocodeResults[0];
        const [lon = 0, lat = 0] =
          geo?.sourcelnglat ?? geo?.displaylnglat ?? [];
        return {
          placeId: w.placeId,
          name: w.userInput || geo?.title || '?',
          lat,
          lon,
        };
      })
  );

/** Why the panel could not say where you are — a code, not a finished sentence,
 * so the line follows the interface language instead of freezing in one. */
type GeoReason = 'unsupported' | 'failedShort' | 'denied';

export const Sidebar = () => {
  const panelOpen = useCommonStore((s) => s.directionsPanelOpen);
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
  // Which panel view is open: building/refining the route, the past routes, or
  // the ready-made starting points. The guide is not a view — see `guiding`.
  const [mode, setMode] = useState<PanelView>('plan');
  // Guide mode: the panel becomes the navigator's screen. Entered by an explicit
  // action on a ready route, left by «выйти» in its own header.
  const [guiding, setGuiding] = useState(false);
  const setGuidingStore = useCommonStore((s) => s.setGuiding);
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
  const [geoReason, setGeoReason] = useState<GeoReason | null>(null);
  const [busy, setBusy] = useState(false);
  // ── Honest waiting ──────────────────────────────────────────────────────
  // `stage` is one of the stages the client can actually observe, `elapsed` is
  // the seconds that have passed, `abortRef` is what makes «отменить» real.
  const [stage, setStage] = useState<RouteStage>('requesting');
  const [elapsed, setElapsed] = useState(0);
  // The pipeline reports its own stages under this id, so the panel can name
  // them instead of guessing. `null` (no id, unknown id) keeps the client's own,
  // observable sentence on screen.
  const [progressId, setProgressId] = useState<string | null>(null);
  const serverStage = useRouteProgress(progressId, { enabled: busy });
  const abortRef = useRef<AbortController | null>(null);

  // Tick while a request is in flight. The interval is torn down with `busy`,
  // so a finished or cancelled request stops counting immediately.
  useEffect(() => {
    if (!busy) return;
    const startedAt = Date.now();
    setElapsed(0);
    const id = setInterval(
      () => setElapsed((Date.now() - startedAt) / 1000),
      250
    );
    return () => clearInterval(id);
  }, [busy]);

  /** Abort the in-flight /routes/generate request; the route is left as it was. */
  const cancelRouteRequest = useCallback(() => {
    abortRef.current?.abort();
  }, []);
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
  // The agent's own verdict on the last answer (why no route / what went
  // unmet). Kept beside the summary: both describe the same plan, and both
  // are cleared when the route is reset or replaced.
  const [verdict, setVerdict] = useState<AgentRouteResponse | null>(null);

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

  const { t } = useTranslation();
  // The docked width, dragged by the tourist and remembered across visits.
  const panel = usePanelWidth();
  const tabs = useMemo(() => buildViews(t), [t]);
  // Filters and presets are built from the dictionary where they are rendered:
  // their visible labels follow the language, while their `code`s never do —
  // those are the backend's own categories.
  const subtitles = useMemo(() => buildViewSubtitles(t), [t]);
  // Once a route exists the same field asks a different question, so it offers
  // different chips: instructions that edit what is already built.
  const hints = useMemo(
    () =>
      waypoints.some(
        (w) => w.id !== ME_WAYPOINT_ID && w.geocodeResults.length > 0
      )
        ? buildRefineHintChips(t)
        : buildHintChips(t),
    [t, waypoints]
  );

  const routeHistory = useDirectionsStore((s) => s.routeHistory);
  const addToHistory = useDirectionsStore((s) => s.addToHistory);
  const removeFromHistory = useDirectionsStore((s) => s.removeFromHistory);
  const clearHistory = useDirectionsStore((s) => s.clearHistory);
  const markWalked = useDirectionsStore((s) => s.markWalked);

  // Ready-made routes: fetched only when the tab that shows them is opened.
  const {
    itineraries,
    missing: itinerariesMissing,
    isLoading: itinerariesLoading,
    error: itinerariesError,
    reload: reloadItineraries,
  } = useItineraries({ enabled: mode === 'itineraries' });

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
        setGeoReason('unsupported');
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
          setGeoReason('failedShort');
          return null;
        }
        const coords = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
        };
        setMe(coords);
        setGeoState('ok');
        setGeoReason(null);
        // Keep my position as waypoint 0 so the map shows it and the line the
        // webapp draws starts there too.
        const current = useDirectionsStore.getState().waypoints;
        setWaypoint([
          meWaypoint(coords.lat, coords.lon, t('sidebar.ui.myLocation')),
          ...current.filter((w) => w.id !== ME_WAYPOINT_ID),
        ]);
        if (!silent) refetchDirections();
        return coords;
      } catch (err) {
        // 1 === PERMISSION_DENIED in the Geolocation API
        const code = (err as GeolocationPositionError | undefined)?.code;
        setGeoState('denied');
        // 1 === PERMISSION_DENIED; anything else is «could not determine».
        setGeoReason(code === 1 ? 'denied' : 'failedShort');
        return null;
      }
    },
    [refetchDirections, setWaypoint, t]
  );

  // Ages the tourist named, in order, deduplicated. Empty = unknown, and then
  // `party_children_ages` is not sent at all.
  const childrenAges = useMemo(
    () => [...new Set(parseChildAges(childrenAgesText))],
    [childrenAgesText]
  );
  const hardServices = useMemo(
    () =>
      buildAmenityOptions(t)
        .filter((o) => amenities[o.code] === 'hard')
        .map((o) => o.code),
    [amenities, t]
  );
  const softAmenities = useMemo(
    () =>
      buildAmenityOptions(t)
        .filter((o) => amenities[o.code] === 'soft')
        .map((o) => o.code),
    [amenities, t]
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
    if (partyAdults != null)
      out.push(t('sidebar.summary.adults', { count: partyAdults }));
    if (partyChildren != null)
      out.push(t('sidebar.summary.children', { count: partyChildren }));
    if (childrenAges.length > 0)
      out.push(t('sidebar.summary.ages', { ages: childrenAges.join(', ') }));
    for (const code of hardServices)
      out.push(t('sidebar.summary.hard', { label: filterLabel(code, t) }));
    for (const code of softAmenities)
      out.push(t('sidebar.summary.soft', { label: filterLabel(code, t) }));
    for (const code of interests)
      out.push(t('sidebar.summary.interest', { label: filterLabel(code, t) }));
    for (const code of avoid)
      out.push(t('sidebar.summary.avoid', { label: filterLabel(code, t) }));
    if (resultMode === 'catalogue') out.push(t('sidebar.ui.catalogue'));
    if (roundTrip) out.push(t('sidebar.ui.roundTrip'));
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
    t,
  ]);

  /** Click the strength a row already has to clear it — off is a valid state. */
  const toggleAmenity = (code: string, next: AmenityStrength) =>
    setAmenities((prev) => {
      const copy = { ...prev };
      if (copy[code] === next) delete copy[code];
      else copy[code] = next;
      return copy;
    });

  /**
   * Open a past route again: the same stops, on the map, with their facts — what
   * the history row promised. Nothing is re-requested from the planner; the
   * route that gets drawn is the one that was stored.
   */
  const restoreFromHistory = (entry: RouteHistoryEntry) => {
    const restored: Waypoint[] = entry.places.map((p, i) => ({
      id: i.toString(),
      userInput: p.name,
      placeId: p.id,
      geocodeResults: [
        {
          title: p.name,
          description: p.category ?? undefined,
          selected: true,
          displaylnglat: [p.lon, p.lat] as [number, number],
          sourcelnglat: [p.lon, p.lat] as [number, number],
          key: i,
          addressindex: 0,
        },
      ],
    }));
    setWaypoint(
      me
        ? [meWaypoint(me.lat, me.lon, t('sidebar.ui.myLocation')), ...restored]
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
            // A restored route shows the same picture it showed when it was built.
            photo: p.photo ?? null,
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
    setVerdict(null);
    refetchDirections();
  };

  /**
   * Show a ready-made route on the map.
   *
   * The stops are real places from the dataset, so this is a plain hand-over:
   * waypoints and their facts go into the store and the router draws the line.
   * No request to the model — that is the point of the tab. The transport comes
   * from the itinerary, the time budget is left exactly as the tourist set it
   * (a curated «осмотр» figure is not a budget they chose).
   */
  const openItinerary = (itinerary: Itinerary) => {
    const stops = itinerary.stops;
    const restored: Waypoint[] = stops.map((stop, i) => ({
      id: i.toString(),
      userInput: stop.name,
      placeId: stop.place_id,
      geocodeResults: [
        {
          title: stop.name,
          description: stop.category ?? undefined,
          selected: true,
          displaylnglat: [stop.lon, stop.lat] as [number, number],
          sourcelnglat: [stop.lon, stop.lat] as [number, number],
          key: i,
          addressindex: 0,
        },
      ],
    }));
    // replan=false: the router is called once below, with the new stops.
    setTransportEverywhere(itinerary.transport, false);
    const next = me
      ? [meWaypoint(me.lat, me.lon, t('sidebar.ui.myLocation')), ...restored]
      : restored;
    setWaypoint(next);
    // A ready-made route is still a route someone may walk, and a walk needs a
    // history entry to attach to — without this, «пройдено» would be missing for
    // exactly the routes most people take.
    addToHistory({
      query: itinerary.title,
      timeBudget: itinerary.visit_minutes,
      routeKey: walkKey(next),
      places: stops.map((stop) => ({
        id: stop.place_id,
        name: stop.name,
        category: stop.category,
        lat: stop.lat,
        lon: stop.lon,
        blurb: stop.blurb ?? null,
        funFact: stop.fun_fact ?? null,
      })),
    });
    setPlaceDetails(
      Object.fromEntries(
        stops.map((stop) => [
          stop.place_id,
          {
            name: stop.name,
            category: stop.category,
            blurb: stop.blurb ?? null,
            funFact: stop.fun_fact ?? null,
            funFacts: stop.fun_facts ?? [],
            photo: stop.photo ?? null,
            links: stop.links ?? [],
            visitMinutes: stop.visit_minutes ?? null,
            openingHours: stop.opening_hours ?? null,
            ticketPrice: stop.ticket_price ?? null,
            town: stop.town ?? null,
            district: stop.district ?? null,
          } satisfies PlaceDetails,
        ])
      )
    );
    setSummary(null);
    setVerdict(null);
    refetchDirections();
    setMode('plan');
  };

  /** Build (or refine) a route. */
  const submitPrompt = async (text?: string) => {
    const q = (text ?? query).trim();
    if (!q || busy) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setStage('requesting');
    // A fresh id per attempt: the pipeline reports under it, and a stale id from
    // a previous request must never colour this one's progress.
    const requestProgressId = newProgressId();
    setProgressId(requestProgressId);
    setStatus(null);
    setSummary(null);
    setVerdict(null);

    // A fresh position wins over the cached one; the position already stored as
    // waypoint 0 is usable even when a previous attempt failed, because the map
    // button may have found it in the meantime. Only then, and only if the
    // browser has not refused us, do we ask again.
    let origin = me ?? storedMeCoords(useDirectionsStore.getState().waypoints);
    if (!origin && geoState !== 'denied') {
      origin = await locateMe(true);
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
        progress_id?: string;
      } = { query: q, progress_id: requestProgressId };
      if (timeBudget >= 15) body.time_budget_minutes = timeBudget;
      const chosen = buildTransportOptions(t).find(
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
            name: wp.userInput || geo.title || t('sidebar.waypoints.unnamed'),
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
        // Cancel is real: the request is aborted, not just visually dismissed.
        signal: controller.signal,
      });
      if (!r.ok) {
        // A 404 here means the app is talking to the wrong server, not that
        // nothing was found — say that instead of blaming the query.
        throw new Error(agentErrorMessage(r.status));
      }
      const data = (await r.json()) as AgentRouteResponse;
      // An infeasible plan carries no points at all, so the honest answer is
      // the verdict — which requirements failed and why — not the generic
      // "fewer than 2 places found": the tourist needs the reason, not a count.
      // The query stays in the field so it can be adjusted and sent again.
      if (data.status === 'infeasible') {
        setVerdict(data);
        return;
      }
      // A degraded plan still has a route; the verdict rides along with it.
      if (data.status === 'degraded') setVerdict(data);
      const pts = data.points ?? [];
      if (pts.length < 2) {
        throw new Error(t('sidebar.status.fewPlaces'));
      }

      // The agent tells us which transport it planned for; adopt it (unless the
      // tourist picked one) so the map's own line uses the same costing.
      if (!transportRef.current && data.costing) {
        const match = buildTransportOptions(t).find(
          (o) => o.costing === data.costing
        );
        if (match) setTransportEverywhere(match.value, false);
      }

      const start: Waypoint[] = origin
        ? [meWaypoint(origin.lat, origin.lon, t('sidebar.ui.myLocation'))]
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
              photo: p.photo ?? null,
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
      // The plan has arrived and the line is still being drawn: both facts are
      // observable client-side, so both may be stated. The promise is awaited
      // at the end of this block so «рисую маршрут» stays on screen exactly as
      // long as the line is missing.
      setStage('drawing');
      const drawing = refetchDirections();

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
        // The route's fingerprint, so the guide (which walks these very stops)
        // can find this entry later and record what was actually walked.
        routeKey: walkKey([...start, ...placeWaypoints]),
        places: pts.map((p) => ({
          id: p.id,
          name: p.name,
          category: p.category ?? null,
          lat: p.lat,
          lon: p.lon,
          blurb: p.blurb ?? null,
          funFact: p.fun_fact ?? null,
          funFacts: p.fun_facts ?? [],
          photo: p.photo ?? null,
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
        setStatus({ kind: 'warn', text: toiletMissingWarning(t) });
      }

      setQuery('');
      // Hold the panel's «рисую маршрут» status until the line is on the map.
      await drawing;
    } catch (e) {
      // The tourist's own cancel is not a failure and leaves the route alone.
      if (isAbortError(e)) {
        setStatus({ kind: 'ok', text: requestCancelled(t) });
      } else {
        setStatus({ kind: 'err', text: routeSubmitErrorText(e, t) });
      }
    } finally {
      abortRef.current = null;
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
    setWaypoint(
      me
        ? [meWaypoint(me.lat, me.lon, t('sidebar.ui.myLocation')), ...empties]
        : empties
    );
    setStatus(null);
    setSummary(null);
    setVerdict(null);
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
        setManualErr(t('sidebar.status.nothingFound'));
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

  // Guide mode is worth offering only when there is something to walk: two
  // stops is the minimum that makes a route. The guide itself explains the
  // empty case, but the panel should not dangle the action before then.
  const canGuide = guideStops.length >= 2;

  /**
   * Keep the route's history entry in step with the walk.
   *
   * The guide reports progress; this is what turns it into «пройдено 3 из 5» in
   * the history, where it is still true after the guide is closed. Identical
   * progress is not written twice (the guide re-reports on every render of its
   * progress), and a route with fewer than two stops has no entry to mark.
   */
  const lastWalkedRef = useRef('');
  const handleWalked = useCallback(
    ({ visited, total }: { visited: number; total: number }) => {
      const routeKey = guideRouteKey(guideStops);
      if (!routeKey || total < 2) return;
      const stamp = `${routeKey}#${visited}/${total}`;
      if (lastWalkedRef.current === stamp) return;
      lastWalkedRef.current = stamp;
      markWalked({ routeKey, visited, total });
    },
    [guideStops, markWalked]
  );

  const stopCount = waypoints.filter(
    (w) => w.id !== ME_WAYPOINT_ID && w.geocodeResults.length > 0
  ).length;
  const hasRoute = stopCount > 0;
  const geoBadge = useMemo(() => {
    switch (geoState) {
      case 'ok':
        return { text: t('sidebar.geo.startIsMe'), tone: 'text-emerald-600' };
      case 'locating':
        return {
          text: t('sidebar.geo.searching'),
          tone: 'text-muted-foreground',
        };
      case 'denied':
        return {
          text: geoReason
            ? `${t('sidebar.geo.label')}: ${t(`sidebar.geo.${geoReason}`)}`
            : t('sidebar.geo.label'),
          tone: 'text-amber-600',
        };
      default:
        return {
          text: t('sidebar.geo.startUnset'),
          tone: 'text-muted-foreground',
        };
    }
  }, [geoState, geoReason, t]);

  // The guide is a mode of the whole app, not just of the panel: while it runs
  // the map follows the walk (see the map's navigator effect). That only works
  // if the map can see the mode.
  useEffect(() => {
    setGuidingStore(guiding);
  }, [guiding, setGuidingStore]);

  // The map's own overlays (the route-line pill, the planner entry) are siblings
  // of the panel, not children, so the `--panel-width` set on the panel below
  // never reached them: they stayed at left-4 and were drawn on top of the
  // panel. Publish the width where both sides can read it, and 0 while the
  // panel is closed so the map keeps its full width.
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty(
      '--panel-width',
      panelOpen ? `${panel.width}px` : '0px'
    );
    return () => {
      root.style.setProperty('--panel-width', '0px');
    };
  }, [panelOpen, panel.width]);

  // Same door for the sheet's HEIGHT, and for the same reason: the map's
  // floating controls are siblings of the sheet, so they cannot see how tall it
  // is and sat behind it on a phone. The number here must stay in step with the
  // classes above — a mobile viewport is the only place it is non-zero, because
  // from md up the panel is a column and covers nothing.
  useEffect(() => {
    const root = document.documentElement;
    const publish = () => {
      // An innerWidth check, not matchMedia: jsdom has no matchMedia, and the
      // unguarded call threw inside this effect — every Sidebar test failed at
      // once. 768 is Tailwind's `md`, the breakpoint the sheet classes use.
      const mobile = window.innerWidth < 768;
      const height = !mobile
        ? '0px'
        : guiding && snap === 'peek'
          ? '26dvh'
          : snap === 'full'
            ? '90dvh'
            : '45dvh';
      root.style.setProperty('--sheet-h', height);
    };
    publish();
    window.addEventListener('resize', publish);
    return () => {
      window.removeEventListener('resize', publish);
      root.style.setProperty('--sheet-h', '0px');
    };
  }, [guiding, snap]);

  return (
    <Sheet open={panelOpen} modal={false}>
      <SheetContent
        side="left"
        className={cn(
          PANEL_SHEET_CLASS,
          // While walking, the sheet is a strip and the map is the navigator;
          // a drag to 'full' still opens everything.
          guiding && snap === 'peek'
            ? GUIDE_SHEET_CLASS
            : SHEET_SNAP_CLASS[snap]
        )}
        style={{ '--panel-width': `${panel.width}px` } as CSSProperties}
      >
        <div className="flex h-full min-h-0 flex-col">
          {/* === Ask ===
              First in the DOM, so the query field is the panel's first tab
              stop — the tourist lands on the thing the panel is for, not on the
              close button. `order-2` keeps it visually under the header. */}
          {!guiding && mode === 'plan' && (
            <section className="order-2 shrink-0 border-b border-border px-4 pb-3 pt-3">
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
                        ? t('ask.placeholderRefine')
                        : t('ask.placeholder')
                    }
                    aria-label={t('ask.label')}
                    className="min-h-0 flex-1 resize-none border-0 bg-transparent p-0 text-body leading-6 shadow-none focus-visible:ring-0 max-md:min-h-11"
                    rows={1}
                    disabled={busy}
                  />
                </div>
              </div>
              <div
                role="group"
                aria-label={t('sidebar.plan.hintsAria')}
                className="mt-2 flex flex-wrap gap-1.5"
              >
                {hints.map((hint) => (
                  <Chip
                    key={hint.id}
                    onClick={() => {
                      setQuery(hint.text);
                      taRef.current?.focus();
                    }}
                    data-testid={`hint-${hint.id}`}
                  >
                    {hint.text}
                  </Chip>
                ))}
              </div>
            </section>
          )}

          {/* ── Header. Planning and browsing keep the tab strip, the title and
              the quiet close row; guide mode gets its own bar instead — the
              navigator's «маршрут идёт» state, with one obvious way out and no
              tabs to read past. All of it sits in normal flow, so nothing can
              slide under the close button. ── */}
          <header className="order-1 shrink-0 border-b border-border px-4 pb-2.5">
            <SheetDragHandle snap={snap} handleProps={handleProps} />
            {guiding ? (
              <div className="mt-1.5 flex items-center gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2 rounded-2xl bg-primary px-3 py-2 text-primary-foreground">
                  {/* Radix still needs a title for the dialog. */}
                  <SheetTitle className="sr-only">
                    {t('sidebar.ui.guide')}
                  </SheetTitle>
                  <Compass className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-label font-semibold">
                      {t('guide.title')}
                    </p>
                    <p className="truncate text-badge opacity-90">
                      {t('guide.stops', { count: guideStops.length })} ·{' '}
                      {guideModeFor(transport).label}
                    </p>
                  </div>
                  <button
                    type="button"
                    data-testid="guide-exit"
                    onClick={() => setGuiding(false)}
                    className="shrink-0 rounded-full bg-primary-foreground/15 px-2.5 py-1 text-badge font-semibold transition-colors hover:bg-primary-foreground/25 max-md:min-h-11 pointer-coarse:min-h-11"
                  >
                    {t('guide.exit')}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <Segmented
                    items={tabs}
                    value={mode}
                    onChange={setMode}
                    label={t('sidebar.plan.tabsAria')}
                    className="min-w-0 flex-1"
                    testId={(value) => `mode-${value}`}
                  />
                  {/* No ✕ here: the panel is opened and closed by its own handle
                      on the map's left edge — one control for one thing, and it
                      cannot fall out of step with the panel's state. */}
                  <LanguageSwitcher className="shrink-0" />
                </div>
                <div className="mt-2.5 flex min-w-0 items-center gap-2">
                  <RouteIcon
                    className="h-4 w-4 shrink-0 text-primary"
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <SheetTitle className="truncate text-body">
                      {t('app.title')}
                    </SheetTitle>
                    {/* Radix wants a description for the dialog; the visible
                        line below is the same sentence, so keep it out of the
                        a11y tree. */}
                    <SheetDescription className="sr-only">
                      {t('app.description')}
                    </SheetDescription>
                    <p className="truncate text-meta text-muted-foreground">
                      {subtitles[mode]}
                    </p>
                  </div>
                </div>
              </>
            )}
          </header>

          {/* ── Body: the only part that scrolls. ── */}
          <div className="slim-scroll order-3 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-6 pt-3">
            {guiding ? (
              // key: a rebuilt route remounts the guide, so the walk restarts
              // instead of carrying progress from the route that no longer exists
              <GuidePanel
                key={guideRouteKey(guideStops)}
                stops={guideStops}
                transport={transport}
                onWalked={handleWalked}
              />
            ) : mode === 'history' ? (
              <HistoryTab
                entries={routeHistory}
                onRestore={restoreFromHistory}
                onRemove={removeFromHistory}
                onClear={clearHistory}
              />
            ) : mode === 'itineraries' ? (
              <ItinerariesTab
                itineraries={itineraries}
                missing={itinerariesMissing}
                isLoading={itinerariesLoading}
                error={itinerariesError}
                onReload={reloadItineraries}
                onOpen={openItinerary}
                disabled={busy}
              />
            ) : (
              <>
                {/* === Constraints: time + transport. Both are the user's call —
                    nothing is invented for them. === */}
                <section className="flex flex-col gap-2.5">
                  <div className="flex flex-col gap-1.5">
                    <span
                      id="time-budget-label"
                      className="text-meta text-muted-foreground"
                    >
                      {t('sidebar.ui.timeLabel')}
                    </span>
                    <div
                      role="group"
                      aria-labelledby="time-budget-label"
                      className="flex flex-wrap gap-1.5"
                    >
                      {buildTimeBudgetOptions(t).map((option) => (
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
                      {t('sidebar.ui.transportLabel')}
                    </span>
                    <Segmented
                      items={buildTransportOptions(t)}
                      value={transport}
                      onChange={setTransportEverywhere}
                      label={t('sidebar.ui.transportLabel')}
                      stacked
                      disabled={busy}
                      testId={(value) => `transport-${value || 'any'}`}
                    />
                  </div>

                  <button
                    type="button"
                    onClick={() => void locateMe()}
                    disabled={geoState === 'locating'}
                    className="flex w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-meta transition-colors hover:bg-muted disabled:opacity-60 max-md:min-h-11 pointer-coarse:min-h-11"
                    title={t('sidebar.plan.geoOverride')}
                  >
                    <LocateFixed
                      className={
                        geoState === 'ok'
                          ? 'h-3.5 w-3.5 shrink-0 text-emerald-600'
                          : 'h-3.5 w-3.5 shrink-0 text-muted-foreground'
                      }
                    />
                    <span className={`truncate ${geoBadge.tone}`}>
                      {geoState === 'idle'
                        ? t('sidebar.geo.detect')
                        : geoBadge.text}
                    </span>
                    <span className="ml-auto shrink-0 text-muted-foreground">
                      {geoState === 'locating' ? '…' : t('sidebar.ui.refresh')}
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
                    className="flex w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-meta transition-colors hover:bg-muted max-md:min-h-11 pointer-coarse:min-h-11"
                  >
                    <SlidersHorizontal
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span>{t('sidebar.ui.moreFilters')}</span>
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
                      <span>{t('sidebar.ui.considering')}</span>
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
                          {t('sidebar.plan.filtersPrecedence')}
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
                          {t('sidebar.plan.partyLabel')}
                        </span>
                        <Stepper
                          label={t('sidebar.plan.adults')}
                          testId="party-adults"
                          value={partyAdults}
                          min={1}
                          max={50}
                          onChange={setPartyAdults}
                        />
                        <Stepper
                          label={t('sidebar.plan.children')}
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
                              {t('sidebar.plan.childrenAges')}
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
                          {t('sidebar.plan.amenities')}
                        </span>
                        {buildAmenityOptions(t).map((option) => (
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
                                {t('sidebar.plan.hard')}
                              </Chip>
                              <Chip
                                selected={amenities[option.code] === 'soft'}
                                onClick={() =>
                                  toggleAmenity(option.code, 'soft')
                                }
                                data-testid={`amenity-${option.code}-soft`}
                                className="h-7 px-2 text-meta"
                              >
                                {t('sidebar.plan.soft')}
                              </Chip>
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Themes: soft by nature, they never force a detour. */}
                      <div className="flex flex-col gap-1.5">
                        <span className="text-meta text-muted-foreground">
                          {t('sidebar.plan.interests')}
                        </span>
                        <div
                          role="group"
                          aria-label={t('sidebar.plan.interests')}
                          className="flex flex-wrap gap-1.5"
                        >
                          {buildInterestOptions(t).map((option) => (
                            <Chip
                              key={option.code}
                              selected={optionSelected(option, interests)}
                              onClick={() => toggleOption(setInterests, option)}
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
                          {t('sidebar.plan.avoid')}
                        </span>
                        <div
                          role="group"
                          aria-label={t('sidebar.plan.avoid')}
                          className="flex flex-wrap gap-1.5"
                        >
                          {buildAvoidOptions(t).map((option) => (
                            <Chip
                              key={option.code}
                              selected={optionSelected(option, avoid)}
                              onClick={() => toggleOption(setAvoid, option)}
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
                          {t('sidebar.plan.resultType')}
                        </span>
                        <Segmented
                          items={buildResultModeOptions(t)}
                          value={resultMode}
                          onChange={setResultMode}
                          label={t('sidebar.plan.resultTypeAria')}
                          disabled={busy}
                          testId={(value) => `result-mode-${value}`}
                        />
                        <Chip
                          selected={roundTrip}
                          onClick={() => setRoundTrip((v) => !v)}
                          data-testid="round-trip"
                          className="mt-0.5 self-start"
                        >
                          {t('sidebar.ui.roundTrip')}
                        </Chip>
                      </div>
                    </div>
                  )}
                </section>

                {/* === Route === */}
                <section className="flex flex-col gap-2.5">
                  {/* The plan's own verdict, above the stops: for a degraded plan
                      it heads the summary it qualifies, and for an infeasible
                      one it shows at all precisely because there is no route —
                      that is when the tourist most needs to hear why. */}
                  <PlanVerdict response={verdict} />
                  {hasRoute && (
                    <div className="flex items-center justify-between gap-2">
                      <h2 className="text-label font-semibold">
                        {t('sidebar.ui.route')}
                      </h2>
                      <button
                        type="button"
                        onClick={reset}
                        className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:min-h-11 pointer-coarse:min-h-11"
                        title={t('sidebar.plan.resetHint')}
                      >
                        <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                        {t('sidebar.plan.reset')}
                      </button>
                    </div>
                  )}

                  {summary ? (
                    <>
                      <StatTiles>
                        {/* The tile inflects the noun itself: «2 точки», not
                            «2 точек» — the count is right there. */}
                        <StatTile
                          value={summary.stops}
                          count={summary.stops}
                          unit="points"
                        />
                        <StatTile
                          value={
                            summary.km != null ? fmtKm(summary.km, t) : '—'
                          }
                          label={t('sidebar.plan.length')}
                        />
                        <StatTile
                          value={summary.travelMinutes}
                          label={t('sidebar.plan.travelMinutes')}
                        />
                      </StatTiles>
                      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                          {t('sidebar.plan.travelSummary', {
                            value: fmtMin(summary.walkMinutes, t),
                          })}
                        </span>
                        <span>
                          {t('sidebar.plan.visitSummary', {
                            value: fmtMin(summary.visitMinutes, t),
                          })}
                        </span>
                        <span>
                          {summary.budgetMinutes
                            ? t('sidebar.plan.budgetSummary', {
                                value: fmtMin(summary.budgetMinutes, t),
                              })
                            : t('sidebar.ui.noLimit')}
                        </span>
                        {!summary.fits && (
                          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700">
                            {t('sidebar.plan.overBudget')}
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
                        {t('sidebar.ui.emptyStops')}
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
                                      ? t('sidebar.plan.refinementAdded', {
                                          names: entry.added.join(', '),
                                        })
                                      : '',
                                    entry.removed.length
                                      ? t('sidebar.plan.refinementRemoved', {
                                          names: entry.removed.join(', '),
                                        })
                                      : '',
                                  ]
                                    .filter(Boolean)
                                    .join(' · ') ||
                                  t('sidebar.status.noChanges')
                                }
                                className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-meta text-muted-foreground"
                              >
                                {entry.instruction}
                              </span>
                              {entry.added.length > 0 && (
                                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-meta text-primary">
                                  {t('sidebar.plan.addedCount', {
                                    count: entry.added.length,
                                  })}
                                </span>
                              )}
                              {entry.removed.length > 0 && (
                                <span className="rounded-full bg-muted px-2 py-0.5 text-meta text-muted-foreground">
                                  {t('sidebar.plan.removedCount', {
                                    count: entry.removed.length,
                                  })}
                                </span>
                              )}
                              {entry.added.length === 0 &&
                                entry.removed.length === 0 && (
                                  <span className="rounded-full bg-muted px-2 py-0.5 text-meta text-muted-foreground">
                                    {t('sidebar.status.noChanges')}
                                  </span>
                                )}
                            </span>
                          ))}
                          {excludedPlaceIds.length > 0 && (
                            <span
                              data-testid="excluded-chip"
                              className="rounded-full bg-destructive/10 px-2.5 py-0.5 text-meta text-destructive"
                            >
                              {t('sidebar.plan.excludedManual', {
                                count: excludedPlaceIds.length,
                              })}
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
                              text: t('sidebar.status.restoredPrevious'),
                            });
                            refetchDirections();
                          }}
                          disabled={routeSnapshots.length === 0}
                          className="h-9 rounded-full px-3 text-label disabled:opacity-40 max-md:h-11 pointer-coarse:h-11"
                        >
                          <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
                          {t('sidebar.plan.undoRefinement')}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => {
                            resetRoute();
                            reset();
                          }}
                          className="h-9 rounded-full px-3 text-label font-normal text-muted-foreground hover:text-foreground max-md:h-11 pointer-coarse:h-11"
                        >
                          <RotateCcw
                            className="h-3.5 w-3.5"
                            aria-hidden="true"
                          />
                          {t('sidebar.plan.newRoute')}
                        </Button>
                      </div>
                    </div>
                  )}
                </section>

                {/* === Manual add === */}
                <section className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-3 shadow-card">
                  <h2 className="text-label font-semibold">
                    {t('sidebar.ui.addPoint')}
                  </h2>
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
                      aria-label={t('sidebar.plan.manualAria')}
                      className="h-10 flex-1 text-body max-md:h-11"
                      disabled={manualBusy}
                    />
                    <Button
                      type="button"
                      onClick={manualAdd}
                      disabled={manualBusy || !manualQuery.trim()}
                      size="icon"
                      className="h-10 w-10 shrink-0 rounded-full max-md:h-11 max-md:w-11"
                      aria-label={t('sidebar.plan.manualSearchAria')}
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
                    className="inline-flex items-center gap-1 self-start rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:min-h-11 pointer-coarse:min-h-11"
                  >
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />{' '}
                    {t('sidebar.ui.emptyPoint')}
                  </button>
                </section>
              </>
            )}
          </div>

          {/* ── Sticky footer: the one action the panel exists for. Outside the
              scroll area, so it is reachable at either snap point. The guide
              brings its own actions, so the footer steps out of its way. ── */}
          {!guiding && mode === 'plan' && (
            <footer className="order-4 shrink-0 border-t border-border bg-background px-4 py-3">
              {/* Honest waiting: only what the client can observe — the request
                  is in flight, or the plan has arrived and the line is being
                  drawn — plus the seconds that have passed and a real cancel.
                  No invented stages, and no bare spinner for 23 seconds. */}
              {busy && (
                <div
                  role="status"
                  aria-live="polite"
                  data-testid="route-progress"
                  data-stage={stage}
                  className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-muted px-3 py-2 text-meta"
                >
                  <Loader2
                    className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span className="text-foreground">
                    {serverStage
                      ? t(routeServerStageKey(serverStage)!)
                      : t(
                          stage === 'requesting'
                            ? 'sidebar.progress.waitingRequest'
                            : 'sidebar.progress.waitingLine'
                        )}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {t('sidebar.progress.elapsed', {
                      count: routeElapsedSeconds(elapsed),
                    })}
                  </span>
                  <button
                    type="button"
                    onClick={cancelRouteRequest}
                    data-testid="route-cancel"
                    className="ml-auto inline-flex h-8 shrink-0 items-center rounded-full border border-border bg-card px-3 text-label font-medium text-foreground transition-colors hover:bg-muted max-md:h-11 pointer-coarse:h-11"
                  >
                    {t('sidebar.progress.cancel')}
                  </button>
                  {elapsed >= LONG_WAIT_SECONDS && (
                    <span className="basis-full text-muted-foreground">
                      {t('sidebar.progress.longWait')}
                    </span>
                  )}
                </div>
              )}
              {/* Entering the guide is its own, louder action: walking a route
                  is a different activity from planning one, and in a navigator
                  it is a button you press once, not a tab you visit. */}
              {canGuide && (
                <Button
                  type="button"
                  data-testid="guide-enter"
                  onClick={() => setGuiding(true)}
                  className="mb-2 h-12 w-full rounded-xl bg-primary text-body font-semibold text-primary-foreground motion-safe:transition hover:brightness-[0.97] active:scale-[0.99] motion-reduce:active:scale-100"
                >
                  <Compass className="h-4 w-4" aria-hidden="true" />
                  {t('guide.enter')}
                </Button>
              )}
              <Button
                type="button"
                data-testid="build-route"
                onClick={() => submitPrompt()}
                disabled={busy || !query.trim()}
                className={cn(
                  'h-12 w-full rounded-xl text-body font-semibold transition hover:brightness-[0.97] active:scale-[0.99] motion-reduce:active:scale-100 disabled:opacity-40',
                  // With a route in hand, starting it is the hero action — the
                  // build button steps back to the quieter style.
                  canGuide
                    ? 'bg-secondary text-secondary-foreground'
                    : 'bg-primary text-primary-foreground'
                )}
              >
                {busy && (
                  <Loader2
                    className="h-4 w-4 animate-spin"
                    aria-hidden="true"
                  />
                )}
                {busy ? t('sidebar.ui.plan') : t('sidebar.ui.build')}
              </Button>
            </footer>
          )}
        </div>
        {/* Last in the DOM on purpose: it is positioned absolutely on the right
            edge, and the panel's first tab stop must stay the query field —
            not a resize handle. */}
        <PanelResizeHandle
          width={panel.width}
          resizing={panel.resizing}
          props={panel.handleProps}
          label={t('panel.resize')}
        />
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
}: StepperProps) => {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <span className="text-label">{label}</span>
      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          aria-label={t('sidebar.plan.decrement', { label })}
          data-testid={`${testId}-dec`}
          disabled={value == null}
          onClick={() =>
            onChange(value == null || value <= min ? null : value - 1)
          }
          className="flex h-7 w-7 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 max-md:h-11 max-md:w-11 pointer-coarse:h-11 pointer-coarse:w-11"
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
          aria-label={t('sidebar.plan.increment', { label })}
          data-testid={`${testId}-inc`}
          onClick={() =>
            onChange(value == null ? min : Math.min(value + 1, max))
          }
          className="flex h-7 w-7 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:h-11 max-md:w-11 pointer-coarse:h-11 pointer-coarse:w-11"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
};
