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

/**
 * The services beside a route: cafés, restaurants, toilets, hotels.
 *
 * Design rules, matching the agent's own:
 *
 * * **Never a stop.** These marks carry no number and never join the route's
 *   stops; they are the second row of the guide, not the itinerary.
 * * **Distance is not a detour.** The card prints «N м в сторону от маршрута»
 *   from the measured distance, and says out loud that the walk to reach it has
 *   not been computed. Printing «+2 мин» here would be inventing a number.
 * * **Unknown hours are unknown.** The dataset has hours for about half of
 *   these points; the rest say «часы неизвестны» instead of implying «открыто».
 */

const ICONS: Record<string, typeof Coffee> = {
  кафе: Coffee,
  ресторан: UtensilsCrossed,
  гостиница: BedDouble,
  туалет: Toilet,
  'остановка транспорта': Bus,
};

/** The glyph for a category; a plain dot for anything the map does not know. */
export const serviceIcon = (category: string) => ICONS[category] ?? Circle;

/**
 * How many marks a phone shows at once, and how far apart they must sit.
 *
 * Measured on 390x844 with a 12-place route: drawn all at once they made 29
 * overlapping pairs — clumps of four to six icons on top of each other, ten of
 * them lying across the route's own place names. Unreadable, and the map read
 * as broken rather than busy.
 *
 * So on a phone the marks are thinned: the ones nearest the middle of what the
 * tourist is looking at, spaced far enough apart not to collide. Four is the
 * number that fits between the sheet and the top of the screen without touching
 * the route's own labels. A desktop screen has the room for all of them.
 */
export const PHONE_MAX_MARKS = 4;

/**
 * Screen pixels apart before two marks count as the same spot on a phone.
 *
 * Measured on 390x844 rather than guessed in metres: what matters is whether
 * two 36px icons and a 210px place plate collide *on this screen*, and that
 * depends on zoom, not on distance. A 90m gap that looks generous on the map is
 * a pile of icons at street zoom, and it said nothing about the route's own
 * labels, which are the widest thing a mark can land on.
 */
export const PHONE_MIN_GAP_PX = 56;

/**
 * The route markers' captions, in screen coordinates.
 *
 * On a phone they are 156px wide — the widest thing on the map — and sit right
 * where a café icon wants to be. A mark covering one hides the name of the stop
 * it is next to, which is the one collision the tourist actually notices. Their
 * width comes from CSS truncation, so it is known only after layout and has to be
 * read from the DOM rather than projected.
 */
/**
 * The route markers' captions, as boxes in screen coordinates.
 *
 * Read from the DOM because their width comes from CSS truncation and nothing
 * else knows the final value. `useSyncExternalStore` rather than state in an
 * effect: this is a subscription to an external thing that changes (the camera,
 * and the layout that follows it), which is exactly what that hook is for — and
 * it avoids a cascading render on every camera move.
 */
/**
 * The last read, kept so an unchanged layout returns the *same array*.
 *
 * `useSyncExternalStore` compares snapshots by identity: a fresh array every read
 * is read as a change every time, and React re-renders forever. Measured after
 * layout, so the boxes belong to the frame just painted.
 */
let lastBoxes: ScreenBox[] = [];

const readLabelBoxes = (): ScreenBox[] => {
  const next: ScreenBox[] = [];
  for (const el of document.querySelectorAll(
    '[data-testid="place-marker-label"]'
  )) {
    const r = el.getBoundingClientRect();
    // Off-screen captions are not drawn and cannot collide, though their rect
    // would still report a box.
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
      // `moveend` rather than `move`: the captions are re-laid-out as the camera
      // settles, and reading their boxes mid-pan catches them in flight. The
      // marks are moving through those frames anyway.
      instance.on('moveend', onChange);
      const id = window.requestAnimationFrame(onChange);
      return () => {
        instance.off?.('moveend', onChange);
        window.cancelAnimationFrame(id);
      };
    },
    [map]
  );

  // The server snapshot is only for SSR, where there is no DOM to measure; an
  // empty list thins nothing.
  return useSyncExternalStore(subscribe, readLabelBoxes, () => []);
};

