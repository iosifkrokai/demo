import { Footprints, LocateFixed, Navigation, RotateCcw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { GuideEmpty } from './parts/guide-empty';
import { fmtDist, metresBetween } from './parts/guide-format';
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

interface GuidePanelProps {
  stops: GuideStop[];
}

const STORAGE_KEY = 'grodno-guide-progress';
/** You are "at" a stop when you are this close to it. */
const ARRIVAL_RADIUS_M = 40;

const mapsUrl = (lat: number, lon: number) =>
  `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;

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
  const nextIndex = useMemo(
    () => (nextStop ? stops.findIndex((s) => s.id === nextStop.id) : -1),
    [stops, nextStop]
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
  }, [stopCount, key, toggle]);

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
  const nextDistance =
    nextStop && position ? metresBetween(position, nextStop) : null;
  const minutesLeft = stops
    .filter((s) => !progress.visited.includes(s.id))
    .reduce((sum, s) => sum + (s.visitMinutes ?? 0), 0);

  const geoLine =
    geoState === 'denied'
      ? 'геолокация недоступна — отмечайте остановки вручную'
      : geoState === 'idle'
        ? 'определяю, где вы…'
        : nextDistance != null
          ? `до следующей ${fmtDist(nextDistance)}`
          : 'вы на маршруте';

  if (stops.length === 0) {
    return (
      <section data-testid="guide-panel" className="flex flex-col gap-3">
        <GuideHeader onReset={reset} />
        <GuideEmpty />
      </section>
    );
  }

  return (
    <section data-testid="guide-panel" className="flex flex-col gap-3">
      <GuideHeader onReset={reset} />

      {/* The next stop, or a quiet «all done» card once there is none. */}
      {nextStop ? (
        // key: remounting on a new stop replays the small fade+slide instead of
        // swapping the text in place (DESIGN.md, Motion).
        <GuideNextStop
          key={nextStop.id}
          number={nextIndex + 1}
          name={nextStop.name}
          category={nextStop.category ?? null}
          visitMinutes={nextStop.visitMinutes ?? null}
          distance={nextDistance}
          mapsHref={mapsUrl(nextStop.lat, nextStop.lon)}
        />
      ) : (
        <GuideRouteDone total={stops.length} />
      )}

      <GuideProgress
        done={done}
        total={stops.length}
        minutesLeft={minutesLeft}
      />

      <p
        data-testid="guide-geo-status"
        className="flex items-center gap-1.5 text-[12px] text-muted-foreground"
      >
        {geoState === 'denied' ? (
          <LocateFixed className="h-3.5 w-3.5" />
        ) : (
          <Navigation className="h-3.5 w-3.5" />
        )}
        {geoLine}
      </p>

      <GuideStopList
        stops={stops}
        visited={progress.visited}
        nextId={nextStop?.id ?? null}
        nextDistance={nextDistance}
        onToggle={toggle}
      />
    </section>
  );
};

interface GuideHeaderProps {
  onReset: () => void;
}

/** Panel title + the one destructive control, kept quiet on purpose. */
const GuideHeader = ({ onReset }: GuideHeaderProps) => (
  <div className="flex items-center justify-between gap-2">
    <div className="flex items-center gap-2">
      <span className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Footprints className="h-4 w-4" />
      </span>
      <div>
        <div className="text-[15px] font-semibold leading-tight">Проводник</div>
        <div className="text-[12px] text-muted-foreground">
          идём по маршруту остановка за остановкой
        </div>
      </div>
    </div>
    <button
      type="button"
      onClick={onReset}
      title="начать маршрут заново"
      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3 text-[12px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <RotateCcw className="h-3.5 w-3.5" />
      сбросить прогресс
    </button>
  </div>
);
