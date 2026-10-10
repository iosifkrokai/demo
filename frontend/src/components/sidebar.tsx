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
  MapPin,
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
import { MobileSection } from './mobile/mobile-section';
import { MOBILE_MAX_WIDTH } from './mobile/use-is-mobile';
import {
  MOBILE_GUIDE_HEIGHT,
  MOBILE_SHEET_HEIGHT,
} from './mobile/use-mobile-sheet-snap';
import { HistoryTab } from './parts/history-tab';
import { PlanVerdict } from './parts/plan-verdict';
import { ItinerariesTab } from './parts/itineraries-tab';
import { PlacesTab } from './parts/places-tab';
import { useItineraries } from '@/hooks/use-itineraries';
import { usePlaces } from '@/hooks/use-places';
import type { Itinerary } from '@/api/types';
import { decimalRu } from '@/utils/plural';
import { newProgressId } from '@/api/progress';
import { currentRunSession, startRunSession } from '@/utils/run-session';
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
import type { SheetHandleProps, SheetSnap } from './parts/sheet-snap';
import { forward_geocode } from '@/utils/nominatim';

const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

/** Time-budget presets; 0 («без ограничения») sends no budget and builds the full route. */
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

/** Transport the tourist has. */
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

/** Panel views — the content a tourist browses. */
export type PanelView = 'plan' | 'history' | 'itineraries' | 'places';

/** The strip's tabs in the tourist's working order: plan, ready-made, history. */
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
  {
    value: 'places',
    label: t('tabs.places'),
    short: t('tabs.placesShort'),
    icon: MapPin,
  },
  { value: 'history', label: t('tabs.history'), icon: History },
];

/** One line under the panel title, per view. */
const buildViewSubtitles = (t: TFunction): Record<PanelView, string> => ({
  plan: t('tabSubtitles.plan'),
  itineraries: t('tabSubtitles.itineraries'),
  places: t('tabSubtitles.places'),
  history: t('tabSubtitles.history'),
});

/** One-tap starters. */
interface HintChip {
  /** Stable id: the chip's text is a whole sentence, and it may be reworded. */
  id: string;
  text: string;
}

/** The chips offered before anything is planned — how a tourist would start. */
const buildHintChips = (t: TFunction): HintChip[] => [
  { id: 'old-town', text: t('ask.chips.oldTown') },
  { id: 'castles-churches', text: t('ask.chips.castlesChurches') },
  { id: 'food', text: t('ask.chips.food') },
  { id: 'evening', text: t('ask.chips.evening') },
  { id: 'with-children', text: t('ask.chips.withChildren') },
];

/** The chips offered once a route exists. */
const buildRefineHintChips = (t: TFunction): HintChip[] => [
  { id: 'refine-add-cafe', text: t('ask.chipsRefine.addCafe') },
  { id: 'refine-remove-museum', text: t('ask.chipsRefine.removeMuseum') },
  { id: 'refine-shorter', text: t('ask.chipsRefine.shorter') },
  { id: 'refine-only-churches', text: t('ask.chipsRefine.onlyChurches') },
  { id: 'refine-with-children', text: t('ask.chipsRefine.withChildren') },
];

/** Options of the advanced filters. */
interface FilterOption {
  /** What the chip is called and remembered by. */
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
  {
    code: 'религиозное',
    codes: ['костёл', 'церковь', 'храм', 'монастырь'],
    label: t('sidebar.interests.religious'),
  },
];

/** Amenity options with strength: «обязательно» hard, «желательно» soft. */
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

/** Ages the tourist actually typed ("4, 7" → [4, 7]). */
const parseChildAges = (raw: string): number[] =>
  raw
    .split(/[^0-9]+/)
    .map((part) => Number.parseInt(part, 10))
    .filter((age) => Number.isInteger(age) && age >= 0 && age <= 18);

