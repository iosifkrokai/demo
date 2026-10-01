import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { ServiceAlong } from '@/api/types';
import type { ServicesState } from '@/hooks/use-services-along';

import { serviceIcon } from './services-layer';

/**
 * What is beside the route — how many, of what kind, and how close.
 *
 * The layer draws the marks; this says they exist. Without it the services are
 * invisible until the tourist happens to press a small icon: the guide knows
 * there are twelve cafés and toilets within a hundred metres of the walk and
 * said nothing.
 *
 * Three rules it keeps, matching the agent's own:
 *
 * * **Never a stop.** The line counts places *beside* the route and says so; it
 *   never suggests the walk goes through them.
 * * **Distance, not time.** «в пределах 120 м от маршрута» is the measured
 *   off-line distance. «+2 мин» would be a number nobody computed.
 * * **«Не удалось проверить» ≠ «ничего нет».** A failed check is shown as a
 *   failure of the check, never as an empty result.
 */

/** Counts per category, biggest first — the order a tourist would ask in. */
export const categoryCounts = (
  items: ServiceAlong[]
): { category: string; count: number }[] => {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.category, (counts.get(item.category) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category));
};

export interface ServicesSummaryProps {
  items: ServiceAlong[];
  state: ServicesState;
  maxOffLineM: number | null;
  capped?: boolean;
  active: boolean;
  /** The toggle itself, so the count sits beside the control it describes. */
  toggle: ReactNode;
}

export function ServicesSummary({
  items,
  state,
  maxOffLineM,
  capped = false,
  active,
  toggle,
}: ServicesSummaryProps) {
  const { t } = useTranslation();

  if (state === 'idle') return <>{toggle}</>;

  const counts = categoryCounts(items);
  const counted = counts.reduce((sum, entry) => sum + entry.count, 0);
  const showCount = state === 'ready' && counted > 0;

  return (
    <div className="flex flex-col items-end gap-2">
      <div
        data-testid="services-summary"
        className="max-w-[15rem] rounded-lg border border-border bg-card/95 px-2.5 py-2 text-right shadow-card max-md:max-w-[11rem]"
      >
        {state === 'loading' && (
          <span className="text-meta text-muted-foreground">
            {t('map.servicesSearching')}
          </span>
        )}

        {state === 'unavailable' && (
          <span
            data-testid="services-summary-unavailable"
            className="text-meta text-muted-foreground"
          >
            {t('map.servicesUnavailable')}
          </span>
        )}

        {state === 'ready' && !showCount && (
          <span
            data-testid="services-summary-empty"
            className="text-meta text-muted-foreground"
          >
            {t('map.servicesEmpty')}
          </span>
        )}

        {showCount && (
          <>
            <span
              data-testid="services-summary-count"
              className="block text-meta font-medium"
            >
              {t('map.servicesOnRoute', { count: counted })}
            </span>
            {/* How close they are, because «по пути» alone could mean anything. */}
            {maxOffLineM !== null && (
              <span
                data-testid="services-summary-within"
                className="mt-0.5 block text-meta text-muted-foreground"
              >
                {t('map.servicesWithin', { metres: maxOffLineM })}
              </span>
            )}
            {/* On a phone the per-category row is four lines over the map; the
                count and the distance are the part that answers «что там есть»,
                and the toggle right below still opens the marks themselves. */}
            <ul className="mt-1 flex flex-wrap justify-end gap-x-2 gap-y-0.5 max-md:hidden">
              {counts.map(({ category, count }) => {
                const Icon = serviceIcon(category);
                return (
                  <li
                    key={category}
                    data-testid="services-summary-category"
                    data-category={category}
                    className="flex items-center gap-1 text-meta text-muted-foreground"
                  >
                    <Icon className="h-3 w-3" aria-hidden="true" />
                    <span>
                      {t(`map.serviceCat_${category}`, {
                        defaultValue: category,
                      })}
                    </span>
                    <span className="tabular-nums">{count}</span>
                  </li>
                );
              })}
            </ul>
            <span className="mt-1 block text-meta text-muted-foreground">
              {active ? t('map.servicesHide') : t('map.servicesShow')}
            </span>
            {/* A trimmed list must not read as the whole truth. */}
            {capped && (
              <span
                data-testid="services-summary-capped"
                className="mt-0.5 block text-meta text-muted-foreground"
              >
                {t('map.servicesCapped')}
              </span>
            )}
          </>
        )}
      </div>
      {toggle}
    </div>
  );
}
