import { useState } from 'react';
import {
  ChevronDown,
  CircleCheck,
  Clock,
  Footprints,
  MapPin,
  Trash2,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  type RouteHistoryEntry,
  type RouteHistoryWalk,
} from '@/stores/directions-store';
import { placeCountRu } from '@/utils/plural';
import { fmtMin } from './guide-format';

interface HistoryTabProps {
  entries: readonly RouteHistoryEntry[];
  /** Put a past route back on the map — the caller owns the stores. */
  onRestore: (entry: RouteHistoryEntry) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
}

/**
 * «История» — its own tab rather than a section at the bottom of the planner.
 *
 * It used to sit under the planner's own controls, which made it look like part
 * of building the next route and pushed it below the fold on a phone. The list
 * itself is unchanged: the same rows, each one restorable with its stops.
 */
export function HistoryTab({
  entries,
  onRestore,
  onRemove,
  onClear,
}: HistoryTabProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="history-tab">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-meta text-muted-foreground">
          Построенные маршруты
          {entries.length > 0 && (
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-badge text-muted-foreground">
              {entries.length}
            </span>
          )}
        </p>
        {entries.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-destructive max-md:min-h-11 pointer-coarse:min-h-11"
            title="очистить историю"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            очистить
          </button>
        )}
      </div>

      {entries.length === 0 ? (
        <p className="text-meta text-muted-foreground">
          Здесь появятся маршруты, которые вы построите — их можно будет открыть
          заново вместе с остановками.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {entries.map((entry) => (
            <HistoryItem
              key={entry.id}
              entry={entry}
              onLoad={() => onRestore(entry)}
              onRemove={() => onRemove(entry.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The day a walk last moved: «27 сентября».
 *
 * Only the day, formatted here rather than in a date library: the history shows
 * it next to a state («пройден»), and the exact time of day was never asked for.
 */
const walkDay = (at: number) =>
  new Date(at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });

/** «пройден» when every stop was visited, otherwise how far it got. */
const WalkBadge = ({ id, walk }: { id: string; walk: RouteHistoryWalk }) => (
  <p
    data-testid={`history-walk-${id}`}
    className={cn(
      'mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-badge',
      walk.completed
        ? 'bg-primary/10 font-semibold text-primary'
        : 'bg-muted text-muted-foreground'
    )}
  >
    {walk.completed ? (
      <>
        <CircleCheck className="h-3 w-3" aria-hidden="true" />
        пройден
      </>
    ) : (
      <>
        <Footprints className="h-3 w-3" aria-hidden="true" />
        пройдено {walk.visited} из {walk.total}
      </>
    )}
    <span className="font-normal opacity-80">· {walkDay(walk.at)}</span>
  </p>
);

interface HistoryItemProps {
  entry: RouteHistoryEntry;
  onLoad: () => void;
  onRemove: () => void;
}

const HistoryItem = ({ entry, onLoad, onRemove }: HistoryItemProps) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-border p-2.5 text-label">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onLoad}
          className="min-w-0 flex-1 truncate text-left font-medium transition-colors hover:text-primary"
          title={entry.query}
        >
          {entry.query.length > 35
            ? entry.query.slice(0, 35) + '…'
            : entry.query}
        </button>
        <div className="flex shrink-0 items-center gap-1">
          <span className="flex items-center gap-0.5 text-meta text-muted-foreground">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {entry.timeBudget <= 0 ? 'без лимита' : fmtMin(entry.timeBudget)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-destructive max-md:h-11 max-md:w-11 pointer-coarse:h-11 pointer-coarse:w-11"
            title="удалить"
            aria-label="удалить из истории"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      {/* A route you actually walked is a different thing from one you only
          planned, so the state is visible without expanding the row. */}
      {entry.walk && <WalkBadge id={entry.id} walk={entry.walk} />}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="mt-1 inline-flex items-center gap-1 text-meta text-muted-foreground transition-colors hover:text-foreground max-md:min-h-11 pointer-coarse:min-h-11"
      >
        <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
        {placeCountRu(entry.places.length)}
        <ChevronDown
          className={`h-3.5 w-3.5 transition-transform ${
            expanded ? 'rotate-180' : ''
          }`}
          aria-hidden="true"
        />
      </button>
      {expanded && (
        <ol className="mt-1.5 flex flex-col gap-0.5 pl-4">
          {entry.places.map((p, i) => (
            <li
              key={p.id}
              className="flex items-center gap-1.5 text-meta text-muted-foreground"
            >
              <span className="h-4 w-4 rounded-full bg-muted text-center text-badge font-semibold leading-4 text-foreground">
                {i + 1}
              </span>
              <span className="min-w-0 break-words">{p.name}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
};
