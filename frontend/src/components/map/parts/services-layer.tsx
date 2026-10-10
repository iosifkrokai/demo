import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { Marker, Popup, useMap, type MapRef } from 'react-map-gl/maplibre';
import { useTranslation } from 'react-i18next';
import {
  BedDouble,
  Bus,
  Circle,
  Coffee,
  Toilet,
  UtensilsCrossed,
} from 'lucide-react';

import type { ServiceAlong } from '@/api/types';

/** The services beside a route: cafés, restaurants, toilets, hotels. */

const ICONS: Record<string, typeof Coffee> = {
  кафе: Coffee,
  ресторан: UtensilsCrossed,
  гостиница: BedDouble,
  туалет: Toilet,
  'остановка транспорта': Bus,
};

/** The glyph for a category; a plain dot for anything the map does not know. */
export const serviceIcon = (category: string) => ICONS[category] ?? Circle;

/** How many marks a phone shows at once, and how far apart they must sit. */
export const PHONE_MAX_MARKS = 4;

/** Screen pixels apart before two marks count as the same spot on a phone. */
export const PHONE_MIN_GAP_PX = 56;

/** The route markers' captions, in screen coordinates. */
/** The route markers' captions, as boxes in screen coordinates. */
/** The last read, kept so an unchanged layout returns the *same array*. */
let lastBoxes: ScreenBox[] = [];

const readLabelBoxes = (): ScreenBox[] => {
  const next: ScreenBox[] = [];
  for (const el of document.querySelectorAll(
    '[data-testid="place-marker-label"]'
  )) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > 0 && r.left < window.innerWidth) {
      next.push({ x: r.x, y: r.y, w: r.width, h: r.height });
    }
  }

  const same =
    next.length === lastBoxes.length &&
    next.every(
      (box, i) =>
        box.x === lastBoxes[i]?.x &&
        box.y === lastBoxes[i]?.y &&
        box.w === lastBoxes[i]?.w &&
        box.h === lastBoxes[i]?.h
    );
  if (same) return lastBoxes;

  lastBoxes = next;
  return next;
};

const useRouteLabelBoxes = (map: MapRef | null | undefined): ScreenBox[] => {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const instance = map?.getMap();
      if (!instance?.on) return () => {};
      instance.on('moveend', onChange);
      const id = window.requestAnimationFrame(onChange);
      return () => {
        instance.off?.('moveend', onChange);
        window.cancelAnimationFrame(id);
      };
    },
    [map]
  );

  return useSyncExternalStore(subscribe, readLabelBoxes, () => []);
};

/** Half the 36px mark: the distance from its centre to its edge. */
const MARK_HALF_PX = 18;

/** How much of a mark may sit over a place name before the mark is dropped. */
const LABEL_MAX_COVER = 0.12;

/** Great-circle metres between two points — enough to space marks out. */
const metresBetween = (
  a: { lat: number; lon: number },
  b: { lat: number; lon: number }
): number => {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
};

/** A rectangle in screen pixels — what two things on a map can collide in. */
interface ScreenBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ServicesLayerProps {
  items: ServiceAlong[];
  /** Thin the marks down for a phone: how many to show, how far apart they must sit, and the screen boxes they must keep clear. */
  thin?: {
    max: number;
    minGapPx: number;
    /** Keep the marks off the route's own place names, read from the DOM at the moment of thinning. */
    avoidLabels?: boolean;
  };
}

