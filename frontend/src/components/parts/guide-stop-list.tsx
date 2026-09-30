import { Check, ChevronDown, MapPin } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { fmtDist, fmtMin } from './guide-format';
import { PlaceIcon } from './place-icon';
import { VisitTimeEditor } from './visit-time-editor';

/** What the list needs from a stop — the panel keeps the rest. */
export interface GuideStopItem {
  id: string;
  name: string;
  category?: string | null;
  /** Minutes to plan with: the tourist's own number, else the estimate. */
  visitMinutes?: number | null;
  /** The number the tourist chose, or null while the estimate stands. */
  visitOverride?: number | null;
  /** The dataset's estimate, shown as the approximate hint. */
  estimateMinutes?: number | null;
}

interface GuideStopListProps {
  stops: GuideStopItem[];
  visited: string[];
  nextId: string | null;
  /** Live distance to the next stop, shown in its row instead of the time. */
  nextDistance: number | null;
  onToggle: (id: string) => void;
  /** Given by the panel: the tourist may set their own time at a stop. */
  onVisitMinutesChange?: (id: string, minutes: number | null) => void;
  /**
   * Movement mode folds the list behind a row with the count. The map is the
   * main screen there, so the list is one tap away, not always in the way.
   */
  collapsible?: boolean;
  defaultOpen?: boolean;
}

/**
 * The route as a timeline: 52px rows, a hairline between the numbered circles,
 * done rows muted and struck through, the next one picked out in the accent.
 * A tap is the manual way to mark a stop — every row is a big, quiet target,
 * and the same tap advances the walk when geolocation is unavailable.
 */
export const GuideStopList = ({
  stops,
  visited,
  nextId,
  nextDistance,
  onToggle,
  onVisitMinutesChange,
  collapsible = false,
  defaultOpen = true,
}: GuideStopListProps) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen);
  const listId = useId();

  const list = (
    <ol id={listId} className="flex flex-col">
      {stops.map((stop, i) => {
        const isDone = visited.includes(stop.id);
        const isNext = nextId === stop.id;
        const isLast = i === stops.length - 1;

        return (
          <li key={stop.id} className="flex">
            {/* Number + the hairline that ties this stop to the next one. */}
            <div className="flex w-7 shrink-0 flex-col items-center pt-[14px]">
              <span
                className={[
                  'flex size-6 shrink-0 items-center justify-center rounded-full text-badge font-semibold',
                  isDone
                    ? 'bg-muted text-muted-foreground'
                    : isNext
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-foreground',
                ].join(' ')}
              >
                {isDone ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              {!isLast && <span className="mt-1 w-px flex-1 bg-border" />}
            </div>

            {/* The row is tapped to mark the stop; the visit time is its own
                control, so it must not be nested inside the row button. */}
            <div className="flex min-h-[52px] w-full items-center gap-1 pr-2 pl-1">
              <button
                type="button"
                data-testid={`guide-stop-${i + 1}`}
                onClick={() => onToggle(stop.id)}
                aria-pressed={isDone}
                aria-current={isNext ? 'step' : undefined}
                className={[
                  'flex min-w-0 flex-1 items-center gap-2 rounded-xl py-2 text-left transition-colors',
                  isNext
                    ? 'bg-primary/5'
                    : isDone
                      ? ''
                      : 'hover:bg-muted active:bg-muted',
                ].join(' ')}
              >
                <PlaceIcon
                  category={stop.category}
                  className={isDone ? 'opacity-60' : undefined}
                />
                <span
                  className={[
                    'min-w-0 flex-1 truncate text-body',
                    isDone
                      ? 'text-muted-foreground line-through'
                      : isNext
                        ? 'font-semibold text-foreground'
                        : 'text-foreground',
                  ].join(' ')}
                >
                  {stop.name}
                </span>

                {isNext && nextDistance != null && (
                  <span className="flex shrink-0 items-center gap-1 text-meta font-medium text-primary">
                    <MapPin className="h-3 w-3" />
                    {fmtDist(nextDistance)}
                  </span>
                )}
              </button>

              {!(isNext && nextDistance != null) &&
                (stop.visitMinutes != null ? (
                  onVisitMinutesChange ? (
                    <VisitTimeEditor
                      compact
                      estimate={stop.estimateMinutes ?? null}
                      value={stop.visitOverride ?? null}
                      onChange={(minutes) =>
                        onVisitMinutesChange(stop.id, minutes)
                      }
                    />
                  ) : (
                    <span className="shrink-0 text-meta text-muted-foreground">
                      ≈ {fmtMin(stop.visitMinutes)}
                    </span>
                  )
                ) : null)}
            </div>
          </li>
        );
      })}
    </ol>
  );

  if (!collapsible) return list;

  return (
    <div className="rounded-2xl border border-border bg-card shadow-card">
      <button
        type="button"
        data-testid="guide-stop-list-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={listId}
        className="flex h-11 w-full items-center justify-between gap-2 rounded-2xl px-3 text-label font-medium transition-colors hover:bg-muted"
      >
        <span>
          {t('guide.stopListTitle', {
            visited: visited.length,
            total: stops.length,
          })}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            'size-4 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none',
            open && 'rotate-180'
          )}
        />
      </button>
      {open && <div className="px-3 pb-3">{list}</div>}
    </div>
  );
};
