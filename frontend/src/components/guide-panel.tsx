import {
  ExternalLink,
  Footprints,
  MapPin,
  Navigation,
  RotateCcw,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

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

interface GuidePanelProps {
  stops: GuideStop[];
}

const STORAGE_KEY = 'grodno-guide-progress';
/** You are "at" a stop when you are this close to it. */
const ARRIVAL_RADIUS_M = 40;

const fmtMin = (min: number) =>
  min >= 60 ? `${Math.floor(min / 60)} ч ${min % 60} мин` : `${min} мин`;

const fmtDist = (m: number) =>
  m >= 1000 ? `${(m / 1000).toFixed(1)} км` : `${Math.round(m)} м`;

const metresBetween = (
  a: { lat: number; lon: number },
  b: { lat: number; lon: number }
) => {
  const dLat = (a.lat - b.lat) * 111_320;
  const dLon = (a.lon - b.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
};

/**
 * Fingerprint of the route the progress belongs to. The parent uses it as the
 * React key: a rebuilt route remounts the guide, which is what starts a fresh
 * walk (see sidebar.tsx).
 */
export const guideRouteKey = (stops: GuideStop[]) =>
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

/**
 * Mode 2 — the guide: walk the route stop by stop.
 *
 * Geolocation follows the tourist and marks a stop once they are within 40 m of
 * it, progress survives a reload as long as the route is unchanged, and every
 * stop can be opened in a maps app. Without geolocation it degrades to tapping
 * the stops by hand.
 */
export const GuidePanel = ({ stops }: GuidePanelProps) => {
  const key = useMemo(() => guideRouteKey(stops), [stops]);
  const [progress, setProgress] = useState<StoredProgress>(() =>
    loadProgress(key)
  );
  const [position, setPosition] = useState<{ lat: number; lon: number } | null>(
    null
  );
  const [geoState, setGeoState] = useState<'idle' | 'ok' | 'denied'>(() =>
    typeof navigator === 'undefined' || !('geolocation' in navigator)
      ? 'denied'
      : 'idle'
  );
  const wakeLockRef = useRef<{ release: () => Promise<void> } | null>(null);

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
  }, [key]);

  const nextStop = useMemo(
    () => stops.find((s) => !progress.visited.includes(s.id)) ?? null,
    [stops, progress.visited]
  );

  // The geolocation callback needs the current stops without re-subscribing to
  // the watcher on every render (refs are written in effects, never while
  // rendering).
  const stopsRef = useRef(stops);
  useEffect(() => {
    stopsRef.current = stops;
  }, [stops]);

  const stopCount = stops.length;
  useEffect(() => {
    if (stopCount === 0) return;
    const geo = navigator.geolocation;
    if (!geo?.watchPosition) return;
    const watch = geo.watchPosition(
      (pos) => {
        const here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        setGeoState('ok');
        setPosition(here);
        // Standing at the next stop means that stop is done (tap undoes it).
        setProgress((prev) => {
          const upcoming = stopsRef.current.find(
            (stop) => !prev.visited.includes(stop.id)
          );
          if (!upcoming || metresBetween(here, upcoming) > ARRIVAL_RADIUS_M) {
            return prev;
          }
          const next = {
            ...prev,
            route: key,
            visited: [...prev.visited, upcoming.id],
          };
          saveProgress(next);
          return next;
        });
      },
      () => setGeoState('denied'),
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 15_000 }
    );
    return () => geo.clearWatch?.(watch);
  }, [stopCount, toggle]);

  // Keep the screen awake while walking; browsers may refuse — that is fine.
  useEffect(() => {
    const nav = navigator as Navigator & {
      wakeLock?: {
        request: (type: 'screen') => Promise<{ release: () => Promise<void> }>;
      };
    };
    let cancelled = false;
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
    return () => {
      cancelled = true;
      void wakeLockRef.current?.release().catch(() => undefined);
      wakeLockRef.current = null;
    };
  }, []);

  const done = progress.visited.length;
  const metresLeft =
    nextStop && position ? metresBetween(position, nextStop) : null;
  const minutesLeft = stops
    .filter((s) => !progress.visited.includes(s.id))
    .reduce((sum, s) => sum + (s.visitMinutes ?? 0), 0);

  if (stops.length === 0) {
    return (
      <section
        data-testid="guide-panel"
        className="rounded-xl border border-border/60 bg-card p-3 text-[12px] text-muted-foreground shadow-sm"
      >
        Сначала соберите маршрут в режиме планирования — проводник ведёт по уже
        построенному маршруту.
      </section>
    );
  }

  return (
    <section
      data-testid="guide-panel"
      className="space-y-2 rounded-xl border border-border/60 bg-card p-3 shadow-sm"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[12px] font-medium">
          <Footprints className="h-3.5 w-3.5 text-primary" />
          проводник
        </div>
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          title="начать маршрут заново"
        >
          <RotateCcw className="h-3 w-3" />
          сбросить прогресс
        </button>
      </div>

      <div className="text-[11px] text-muted-foreground">
        пройдено {done} из {stops.length}
        {minutesLeft > 0 && ` · осталось осмотра ~${fmtMin(minutesLeft)}`}
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${Math.round((done / stops.length) * 100)}%` }}
        />
      </div>

      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Navigation className="h-3 w-3" />
        {geoState === 'denied'
          ? 'геолокация недоступна — отмечайте остановки вручную'
          : geoState === 'idle'
            ? 'определяю, где вы…'
            : metresLeft != null
              ? `до следующей ${fmtDist(metresLeft)}`
              : 'вы на маршруте'}
      </div>

      {nextStop && (
        <div className="rounded-lg bg-primary/5 px-2.5 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            следующая остановка
          </div>
          <div className="text-[13px] font-medium">{nextStop.name}</div>
          <div className="flex items-center justify-between gap-2">
            {nextStop.category && (
              <div className="text-[11px] text-muted-foreground">
                {nextStop.category}
              </div>
            )}
            <a
              href={`https://www.google.com/maps/search/?api=1&query=${nextStop.lat},${nextStop.lon}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              открыть в картах
            </a>
          </div>
        </div>
      )}

      <ol className="flex flex-col gap-0.5">
        {stops.map((stop, i) => {
          const isDone = progress.visited.includes(stop.id);
          const isNext = nextStop?.id === stop.id;
          return (
            <li key={stop.id}>
              <button
                type="button"
                data-testid={`guide-stop-${i + 1}`}
                onClick={() => toggle(stop.id)}
                className={[
                  'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[12px]',
                  isDone
                    ? 'text-muted-foreground line-through'
                    : 'hover:bg-muted',
                  isNext ? 'bg-primary/5' : '',
                ].join(' ')}
              >
                <span
                  className={[
                    'h-4 w-4 shrink-0 rounded-full text-center text-[9px] font-medium leading-4',
                    isDone
                      ? 'bg-muted text-muted-foreground'
                      : 'bg-primary/10 text-primary',
                  ].join(' ')}
                >
                  {i + 1}
                </span>
                <span className="truncate">{stop.name}</span>
                {isNext && metresLeft != null ? (
                  <span className="ml-auto flex shrink-0 items-center gap-0.5 text-[10px] text-primary">
                    <MapPin className="h-3 w-3" />
                    {fmtDist(metresLeft)}
                  </span>
                ) : (
                  stop.visitMinutes != null && (
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                      ~{stop.visitMinutes} мин
                    </span>
                  )
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
};
