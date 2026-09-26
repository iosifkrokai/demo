import { Check, MapPin } from 'lucide-react';

import { fmtDist } from './guide-format';
import { PlaceIcon } from './place-icon';

/** What the list needs from a stop — the panel keeps the rest. */
export interface GuideStopItem {
  id: string;
  name: string;
  category?: string | null;
  visitMinutes?: number | null;
}

interface GuideStopListProps {
  stops: GuideStopItem[];
  visited: string[];
  nextId: string | null;
  /** Live distance to the next stop, shown in its row instead of the time. */
  nextDistance: number | null;
  onToggle: (id: string) => void;
}

/**
 * The route as a timeline: 52px rows, a hairline between the numbered circles,
 * done rows muted and struck through, the next one picked out in the accent.
 * A tap is the manual way to mark a stop — every row is a big, quiet target.
 */
export const GuideStopList = ({
  stops,
  visited,
  nextId,
  nextDistance,
  onToggle,
}: GuideStopListProps) => (
  <ol className="flex flex-col">
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
                'flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold',
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

          <button
            type="button"
            data-testid={`guide-stop-${i + 1}`}
            onClick={() => onToggle(stop.id)}
            aria-pressed={isDone}
            aria-current={isNext ? 'step' : undefined}
            className={[
              'flex min-h-[52px] w-full items-center gap-2 rounded-xl pr-2 pl-1 text-left transition-colors',
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
                'min-w-0 flex-1 truncate text-[14px]',
                isDone
                  ? 'text-muted-foreground line-through'
                  : isNext
                    ? 'font-semibold text-foreground'
                    : 'text-foreground',
              ].join(' ')}
            >
              {stop.name}
            </span>

            {isNext && nextDistance != null ? (
              <span className="flex shrink-0 items-center gap-1 text-[12px] font-medium text-primary">
                <MapPin className="h-3 w-3" />
                {fmtDist(nextDistance)}
              </span>
            ) : (
              stop.visitMinutes != null && (
                <span className="shrink-0 text-[12px] text-muted-foreground">
                  ~{stop.visitMinutes} мин
                </span>
              )
            )}
          </button>
        </li>
      );
    })}
  </ol>
);
