import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Car,
  ChevronDown,
  ChevronUp,
  Clock,
  Bike,
  Footprints,
  History,
  Loader2,
  LocateFixed,
  MapPin,
  Plus,
  RotateCcw,
  Route as RouteIcon,
  Send,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Sheet,
  SheetContent,
  SheetHeader,
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
import { WaypointList } from './waypoint-list';
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

const TIME_BUDGET_OPTIONS = [
  // 0 = the user set no limit: nothing is sent, the agent builds the full route.
  { value: 0, label: 'без ограничения' },
  { value: 60, label: '1 ч' },
  { value: 90, label: '1.5 ч' },
  { value: 120, label: '2 ч' },
  { value: 180, label: '3 ч' },
  { value: 240, label: 'полдня' },
  { value: 360, label: '6 ч' },
  { value: 480, label: 'весь день' },
];

/**
 * Transport the tourist has. Empty value = "как удобно": nothing is sent and the
 * agent picks the costing that fits the query (a walk inside a town, a drive
 * across the область). Picking one sends it — transport is a real constraint.
 */
const TRANSPORT_OPTIONS: Array<{
  value: '' | Profile;
  label: string;
  icon: typeof Footprints;
  /** costing name the agent understands */
  costing?: string;
}> = [
  { value: '', label: 'как удобно', icon: Sparkles },
  {
    value: 'pedestrian',
    label: 'пешком',
    icon: Footprints,
    costing: 'pedestrian',
  },
  { value: 'bicycle', label: 'велосипед', icon: Bike, costing: 'bicycle' },
  { value: 'car', label: 'машина', icon: Car, costing: 'auto' },
];

