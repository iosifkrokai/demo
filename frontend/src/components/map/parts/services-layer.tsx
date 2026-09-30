import { useState } from 'react';
import { Marker, Popup } from 'react-map-gl/maplibre';
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

export interface ServicesLayerProps {
  items: ServiceAlong[];
}

export function ServicesLayer({ items }: ServicesLayerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<ServiceAlong | null>(null);

  return (
    <>
      {items.map((service) => {
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
