import { Clock, ExternalLink, Footprints, MapPin } from 'lucide-react';

import { fmtDist, fmtMin } from './guide-format';
import { PlaceIcon } from './place-icon';

interface GuideNextStopProps {
  /** 1-based place of this stop on the route. */
  number: number;
  name: string;
  category: string | null;
  visitMinutes: number | null;
  /**
   * Live distance to the stop; null while the browser has not said where we are
   * — or while the fix is too coarse to turn into a confident number.
   */
  distance: number | null;
  /** Walking time to the stop, from the route's own time/length. */
  walkMinutes?: number | null;
  /** «≈ 14:35» — arrival estimate, only when the fix can carry one. */
  etaLabel?: string | null;
  mapsHref: string;
}

/**
 * The one thing the tourist needs right now: where they are going next, how
 * far it is, when they get there, and how to hand it to a maps app. Remounted
 * (keyed on the stop) by the panel so the card fades in again when the next
 * stop changes.
 */
export const GuideNextStop = ({
  number,
  name,
  category,
  visitMinutes,
  distance,
  walkMinutes,
  etaLabel,
  mapsHref,
}: GuideNextStopProps) => (
  <div
    data-testid="guide-next-stop"
    className="animate-in fade-in-0 motion-safe:slide-in-from-bottom-1 rounded-2xl border border-border bg-card p-4 shadow-card duration-200 ease-out"
  >
    <div className="flex items-center justify-between gap-2">
      <span className="text-meta font-medium text-muted-foreground">
        следующая остановка
      </span>
      {distance != null && (
        <span
          data-testid="guide-next-distance"
          className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-meta font-medium text-foreground"
        >
          <MapPin className="h-3 w-3 text-primary" />
          до неё {fmtDist(distance)}
        </span>
      )}
    </div>

    <div className="mt-2.5 flex items-start gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-body font-semibold text-primary-foreground">
        {number}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-title font-semibold leading-tight">{name}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-meta text-muted-foreground">
          {category && (
            <span className="inline-flex items-center gap-1.5">
              <PlaceIcon category={category} />
              {category}
            </span>
          )}
          {visitMinutes != null && (
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" />
              осмотр {fmtMin(visitMinutes)}
            </span>
          )}
        </div>
      </div>
    </div>

    {(walkMinutes != null || etaLabel != null) && (
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-2.5 text-meta text-muted-foreground">
        {walkMinutes != null && (
          <span
            data-testid="guide-next-walk"
            className="inline-flex items-center gap-1"
          >
            <Footprints className="h-3.5 w-3.5" />
            идти ~{fmtMin(walkMinutes)}
          </span>
        )}
        {etaLabel != null && (
          <span
            data-testid="guide-next-eta"
            className="inline-flex items-center gap-1"
          >
            <Clock className="h-3.5 w-3.5" />
            прибытие {etaLabel}
          </span>
        )}
      </div>
    )}

    <a
      href={mapsHref}
      target="_blank"
      rel="noreferrer"
      className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-secondary font-semibold text-secondary-foreground transition hover:brightness-[0.97] active:scale-[0.99]"
    >
      <ExternalLink className="h-4 w-4" />
      открыть в картах
    </a>
  </div>
);
