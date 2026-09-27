import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Clock, Loader2, MapPin, Route as RouteIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Itinerary, ItineraryStop } from '@/api/types';
import { fmtMin } from './guide-format';
import { guideModeFor } from './guide-mode';
import { STOP_FORMS, pluralCountRu } from '@/utils/plural';

interface ItinerariesTabProps {
  /** Show a curated route on the map. No request to the model is made. */
  onOpen: (itinerary: Itinerary) => void;
  /** A plan is already in flight: opening another route would fight it. */
  disabled?: boolean;
  itineraries: readonly Itinerary[];
  /** Stop keys the dataset no longer holds (see the agent's `missing` list). */
  missing?: readonly string[];
  isLoading?: boolean;
  error?: unknown;
  onReload?: () => void;
}

/**
 * «Готовые» — curated routes with their stops, ready to look at.
 *
 * These are authored by hand (backend/data/itineraries.json) and resolved
 * against the live dataset, so a card prints the real stop names, the curated
 * visit time and the opening hours that are actually known — nothing is
 * described from memory. Tapping «показать на карте» puts the stops on the map
 * and draws the route with the router: it does **not** ask the model for a plan,
 * which is the point of the tab — something to start from without a request.
 *
 * A route the dataset cannot fully resolve is still shown, with the shortfall
 * stated, rather than quietly presented as complete.
 */
export function ItinerariesTab({
  itineraries,
  missing = [],
  isLoading = false,
  error,
  onOpen,
  onReload,
  disabled = false,
}: ItinerariesTabProps) {
  const { t } = useTranslation();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <p
        className="flex items-center gap-2 text-meta text-muted-foreground"
        data-testid="itineraries-loading"
        role="status"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t('itineraries.loading')}
      </p>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-2" data-testid="itineraries-error">
        <p className="text-meta text-muted-foreground" role="alert">
          Не удалось загрузить готовые маршруты. Сами маршруты целы — не ответил
          агент.
        </p>
        {onReload && (
          <button
            type="button"
            onClick={() => onReload()}
            className="self-start rounded-full border border-border px-3 py-1.5 text-meta transition-colors hover:bg-muted max-md:min-h-11 pointer-coarse:min-h-11"
          >
            {t('itineraries.retry')}
          </button>
        )}
      </div>
    );
  }

  if (itineraries.length === 0) {
    return (
      <p className="text-meta text-muted-foreground" data-testid="itineraries-empty">
        Готовых маршрутов пока нет.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="itineraries-tab">
      <p className="text-meta text-muted-foreground">
        Собраны вручную из реальных точек данных: открываются сразу, без запроса
        к модели — потом маршрут можно править как обычно.
      </p>

      {missing.length > 0 && (
        <p className="text-meta text-destructive" role="status">
          Часть точек не нашлась в данных ({missing.length}) — такие маршруты
          показаны не полностью.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {itineraries.map((itinerary) => {
          const mode = guideModeFor(itinerary.transport);
          const TransportIcon = mode.icon;
          const expanded = expandedId === itinerary.id;
          return (
            <li key={itinerary.id}>
              <div className="flex flex-col gap-1.5 rounded-2xl border border-border bg-card p-3 shadow-card">
                <h3 className="text-label font-semibold">{itinerary.title}</h3>
                <p className="text-meta text-muted-foreground">
                  {itinerary.blurb}
                </p>

                {/* Only facts the dataset holds: the curated visit time and how
                    many real stops the route has. */}
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-badge text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <TransportIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    {mode.label}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <RouteIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    {pluralCountRu(itinerary.stop_count, STOP_FORMS)}
                  </span>
                  {itinerary.visit_minutes > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('itineraries.visit')} ~{fmtMin(itinerary.visit_minutes)}
                    </span>
                  )}
                </p>

                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    data-testid={`itinerary-open-${itinerary.id}`}
                    disabled={disabled}
                    onClick={() => onOpen(itinerary)}
                    className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-meta font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50 max-md:min-h-11 pointer-coarse:min-h-11"
                  >
                    <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
                    {t('itineraries.open')}
                  </button>
                  <button
                    type="button"
                    data-testid={`itinerary-stops-${itinerary.id}`}
                    aria-expanded={expanded}
                    onClick={() =>
                      setExpandedId(expanded ? null : itinerary.id)
                    }
                    className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground max-md:min-h-11 pointer-coarse:min-h-11"
                  >
                    {t('itineraries.stops')}
                    <ChevronDown
                      className={cn(
                        'h-3.5 w-3.5 transition-transform',
                        expanded && 'rotate-180'
                      )}
                      aria-hidden="true"
                    />
                  </button>
                </div>

                {expanded && (
                  <ol className="mt-1 flex flex-col gap-1.5" data-testid={`itinerary-stops-list-${itinerary.id}`}>
                    {itinerary.stops.map((stop, index) => (
                      <StopRow key={stop.source_url} stop={stop} index={index} />
                    ))}
                  </ol>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** One stop: its number, its name, and the facts that are actually known. */
function StopRow({ stop, index }: { stop: ItineraryStop; index: number }) {
  const { t } = useTranslation();
  const facts = [
    stop.visit_minutes ? fmtMin(stop.visit_minutes) : null,
    stop.opening_hours,
  ].filter((v): v is string => Boolean(v));

  return (
    <li className="flex gap-2 text-meta">
      <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full bg-muted text-center text-badge font-semibold leading-4 text-foreground">
        {index + 1}
      </span>
      {stop.photo && (
        <img
          src={stop.photo.url}
          alt={stop.name}
          loading="lazy"
          decoding="async"
          className="h-14 w-14 shrink-0 rounded-md border border-border object-cover"
        />
      )}
      <span className="min-w-0">
        <span className="block break-words font-medium text-foreground">
          {stop.name}
        </span>
        {facts.length > 0 && (
          <span className="block break-words text-muted-foreground">
            {facts.join(' · ')}
          </span>
        )}
        {/* The credit stays on the row: it is a licence obligation, and a
            thumbnail without it would be an unlicensed picture in a small size. */}
        {stop.photo && (
          <span className="block break-words text-muted-foreground">
            {t('photo.credit', {
              author: stop.photo.author,
              license: stop.photo.license,
            })}
          </span>
        )}
      </span>
    </li>
  );
}