/** Half the 36px mark: the distance from its centre to its edge. */
const MARK_HALF_PX = 18;

/**
 * How much of a mark may sit over a place name before the mark is dropped.
 *
 * Not a distance test. Both distance rules were tried and both are wrong in a
 * way the screen shows: a strict edge test threw away marks that merely grazed a
 * plate's corner and left two marks on a twelve-place route, while a loose one
 * let a mark sit 27 % over a name. What matters is whether the mark *covers*
 * the name, and 12 % of a 36px icon is a corner touch that still reads as two
 * separate things on the map.
 */
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
  /**
   * Thin the marks down for a phone: how many to show, how far apart they must
   * sit, and the screen boxes they must keep clear. Left out (as on a wide
   * screen) every mark is drawn and nothing is avoided.
   */
  thin?: {
    max: number;
    minGapPx: number;
    /**
     * Keep the marks off the route's own place names, read from the DOM at the
     * moment of thinning.
     *
     * They are read here rather than passed in: the captions are re-laid-out by
     * the same camera move that moves the marks, so boxes handed down from the
     * parent are a frame stale — which is exactly the frame where a mark lands on
     * the name it was supposed to avoid.
     */
    avoidLabels?: boolean;
  };
}

/**
 * The marks a phone can actually show: the ones nearest the middle of the map,
 * spaced far enough apart not to sit on top of each other.
 *
 * A pure function so the rule is testable without a map: pass the items, the
 * centre and the limits, get back the marks to draw. Wide screens pass no
 * `thin` and get everything.
 */
export const visibleServices = (
  items: ServiceAlong[],
  centre: { lat: number; lon: number } | null,
  thin: { max: number; minGapPx: number } | null,
  /** Screen position of each item — what the gap is actually measured in. */
  screenPos?: (item: ServiceAlong) => { x: number; y: number } | null,
  /**
   * Screen boxes a mark must keep clear — the route's own place names. A mark
   * that covers one hides the name of the stop it sits next to.
   */
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

    // Prefer the screen gap when the map can project; fall back to the
    // geographic one when it cannot (no map yet, or a test).
    const at = screenPos?.(item) ?? null;
    const tooClose = at
      ? keptScreen.some(
          (k) => Math.hypot(k.x - at.x, k.y - at.y) < thin.minGapPx
        )
      : kept.some((k) => metresBetween(k, item) < thin.minGapPx);
    if (tooClose) continue;

    // How much of the mark would sit over a place name. The name is the thing
    // being read; a mark covering a quarter of it is a mark in the way.
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

  // The middle of what the tourist is looking at, re-read as the camera moves:
  // "nearest to the centre" only means anything if it follows them.
  const [centre, setCentre] = useState<{ lat: number; lon: number } | null>(
    null
  );
  useEffect(() => {
    const instance = map?.getMap();
    // Every one of these is feature-detected: this component also renders
    // outside a real map (a story, a test), and a map that has not finished
    // creating yet is missing half its API. Throwing here took the whole map
    // down, which is a much worse failure than showing every mark.
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

  // Where each mark would actually land on this screen. That is the only
  // distance that decides whether two 36px icons — or an icon and a route
  // place name 210px wide — collide, and it moves with the camera.
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

  // Unconditional: a hook called only on a phone is a hook that breaks the
  // moment the window changes width.
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
                // The click belongs to the mark, not to the map underneath it.
                event.stopPropagation();
                setOpen(service);
              }}
              className="flex h-6 w-6 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-card transition-colors hover:text-foreground pointer-coarse:h-9 pointer-coarse:w-9"
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
            {/* Said out loud, because the number above is a distance, not a walk. */}
            <span className="text-meta text-muted-foreground">
              {t('map.serviceNoDetour')}
            </span>
          </div>
        </Popup>
      )}
    </>
  );
}
