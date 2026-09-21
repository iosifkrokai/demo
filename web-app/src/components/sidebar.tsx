import { useEffect, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  Loader2,
  MapPin,
  Plus,
  RotateCcw,
  Send,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useCommonStore } from '@/stores/common-store';
import { useDirectionsStore, type Waypoint } from '@/stores/directions-store';
import { useDirectionsQuery } from '@/hooks/use-directions-queries';
import { WaypointList } from './waypoint-list';
import { forward_geocode } from '@/utils/nominatim';

const AGENT_URL =
  (import.meta.env.VITE_AGENT_URL as string | undefined) ||
  'http://localhost:8080';

interface AgentPoint {
  id: number;
  name: string;
  category?: string | null;
  lat: number;
  lon: number;
}

const SUGGESTIONS = [
  'замки Гродно',
  'костёлы центра',
  'усадьбы Гродненской области',
  'достопримечательности Гродно',
];

const N_POINTS_OPTIONS = [2, 3, 4, 5, 6, 7, 8];

const TIME_BUDGET_OPTIONS = [
  { value: 60, label: '1 ч' },
  { value: 120, label: '2 ч' },
  { value: 180, label: '3 ч' },
  { value: 240, label: 'полдня' },
  { value: 480, label: 'весь день' },
];

const fmtMin = (m: number) =>
  m >= 60 ? `${Math.floor(m / 60)} ч ${m % 60 ? `${m % 60} мин` : ''}`.trim() : `${m} мин`;

/**
 * Left sidebar that replaces the upstream RoutePlanner. Three sections:
 *   1. prompt — textarea + send; on submit hits /routes/generate and pushes
 *      the agent's ordered points straight into the directions store.
 *   2. waypoints — WaypointList (drag/drop, up/down, delete).
 *   3. manual add — small Nominatim lookup → append to the list.
 */
export const Sidebar = () => {
  const panelOpen = useCommonStore((s) => s.directionsPanelOpen);
  const toggle = useCommonStore((s) => s.toggleDirections);
  const setWaypoint = useDirectionsStore((s) => s.setWaypoint);
  const addEmptyWaypointToEnd = useDirectionsStore(
    (s) => s.addEmptyWaypointToEnd
  );
  const waypoints = useDirectionsStore((s) => s.waypoints);
  const { refetch: refetchDirections } = useDirectionsQuery();

  const [query, setQuery] = useState('');
  const [nPoints, setNPoints] = useState(4);
  const [timeBudget, setTimeBudget] = useState<number | ''>('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{
    kind: 'ok' | 'err';
    text: string;
  } | null>(null);

  const [manualQuery, setManualQuery] = useState('');
  const [manualBusy, setManualBusy] = useState(false);
  const [manualErr, setManualErr] = useState<string | null>(null);

  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [query]);

  const submitPrompt = async (text?: string) => {
    const q = (text ?? query).trim();
    if (!q || busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const body: { query: string; n_points: number; time_budget_minutes?: number } = {
        query: q,
        n_points: nPoints,
      };
      if (timeBudget !== '' && timeBudget >= 15) {
        body.time_budget_minutes = timeBudget;
      }
      const r = await fetch(`${AGENT_URL}/routes/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const body = await r.text().catch(() => '');
        throw new Error(`агент ${r.status}: ${body.slice(0, 120)}`);
      }
      const data = (await r.json()) as {
        points?: AgentPoint[];
        budget?: {
          budget_minutes: number;
          total_minutes: number;
          walk_minutes: number;
          visit_minutes: number;
          fits: boolean;
        };
      };
      const pts = data.points ?? [];
      if (pts.length < 2) {
        throw new Error('агент не нашёл достаточно мест — попробуй иначе');
      }
      const budgetNote = data.budget
        ? ` · ${fmtMin(data.budget.total_minutes)} из ${fmtMin(data.budget.budget_minutes)}${
            data.budget.fits ? '' : ' — не влезло'
          }`
        : '';
      const next: Waypoint[] = pts.map((p, i) => ({
        id: i.toString(),
        userInput: p.name,
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
      setWaypoint(next);
      refetchDirections();
      setSuccess(`маршрут из ${pts.length} мест построен${budgetNote}`);
      setQuery('');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus({ kind: 'err', text: msg });
    } finally {
      setBusy(false);
    }
  };

  const setSuccess = (text: string) =>
    setStatus({ kind: 'ok', text: text + ` · ${waypoints.length} точек` });

  const reset = () => {
    setWaypoint([
      {
        id: '0',
        userInput: '',
        geocodeResults: [],
      },
      {
        id: '1',
        userInput: '',
        geocodeResults: [],
      },
    ]);
    setStatus(null);
    refetchDirections();
  };

  const manualAdd = async () => {
    const q = manualQuery.trim();
    if (!q || manualBusy) return;
    setManualBusy(true);
    setManualErr(null);
    try {
      const { data } = await forward_geocode(q);
      if (!data || data.length === 0) {
        setManualErr('ничего не нашлось');
        return;
      }
      const top = data[0];
      const lat = parseFloat(top.lat);
      const lon = parseFloat(top.lon);
      const title = top.display_name.split(',')[0].trim() || q;
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

  return (
    <Sheet open={panelOpen} modal={false}>
      <SheetContent
        side="left"
        className="flex w-[380px] flex-col gap-3 overflow-y-auto px-3 py-3 sm:max-w-[unset]"
      >
        <SheetHeader>
          <SheetTitle>Grodno walking routes</SheetTitle>
          <SheetDescription>
            Опиши маршрут или собери точки вручную — карта обновится сама.
          </SheetDescription>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={toggle}
            className="absolute right-3 top-3"
            aria-label="закрыть"
            title="закрыть"
          >
            <ChevronUp className="size-4" />
          </Button>
        </SheetHeader>

        {/* === Prompt === */}
        <section className="rounded-xl border border-border/60 bg-card p-3">
          <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
            <MapPin className="h-3.5 w-3.5" /> Куда хочешь сходить?
          </div>
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
              aria-label="построить"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1.5 text-muted-foreground">
              точек:
              <select
                value={nPoints}
                onChange={(e) => setNPoints(Number(e.target.value))}
                disabled={busy}
                className="h-7 rounded-md border border-border/60 bg-background px-1.5 text-xs"
              >
                {N_POINTS_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-muted-foreground">
              есть:
              <select
                value={timeBudget}
                onChange={(e) => setTimeBudget(e.target.value === '' ? '' : Number(e.target.value))}
                disabled={busy}
                className="h-7 rounded-md border border-border/60 bg-background px-1.5 text-xs"
              >
                <option value="">не ограничено</option>
                {TIME_BUDGET_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
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
        <section className="rounded-xl border border-border/60 bg-card p-3">
          <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-muted-foreground">
            <span>Маршрут — {waypoints.length} точек</span>
            <button
              type="button"
              onClick={reset}
              className="inline-flex items-center gap-1 text-xs normal-case text-muted-foreground hover:text-foreground"
              title="очистить"
            >
              <RotateCcw className="h-3 w-3" />
              сброс
            </button>
          </div>
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
              aria-label="добавить"
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
            <ChevronDown className="h-3 w-3" /> пустая точка (выбрать кликом по карте)
          </button>
        </section>
      </SheetContent>
    </Sheet>
  );
};
