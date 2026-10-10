import { Clock, ExternalLink, MapPin, ParkingCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { fmtDist, fmtMin } from './guide-format';
import type { GuideTravelMode } from './guide-mode';
import { PlaceIcon } from './place-icon';
import { VisitTimeEditor } from './visit-time-editor';

interface GuideNextStopProps {
  /** 1-based place of this stop on the route. */
  number: number;
  name: string;
  category: string | null;
  /** Minutes to plan with here: the tourist's own number, else the estimate. */
  visitMinutes: number | null;
  /** The number the tourist chose, or null while the estimate stands. */
  visitOverride?: number | null;
  /** The dataset's estimate, kept apart so the editor can offer a reset. */
  estimateMinutes?: number | null;
  /** When given, the tourist may set their own time at this stop. */
  onVisitMinutesChange?: (minutes: number | null) => void;
  /** Live distance to the stop; null while the browser has not said where we are — or while the fix is too coarse to turn into a confident number. */
  distance: number | null;
  /** Travel time to the stop, from the route's own time and length. */
  travelMinutes?: number | null;
  /** «≈ 14:35» — arrival estimate, only when the fix can carry one. */
  etaLabel?: string | null;
  /** How the tourist moves: «идти» on foot, «ехать» by bike or car. */
  mode: GuideTravelMode;
  mapsHref: string;
  /** The navigator's version of the card: name, number and distance only. */
  compact?: boolean;
}

/** The one thing the tourist needs right now: where they are going next, how far it is, when they get there, and how to hand it to a maps app. */
export const GuideNextStop = ({
  number,
  name,
  category,
  visitMinutes,
  visitOverride = null,
  estimateMinutes = null,
  onVisitMinutesChange,
  distance,
  travelMinutes,
  etaLabel,
  mode,
  mapsHref,
  compact = false,
}: GuideNextStopProps) => {
  const { t } = useTranslation();
  const TravelIcon = mode.icon;

  return (
    <div
      data-testid="guide-next-stop"
      className={
        compact
          ? 'relative animate-in fade-in-0 motion-safe:slide-in-from-bottom-1 rounded-2xl border border-border bg-card p-3 pr-14 shadow-card duration-200 ease-out'
          : 'animate-in fade-in-0 motion-safe:slide-in-from-bottom-1 rounded-2xl border border-border bg-card p-4 shadow-card duration-200 ease-out'
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-meta font-medium text-muted-foreground">
          {t('guide.nextStop')}
        </span>
        {distance != null && (
          <span
            data-testid="guide-next-distance"
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-meta font-medium text-foreground"
          >
            <MapPin className="h-3 w-3 text-primary" />
            {t('guide.distanceToStop', { distance: fmtDist(distance) })}
          </span>
        )}
      </div>

      <div
        className={
          compact
            ? 'mt-1.5 flex items-center gap-2.5'
            : 'mt-2.5 flex items-start gap-3'
        }
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-body font-semibold text-primary-foreground">
          {number}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-title font-semibold leading-tight">{name}</div>
          {!compact && (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
              {category && (
                <span className="inline-flex items-center gap-1.5">
                  <PlaceIcon category={category} />
                  {category}
                </span>
              )}
              {visitMinutes != null && onVisitMinutesChange && (
                <VisitTimeEditor
                  estimate={estimateMinutes}
                  value={visitOverride}
                  onChange={onVisitMinutesChange}
                />
              )}
              {visitMinutes != null && !onVisitMinutesChange && (
                <span className="inline-flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5" />
                  {t('guide.visitApprox', { time: fmtMin(visitMinutes) })}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {!compact &&
        (travelMinutes != null || etaLabel != null || mode.arrivalHint) && (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-2.5 text-meta text-muted-foreground">
            {travelMinutes != null && (
              <span
                data-testid="guide-next-travel"
                className="inline-flex items-center gap-1"
              >
                <TravelIcon className="h-3.5 w-3.5" />
                {t('guide.travelFor', {
                  verb: mode.verb,
                  time: fmtMin(travelMinutes),
                })}
              </span>
            )}
            {etaLabel != null && (
              <span
                data-testid="guide-next-eta"
                className="inline-flex items-center gap-1"
              >
                <Clock className="h-3.5 w-3.5" />
                {t('guide.arrival', { eta: etaLabel })}
              </span>
            )}
            {mode.arrivalHint && (
              <span
                data-testid="guide-next-arrival-hint"
                className="inline-flex items-center gap-1"
              >
                <ParkingCircle className="h-3.5 w-3.5" />
                {mode.arrivalHint}
              </span>
            )}
          </div>
        )}

      {compact ? (
        <a
          href={mapsHref}
          target="_blank"
          rel="noreferrer"
          aria-label={t('guide.openInMaps')}
          title={t('guide.openInMaps')}
          className="absolute right-2 top-2 flex size-11 items-center justify-center rounded-full text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <ExternalLink className="size-4" aria-hidden="true" />
        </a>
      ) : (
        <a
          href={mapsHref}
          target="_blank"
          rel="noreferrer"
          className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-secondary font-semibold text-secondary-foreground transition hover:brightness-[0.97] active:scale-[0.99]"
        >
          <ExternalLink className="h-4 w-4" />
          {t('guide.openInMaps')}
        </a>
      )}
    </div>
  );
};