/** Map a /routes/generate failure to a short Russian line the tourist can act on. */
const routeSubmitErrorText = (err: unknown, t: TFunction): string => {
  if (!(err instanceof Error)) return String(err);
  const msg = err.message;
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

/** The planning panel: ask, waypoints, manual add and history tabs. */
/** Fingerprint of the route as the guide will walk it. */
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

/** Why the panel could not locate you — a code, translated to the interface language. */
type GeoReason = 'unsupported' | 'failedShort' | 'denied';

export interface SidebarProps {
  /** Render only the panel's contents: no `Sheet`/`SheetContent`, no desktop resize handle. */
  bare?: boolean;
  /** Position and drag handlers, when the caller owns them (mobile shell). */
  snap?: SheetSnap;
  handleProps?: SheetHandleProps;
  /** Off when the caller publishes `--sheet-h` itself (mobile shell). */
  publishSheetHeight?: boolean;
}

export const Sidebar = ({
  bare = false,
  snap: snapProp,
  handleProps: handlePropsProp,
  publishSheetHeight = true,
}: SidebarProps = {}) => {
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
  const [mode, setMode] = useState<PanelView>('plan');
  const [guiding, setGuiding] = useState(false);
  const setGuidingStore = useCommonStore((s) => s.setGuiding);
  const setPlacesVisible = useCommonStore((s) => s.setPlacesVisible);
  const placeDetails = useDirectionsStore((s) => s.placeDetails);
  const [timeBudget, setTimeBudget] = useState(0);
  const [transport, setTransport] = useState<'' | Profile>('');
  const [mirroredCosting, setMirroredCosting] = useState<Profile | null>(null);

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [partyAdults, setPartyAdults] = useState<number | null>(null);
  const [partyChildren, setPartyChildren] = useState<number | null>(null);
  const [childrenAgesText, setChildrenAgesText] = useState('');
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
  const [stage, setStage] = useState<RouteStage>('requesting');
  const [elapsed, setElapsed] = useState(0);
  const [progressId, setProgressId] = useState<string | null>(null);
  const serverStage = useRouteProgress(progressId, { enabled: busy });
  const abortRef = useRef<AbortController | null>(null);

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
  const [verdict, setVerdict] = useState<AgentRouteResponse | null>(null);

  const lastQueryRef = useRef<string | null>(null);
  const replanRef = useRef<() => void>(() => {});
  const transportRef = useRef<'' | Profile>('');

  const [manualQuery, setManualQuery] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualErr, setManualErr] = useState<string | null>(null);

  const { t } = useTranslation();
  const panel = usePanelWidth();
  const tabs = useMemo(() => buildViews(t), [t]);
  const subtitles = useMemo(() => buildViewSubtitles(t), [t]);
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

  const {
    itineraries,
    missing: itinerariesMissing,
    isLoading: itinerariesLoading,
    error: itinerariesError,
    reload: reloadItineraries,
  } = useItineraries({ enabled: mode === 'itineraries' });

  const {
    places,
    total: placesTotal,
    isLoading: placesLoading,
    error: placesError,
    reload: reloadPlaces,
  } = usePlaces({ enabled: mode === 'places' });

  useEffect(() => {
    setPlacesVisible(mode === 'places');
  }, [mode, setPlacesVisible]);

  const { snap: ownSnap, handleProps: ownHandleProps } = useSheetSnap();
  const snap = snapProp ?? ownSnap;
  const handleProps = handlePropsProp ?? ownHandleProps;
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [query]);

  /** Pick a transport. */
  const setTransportEverywhere = useCallback(
    (value: '' | Profile, replan = true) => {
      const changed = transportRef.current !== value;
      transportRef.current = value;
      setTransport(value);
      if (value === '') return;
      setMirroredCosting(value);
      resetSettings(value);
      if (replan && changed && lastQueryRef.current) replanRef.current();
    },
    [resetSettings]
  );

  useEffect(() => {
    if (!mirroredCosting) return;
    navigate({
      search: (prev) => ({ ...prev, profile: mirroredCosting }),
      replace: true,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mirroredCosting]);

  /** Locate the browser and pin it as the route start (marker, first waypoint, `origin`). */
  const locateMe = useCallback(
    async (silent = false): Promise<{ lat: number; lon: number } | null> => {
      if (!navigator.geolocation) {
        setGeoState('denied');
        setGeoReason('unsupported');
        return null;
      }
      setGeoState('locating');
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
        const current = useDirectionsStore.getState().waypoints;
        setWaypoint([
          meWaypoint(coords.lat, coords.lon, t('sidebar.ui.myLocation')),
          ...current.filter((w) => w.id !== ME_WAYPOINT_ID),
        ]);
        if (!silent) refetchDirections();
        return coords;
      } catch (err) {
        const code = (err as GeolocationPositionError | undefined)?.code;
        setGeoState('denied');
        setGeoReason(code === 1 ? 'denied' : 'failedShort');
        return null;
      }
    },
    [refetchDirections, setWaypoint, t]
  );

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
  const interestCodes = useMemo(
    () => [...interests, ...softAmenities],
    [interests, softAmenities]
  );

  /** One line per filter the tourist set, so no condition is applied invisibly. */
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

  /** Reopen a past route: the same stops on the map with their facts. */
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

  /** Show a ready-made route on the map. */
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
    setTransportEverywhere(itinerary.transport, false);
    const next = me
      ? [meWaypoint(me.lat, me.lon, t('sidebar.ui.myLocation')), ...restored]
      : restored;
    setWaypoint(next);
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
    const requestProgressId = newProgressId();
    setProgressId(requestProgressId);
    setStatus(null);
    setSummary(null);
    setVerdict(null);

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
        session_id?: string;
      } = { query: q, progress_id: requestProgressId };
      if (timeBudget >= 15) body.time_budget_minutes = timeBudget;
      const chosen = buildTransportOptions(t).find(
        (o) => o.value === transportRef.current
      );
      if (chosen?.costing) body.profile = chosen.costing;
      if (origin) body.origin = origin;

      if (partyAdults != null) body.party_adults = partyAdults;
      if (partyChildren != null) body.party_children = partyChildren;
      if (childrenAges.length > 0) body.party_children_ages = childrenAges;
      if (hardServices.length > 0) body.hard_services = hardServices;
      if (interestCodes.length > 0) body.interests = interestCodes;
      if (avoid.length > 0) body.avoid = avoid;
      if (resultMode === 'catalogue') body.result_mode = resultMode;
      if (roundTrip) body.round_trip = true;

      const prior = useDirectionsStore.getState();
      const basePoints = prior.waypoints
        .map((wp) => {
          const geo =
            wp.geocodeResults.find((g) => g.selected) ?? wp.geocodeResults[0];
          if (!geo) return null;
          const [lon, lat] = geo.sourcelnglat ?? geo.displaylnglat ?? [];
          if (lat === undefined || lon === undefined) return null;
          const isMe = wp.id === ME_WAYPOINT_ID;
          return {
            id: wp.placeId ?? null,
            name: wp.userInput || geo.title || t('sidebar.waypoints.unnamed'),
            lat,
            lon,
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
      body.session_id = isRefinement ? currentRunSession() : startRunSession();

      const r = await fetch(`${AGENT_URL}/routes/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!r.ok) {
        throw new Error(agentErrorMessage(r.status));
      }
      const data = (await r.json()) as AgentRouteResponse;
      if (data.status === 'infeasible') {
        setVerdict(data);
        return;
      }
      if (data.status === 'degraded') setVerdict(data);
      const pts = data.points ?? [];
      if (pts.length < 2) {
        throw new Error(t('sidebar.status.fewPlaces'));
      }

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

      if (
        (queryAsksForToilet(q) || hardServices.includes('туалет')) &&
        !pts.some((p) => p.category === 'туалет')
      ) {
        setStatus({ kind: 'warn', text: toiletMissingWarning(t) });
      }

      setQuery('');
      await drawing;
    } catch (e) {
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
            name:
              w.userInput ||
              geo?.title ||
              t('sidebar.waypoints.unnamedWithId', { id: w.id }),
            lat,
            lon,
            placeId: w.placeId,
            category: details?.category ?? null,
            visitMinutes: details?.visitMinutes ?? null,
          };
        })
        .filter((stop): stop is GuideStop => stop !== null),
    [waypoints, placeDetails, t]
  );

  const canGuide = guideStops.length >= 2;

  /** Keep the route's history entry in step with the walk. */
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

  useEffect(() => {
    setGuidingStore(guiding);
  }, [guiding, setGuidingStore]);

  useEffect(() => {
    const root = document.documentElement;
    const publish = () => {
      const desktop = window.innerWidth > MOBILE_MAX_WIDTH;
      const width = panelOpen && desktop && !guiding ? panel.width : 0;
      root.style.setProperty('--panel-width', `${width}px`);
    };
    publish();
    window.addEventListener('resize', publish);
    return () => {
      window.removeEventListener('resize', publish);
      root.style.setProperty('--panel-width', '0px');
    };
  }, [panelOpen, panel.width, guiding]);

  useEffect(() => {
    if (!publishSheetHeight) return;
    const root = document.documentElement;
    const publish = () => {
      const mobile = window.innerWidth < 768;
      const height = !mobile
        ? '0px'
        : guiding && snap === 'peek'
          ? MOBILE_GUIDE_HEIGHT
          : snap === 'full'
            ? MOBILE_SHEET_HEIGHT.full
            : MOBILE_SHEET_HEIGHT.peek;
      root.style.setProperty('--sheet-h', height);
    };
    publish();
    window.addEventListener('resize', publish);
    return () => {
      window.removeEventListener('resize', publish);
      root.style.setProperty('--sheet-h', '0px');
    };
  }, [guiding, snap, publishSheetHeight]);

  const content = (
    <div className="flex h-full min-h-0 flex-col">
      {!guiding && mode === 'plan' && (
        <section className="order-2 shrink-0 border-b border-border px-4 pb-3 pt-3 max-md:px-3 max-md:pb-1.5 max-md:pt-1.5">
          <div className="rounded-2xl border border-border bg-card px-3 py-2.5 shadow-card transition-colors focus-within:border-ring max-md:py-1.5">
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
                  hasRoute ? t('ask.placeholderRefine') : t('ask.placeholder')
                }
                aria-label={t('ask.label')}
                className="min-h-0 flex-1 resize-none border-0 bg-transparent p-0 text-body leading-6 shadow-none focus-visible:ring-0 max-md:min-h-10"
                rows={1}
                disabled={busy}
              />
            </div>
          </div>
          <div
            role="group"
            aria-label={t('sidebar.plan.hintsAria')}
            className="mt-2 flex gap-1.5 max-md:flex-nowrap max-md:overflow-x-auto max-md:pb-0.5 md:flex-wrap"
          >
            {hints.map((hint) => (
              <Chip
                key={hint.id}
                className="truncate max-w-[12rem]"
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

      <header
        className={cn(
          'order-1 shrink-0 border-b border-border px-4 pb-2.5 max-md:px-3 max-md:pb-1.5',
          guiding && snap === 'peek' && 'border-b-transparent'
        )}
      >
        <SheetDragHandle snap={snap} handleProps={handleProps} />
        {guiding ? (
          <SheetTitle className="sr-only">{t('sidebar.ui.guide')}</SheetTitle>
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
              <LanguageSwitcher className="shrink-0" />
            </div>
            <div className="mt-2.5 flex min-w-0 items-center gap-2 max-md:mt-1">
              <RouteIcon
                className="h-4 w-4 shrink-0 text-primary max-md:hidden"
                aria-hidden="true"
              />
              <div className="min-w-0">
                <SheetTitle className="truncate text-body max-md:sr-only">
                  {t('app.title')}
                </SheetTitle>
                <SheetDescription className="sr-only">
                  {t('app.description')}
                </SheetDescription>
                <p className="truncate text-meta text-muted-foreground max-md:hidden">
                  {subtitles[mode]}
                </p>
              </div>
            </div>
          </>
        )}
      </header>

      <div className="slim-scroll order-3 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-6 pt-3 max-md:px-3 max-md:pb-4">
        {guiding ? (
          <GuidePanel
            key={guideRouteKey(guideStops)}
            stops={guideStops}
            startInMoving
            overviewOpen={snap === 'full'}
            transport={transport}
            onWalked={handleWalked}
            onExit={() => setGuiding(false)}
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
        ) : mode === 'places' ? (
          <PlacesTab
            places={places}
            total={placesTotal}
            isLoading={placesLoading}
            error={placesError}
            onReload={reloadPlaces}
          />
        ) : (
          <>
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
                  <MobileSection
                    id="party"
                    title={t('sidebar.plan.partyLabel')}
                    defaultOpen={true}
                  >
                    <div className="flex flex-col gap-1.5">
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
                  </MobileSection>

                  <MobileSection
                    id="amenities"
                    title={t('sidebar.plan.amenities')}
                    summary={Object.keys(amenities).length || undefined}
                  >
                    <div className="flex flex-col gap-1.5">
                      {buildAmenityOptions(t).map((option) => (
                        <div
                          key={option.code}
                          className="flex items-center gap-2"
                        >
                          <span className="text-label">{option.label}</span>
                          <div className="ml-auto flex gap-1">
                            <Chip
                              selected={amenities[option.code] === 'hard'}
                              onClick={() => toggleAmenity(option.code, 'hard')}
                              data-testid={`amenity-${option.code}-hard`}
                              className="px-2 text-meta"
                            >
                              {t('sidebar.plan.hard')}
                            </Chip>
                            <Chip
                              selected={amenities[option.code] === 'soft'}
                              onClick={() => toggleAmenity(option.code, 'soft')}
                              data-testid={`amenity-${option.code}-soft`}
                              className="px-2 text-meta"
                            >
                              {t('sidebar.plan.soft')}
                            </Chip>
                          </div>
                        </div>
                      ))}
                    </div>
                  </MobileSection>

                  <MobileSection
                    id="interests"
                    title={t('sidebar.plan.interests')}
                    summary={interests.length || undefined}
                  >
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
                  </MobileSection>

                  <MobileSection
                    id="avoid"
                    title={t('sidebar.plan.avoid')}
                    summary={avoid.length || undefined}
                  >
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
                  </MobileSection>

                  <MobileSection
                    id="result-type"
                    title={t('sidebar.plan.resultType')}
                    defaultOpen={true}
                    summary={
                      resultMode === 'catalogue' || roundTrip
                        ? [
                            resultMode === 'catalogue' ? 'каталог' : null,
                            roundTrip ? '↩' : null,
                          ]
                            .filter(Boolean)
                            .join(' ')
                        : undefined
                    }
                  >
                    <div className="flex flex-col gap-1.5">
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
                  </MobileSection>
                </div>
              )}
            </section>

            <section className="flex flex-col gap-2.5">
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
                    <StatTile
                      value={summary.stops}
                      count={summary.stops}
                      unit="points"
                    />
                    <StatTile
                      value={summary.km != null ? fmtKm(summary.km, t) : '—'}
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
                                .join(' · ') || t('sidebar.status.noChanges')
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
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('sidebar.plan.newRoute')}
                    </Button>
                  </div>
                </div>
              )}
            </section>

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
                  placeholder={t('sidebar.plan.queryPlaceholder')}
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
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />{' '}
                {t('sidebar.ui.emptyPoint')}
              </button>
            </section>
          </>
        )}
      </div>

      {!guiding && mode === 'plan' && (
        <footer className="order-4 shrink-0 border-t border-border bg-background px-4 py-3 max-md:px-3 max-md:pt-1.5 max-md:pb-[calc(env(safe-area-inset-bottom)+0.375rem)]">
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
              canGuide
                ? 'bg-secondary text-secondary-foreground'
                : 'bg-primary text-primary-foreground'
            )}
          >
            {busy && (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            {busy ? t('sidebar.ui.plan') : t('sidebar.ui.build')}
          </Button>
        </footer>
      )}
    </div>
  );

  if (bare) return content;

  return (
    <Sheet open={panelOpen} modal={false}>
      <SheetContent
        side="left"
        className={cn(
          PANEL_SHEET_CLASS,
          guiding ? GUIDE_SHEET_CLASS : SHEET_SNAP_CLASS[snap]
        )}
        style={{ '--panel-width': `${panel.width}px` } as CSSProperties}
      >
        {content}
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

interface StepperProps {
  label: string;
  testId: string;
  /** null = the tourist has not named a number yet. */
  value: number | null;
  min: number;
  max: number;
  onChange: (next: number | null) => void;
}

/** A small −/+ stepper for a party count. */
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
      <span className="min-w-0 flex-1 truncate text-label">{label}</span>
      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          aria-label={t('sidebar.plan.decrement', { label })}
          data-testid={`${testId}-dec`}
          disabled={value == null}
          onClick={() =>
            onChange(value == null || value <= min ? null : value - 1)
          }
          className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 max-md:h-11 max-md:w-11 pointer-coarse:h-11 pointer-coarse:w-11"
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
          className="flex h-9 w-9 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:h-11 max-md:w-11 pointer-coarse:h-11 pointer-coarse:w-11"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
};