const SUGGESTIONS = [
  'интересные музеи и галереи',
  'прогулка по замкам',
  'костёлы и храмы',
  'неман и набережная',
  'дворцы и усадьбы',
  'история Гродно',
];

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
 * Left sidebar that replaces the upstream RoutePlanner:
 *   1. prompt — textarea + time budget + transport; on submit hits
 *      /routes/generate and pushes the agent's ordered points into the
 *      directions store (with my own position as the start when known).
 *   2. waypoints — WaypointList (drag/drop, up/down, delete).
 *   3. manual add — small Nominatim lookup → append to the list.
 *   4. history — previous routes, restored with their descriptions.
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
  const { refetch: refetchDirections } = useDirectionsQuery();

  const resetSettings = useCommonStore((s) => s.resetSettings);
  const navigate = useNavigate({ from: '/$activeTab' });

  const [query, setQuery] = useState('');
  const [timeBudget, setTimeBudget] = useState(0); // 0 = без ограничения
  // '' = «как удобно»: no transport constraint, the agent picks the costing
  // (walking inside a town, driving across a region). Only an explicit pick —
  // or the costing the agent answers with — is a real constraint.
  const [transport, setTransport] = useState<'' | Profile>('');
  const [mirroredCosting, setMirroredCosting] = useState<Profile | null>(null);
  const [me, setMe] = useState<{ lat: number; lon: number } | null>(null);
  const [geoState, setGeoState] = useState<
    'idle' | 'locating' | 'ok' | 'denied'
  >('idle');
  const [geoReason, setGeoReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{
    kind: 'ok' | 'err';
    text: string;
  } | null>(null);
  const [summary, setSummary] = useState<{
    stops: number;
    km: number | null;
    walkMinutes: number;
    visitMinutes: number;
    budgetMinutes: number | null;
    fits: boolean;
  } | null>(null);

  // The query of the last successful build: switching transport re-plans it, so
  // the stops, the drawn line and the "в пути" time all follow the new costing.
  const lastQueryRef = useRef<string | null>(null);
  const replanRef = useRef<() => void>(() => {});

  const [manualQuery, setManualQuery] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualErr, setManualErr] = useState<string | null>(null);

  const routeHistory = useDirectionsStore((s) => s.routeHistory);
  const addToHistory = useDirectionsStore((s) => s.addToHistory);
  const removeFromHistory = useDirectionsStore((s) => s.removeFromHistory);
  const clearHistory = useDirectionsStore((s) => s.clearHistory);

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
    (value: '' | Profile) => {
      setTransport(value);
      if (value === '') return;
      setMirroredCosting(value);
      // the app's own routing (isochrones, directions) needs the costing too
      resetSettings(value);
      // A transport is a real constraint: re-plan with it instead of leaving a
      // walking route (and its travel time) on screen for a drive.
      if (lastQueryRef.current) replanRef.current();
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
      try {
        const pos = await new Promise<GeolocationPosition>(
          (resolve, reject) => {
            navigator.geolocation.getCurrentPosition(resolve, reject, {
              enableHighAccuracy: true,
              timeout: 8000,
              maximumAge: 60_000,
            });
          }
        );
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
      } = { query: q };
      if (timeBudget >= 15) body.time_budget_minutes = timeBudget;
      const chosen = TRANSPORT_OPTIONS.find((o) => o.value === transport);
      if (chosen?.costing) body.profile = chosen.costing;
      if (origin) body.origin = origin;

      const r = await fetch(`${AGENT_URL}/routes/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const errBody = await r.text().catch(() => '');
        throw new Error(`агент ${r.status}: ${errBody.slice(0, 160)}`);
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
      if (!transport && data.costing) {
        const match = TRANSPORT_OPTIONS.find((o) => o.costing === data.costing);
        if (match) setTransportEverywhere(match.value);
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
      const walkMins =
        data.budget?.walk_minutes ??
        Math.round((data.summary?.time_seconds ?? 0) / 60);
      lastQueryRef.current = q;
      setSummary({
        stops: pts.length,
        km: data.summary?.length_km ?? null,
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
      setQuery('');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus({ kind: 'err', text: msg });
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

  const stopCount = waypoints.filter(
    (w) => w.id !== ME_WAYPOINT_ID && w.geocodeResults.length > 0
  ).length;
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
        className="flex w-[380px] flex-col gap-3 overflow-y-auto px-3 py-3 sm:max-w-[unset]"
      >
        {/* pr-9 keeps the intro text clear of the absolutely-placed close
            button in the top-right corner. */}
        <SheetHeader className="space-y-0.5 pr-9">
          <SheetTitle className="flex items-center gap-2 text-base">
            <RouteIcon className="h-4 w-4 text-primary" />
            AI-гид по Гродно
          </SheetTitle>
          <p className="text-[11px] text-muted-foreground">
            Опиши, что хочется посмотреть — соберу маршрут по реальным дорогам
          </p>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={toggle}
            className="absolute right-3 top-3"
            aria-label="закрыть"
            title="закрыть панель"
          >
            <ChevronUp className="size-4" />
          </Button>
        </SheetHeader>

        {/* === Prompt === */}
        <section className="rounded-xl border border-border/60 bg-card p-3 shadow-sm">
          <div className="flex items-end gap-2">
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
              placeholder="прогулка по замкам Гродно"
              className="min-h-9 flex-1 resize-none border-0 bg-transparent px-0 text-sm shadow-none focus-visible:ring-0"
              rows={1}
              disabled={busy}
            />
            <Button
              type="button"
              onClick={() => submitPrompt()}
              disabled={busy || !query.trim()}
              size="icon"
              className="h-9 w-9 shrink-0"
              aria-label="построить маршрут"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>

          {/* Constraints: time + transport. Both are the user's call — nothing
              is invented for them. */}
          <div className="mt-2.5 flex flex-col gap-2 text-xs">
            <label className="flex items-center justify-between gap-2 text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" /> есть время
              </span>
              <select
                value={timeBudget}
                onChange={(e) => setTimeBudget(Number(e.target.value))}
                disabled={busy}
                className="h-8 rounded-md border border-border/60 bg-background px-1.5 text-xs font-medium text-foreground"
              >
                {TIME_BUDGET_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-1 text-muted-foreground">
                <Car className="h-3 w-3" /> на чём
              </span>
              <div className="flex gap-1">
                {TRANSPORT_OPTIONS.map((o) => {
                  const Icon = o.icon;
                  const active = transport === o.value;
                  return (
                    <button
                      key={o.value || 'any'}
                      type="button"
                      onClick={() => setTransportEverywhere(o.value)}
                      disabled={busy}
                      aria-pressed={active}
                      title={o.label}
                      data-testid={`transport-${o.value || 'any'}`}
                      className={[
                        'flex flex-1 items-center justify-center gap-1 rounded-md border px-1.5 py-1.5 text-[11px] transition-colors disabled:opacity-60',
                        active
                          ? 'border-primary/50 bg-primary/10 font-medium text-primary'
                          : 'border-border/60 bg-background text-muted-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-foreground',
                      ].join(' ')}
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{o.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void locateMe()}
            disabled={geoState === 'locating'}
            className="mt-2 flex w-full items-center gap-1.5 rounded-md border border-border/40 bg-background/60 px-2 py-1.5 text-left text-[11px] transition-colors hover:border-primary/40 hover:bg-primary/5 disabled:opacity-60"
            title="переопределить, откуда начинается маршрут"
          >
            <LocateFixed
              className={`h-3.5 w-3.5 shrink-0 ${
                geoState === 'ok' ? 'text-emerald-600' : 'text-muted-foreground'
              }`}
            />
            <span className={geoBadge.tone}>{geoBadge.text}</span>
            <span className="ml-auto text-muted-foreground">
              {geoState === 'locating' ? '…' : 'обновить'}
            </span>
          </button>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => submitPrompt(s)}
                disabled={busy}
                className="rounded-full border border-border/60 bg-background px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-foreground disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>
        </section>

        {/* === Waypoints === */}
        <section className="rounded-xl border border-border/60 bg-card p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <MapPin className="h-3.5 w-3.5" />
              Маршрут — {stopCount}{' '}
              {stopCount === 1 ? 'точка' : stopCount < 5 ? 'точки' : 'точек'}
            </span>
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-1 text-xs normal-case text-muted-foreground hover:text-foreground"
              title="очистить маршрут"
            >
              <RotateCcw className="h-3 w-3" />
              сброс
            </button>
          </div>

          {summary && (
            <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-primary/5 px-2.5 py-2 text-[11px]">
              {summary.km != null && (
                <span className="flex items-center gap-1 font-medium text-foreground">
                  <RouteIcon className="h-3 w-3 text-primary" />
                  {fmtKm(summary.km)}
                </span>
              )}
              <span className="flex items-center gap-1 text-muted-foreground">
                <Clock className="h-3 w-3" />в пути ~
                {fmtMin(summary.walkMinutes)}
              </span>
              <span className="text-muted-foreground">
                осмотр ~{fmtMin(summary.visitMinutes)}
              </span>
              {!summary.fits && (
                <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-700">
                  не влезло в лимит
                </span>
              )}
              {summary.budgetMinutes ? (
                <span className="text-muted-foreground">
                  лимит {fmtMin(summary.budgetMinutes)}
                </span>
              ) : (
                <span className="text-muted-foreground">без лимита</span>
              )}
            </div>
          )}

          <WaypointList onChanged={() => setStatus(null)} />
          {status && (
            <div
              className={[
                'mt-2 rounded-md px-2 py-1 text-xs',
                status.kind === 'ok'
                  ? 'bg-primary/10 text-primary'
                  : 'bg-destructive/10 text-destructive',
              ].join(' ')}
            >
              {status.text}
            </div>
          )}
        </section>

        {/* === Manual add === */}
        <section className="rounded-xl border border-dashed border-border/60 bg-card/50 p-3">
          <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
            Добавить точку
          </div>
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
              className="h-9 flex-1 text-sm"
              disabled={manualBusy}
            />
            <Button
              type="button"
              onClick={manualAdd}
              disabled={manualBusy || !manualQuery.trim()}
              size="icon"
              className="h-9 w-9"
              aria-label="добавить точку"
            >
              {manualBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
            </Button>
          </div>
          {manualErr && (
            <div className="mt-1.5 text-xs text-destructive">{manualErr}</div>
          )}
          <button
            type="button"
            onClick={addEmptyWaypointToEnd}
            className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronDown className="h-3 w-3" /> пустая точка (выбрать кликом по
            карте)
          </button>
        </section>

        {/* === Route History === */}
        <section className="rounded-xl border border-border/60 bg-card p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
              <History className="h-3.5 w-3.5" />
              История
              {routeHistory.length > 0 && (
                <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px]">
                  {routeHistory.length}
                </span>
              )}
            </div>
            {routeHistory.length > 0 && (
              <button
                type="button"
                onClick={clearHistory}
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-destructive"
                title="очистить историю"
              >
                <Trash2 className="h-3 w-3" />
                очистить
              </button>
            )}
          </div>

          {routeHistory.length === 0 ? (
            <p className="text-xs text-muted-foreground/60">
              Построенные маршруты появятся здесь
            </p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {routeHistory.map((entry) => (
                <HistoryItem
                  key={entry.id}
                  entry={entry}
                  onLoad={() => {
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
                      me ? [meWaypoint(me.lat, me.lon), ...restored] : restored
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
      </SheetContent>
    </Sheet>
  );
};

// History item component
interface HistoryItemProps {
  entry: RouteHistoryEntry;
  onLoad: () => void;
  onRemove: () => void;
}

const HistoryItem = ({ entry, onLoad, onRemove }: HistoryItemProps) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="group rounded-lg border border-border/40 bg-background/50 p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onLoad}
          className="flex-1 truncate text-left font-medium hover:text-primary"
          title={entry.query}
        >
          {entry.query.length > 35
            ? entry.query.slice(0, 35) + '…'
            : entry.query}
        </button>
        <div className="flex shrink-0 items-center gap-1">
          <span className="flex items-center gap-0.5 text-muted-foreground">
            <Clock className="h-3 w-3" />
            {entry.timeBudget <= 0 ? 'без лимита' : fmtMin(entry.timeBudget)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            title="удалить"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
      >
        <MapPin className="h-3 w-3" />
        {entry.places.length} мест
        <ChevronDown
          className={`h-3 w-3 transition-transform ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      {expanded && (
        <div className="mt-1.5 flex flex-col gap-0.5 pl-4">
          {entry.places.map((p, i) => (
            <div
              key={p.id}
              className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
            >
              <span className="h-4 w-4 rounded-full bg-primary/10 text-center text-[9px] font-medium leading-4 text-primary">
                {i + 1}
              </span>
              <span className="truncate">{p.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