/** The marks a phone can actually show: the ones nearest the middle of the map, spaced far enough apart not to sit on top of each other. */
export const visibleServices = (
  items: ServiceAlong[],
  centre: { lat: number; lon: number } | null,
  thin: { max: number; minGapPx: number } | null,
  /** Screen position of each item — what the gap is actually measured in. */
  screenPos?: (item: ServiceAlong) => { x: number; y: number } | null,
  /** Screen boxes a mark must keep clear — the route's own place names. */
  labelBoxes: ScreenBox[] = []
): ServiceAlong[] => {
  if (!thin || !centre) return items;

  const nearCentre = [...items].sort(
    (a, b) => metresBetween(a, centre) - metresBetween(b, centre)
  );

  const kept: ServiceAlong[] = [];
  const keptScreen: { x: number; y: number }[] = [];

  for (const item of nearCentre) {
    if (kept.length >= thin.max) break;

    const at = screenPos?.(item) ?? null;
    const tooClose = at
      ? keptScreen.some(
          (k) => Math.hypot(k.x - at.x, k.y - at.y) < thin.minGapPx
        )
      : kept.some((k) => metresBetween(k, item) < thin.minGapPx);
    if (tooClose) continue;

    if (at && labelBoxes.length) {
      const half = MARK_HALF_PX;
      const coversTooMuch = labelBoxes.some((box) => {
        const dx =
          Math.min(at.x + half, box.x + box.w) - Math.max(at.x - half, box.x);
        if (dx <= 0) return false;
        const dy =
          Math.min(at.y + half, box.y + box.h) - Math.max(at.y - half, box.y);
        if (dy <= 0) return false;
        return (dx * dy) / (half * 2 * (half * 2)) > LABEL_MAX_COVER;
      });
      if (coversTooMuch) continue;
    }
    kept.push(item);
    if (at) keptScreen.push(at);
  }
  return kept;
};

export function ServicesLayer({ items, thin }: ServicesLayerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<ServiceAlong | null>(null);
  const { current: map } = useMap();

  const [centre, setCentre] = useState<{ lat: number; lon: number } | null>(
    null
  );
  useEffect(() => {
    const instance = map?.getMap();
    if (typeof instance?.getCenter !== 'function') return;
    const read = () => {
      const c = instance.getCenter?.();
      if (c) setCentre({ lat: c.lat, lon: c.lng });
    };
    read();
    instance.on?.('move', read);
    return () => {
      instance.off?.('move', read);
    };
  }, [map]);

  const screenPos = useCallback(
    (item: ServiceAlong) => {
      const instance = map?.getMap();
      if (typeof instance?.project !== 'function') return null;
      const p = instance.project([item.lon, item.lat]);
      return p && Number.isFinite(p.x) && Number.isFinite(p.y)
        ? { x: p.x, y: p.y }
        : null;
    },
    [map]
  );

  const labelBoxes = useRouteLabelBoxes(map);

  const shown = visibleServices(
    items,
    centre,
    thin ? { max: thin.max, minGapPx: thin.minGapPx } : null,
    screenPos,
    thin?.avoidLabels ? labelBoxes : []
  );

  return (
    <>
      {shown.map((service) => {
        const Icon = serviceIcon(service.category);
        return (
          <Marker
            key={service.source_url}
            longitude={service.lon}
            latitude={service.lat}
            anchor="center"
          >
            <button
              type="button"
              data-testid="service-marker"
              data-category={service.category}
              title={service.name}
              data-hidden-count={items.length - shown.length || undefined}
              onClick={(event) => {
                event.stopPropagation();
                setOpen(service);
              }}
              className="relative flex size-6 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-card transition-colors hover:text-foreground before:absolute before:size-11 before:content-[''] pointer-coarse:before:size-11"
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </Marker>
        );
      })}

      {open && (
        <Popup
          longitude={open.lon}
          latitude={open.lat}
          anchor="bottom"
          offset={18}
          closeButton={false}
          closeOnClick
          onClose={() => setOpen(null)}
          maxWidth="280px"
        >
          <div
            className="flex min-w-[200px] flex-col gap-1.5 px-2.5 py-2.5"
            data-testid="service-card"
            onClick={(event) => event.stopPropagation()}
          >
            <span className="text-body font-semibold leading-tight">
              {open.name}
            </span>
            <span className="text-meta text-muted-foreground">
              {open.category}
              {open.town ? ` · ${open.town}` : ''}
            </span>
            <span className="text-meta">
              {t('map.serviceOffLine', { metres: open.off_line_m })}
            </span>
            <span className="text-meta text-muted-foreground">
              {open.opening_hours ?? t('map.serviceHoursUnknown')}
            </span>
            <span className="text-meta text-muted-foreground">
              {t('map.serviceNoDetour')}
            </span>
          </div>
        </Popup>
      )}
    </>
  );
}
