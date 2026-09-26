import { useState } from 'react';
import { GripVertical, Pin, Trash2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';
import { useDirectionsQuery } from '@/hooks/use-directions-queries';
import { PlaceIcon } from './parts/place-icon';

interface Props {
  onChanged: () => void; // called after any local mutation that needs a route refetch
}

/**
 * The stops timeline: numbered circles joined by a hairline, the stop's category
 * and name, how long it is worth staying for, and — on hover — remove / pin.
 *
 * Reordering is drag (HTML5 native) with the arrow keys on the drag handle as
 * the keyboard equivalent, so nothing is mouse-only.
 *
 * Reads from useDirectionsStore; mutates via setWaypoint / doRemoveWaypoint and
 * asks the parent to refetch the route.
 */
export const WaypointList = ({ onChanged }: Props) => {
  const waypoints = useDirectionsStore((s) => s.waypoints);
  const setWaypoint = useDirectionsStore((s) => s.setWaypoint);
  const doRemoveWaypoint = useDirectionsStore((s) => s.doRemoveWaypoint);
  const excludeStops = useDirectionsStore((s) => s.excludeStops);
  const placeDetails = useDirectionsStore((s) => s.placeDetails);
  const { refetch } = useDirectionsQuery();
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const update = (next: Waypoint[]) => {
    setWaypoint(next);
    onChanged();
    refetch();
  };

  const move = (from: number, to: number) => {
    if (
      from === to ||
      from < 0 ||
      to < 0 ||
      from >= waypoints.length ||
      to >= waypoints.length
    ) {
      return;
    }
    const next = [...waypoints];
    const [moved] = next.splice(from, 1);
    if (!moved) return;
    next.splice(to, 0, moved);
    update(next);
  };

  const remove = (i: number) => {
    // A stop deleted by hand is remembered: a later refinement («добавь ещё
    // кофейню») must not quietly put the same place back on the route.
    const placeId = waypoints[i]?.placeId;
    doRemoveWaypoint({ index: i });
    if (placeId != null) excludeStops({ placeIds: [placeId] });
    onChanged();
    refetch();
  };

  /**
   * Pin a stop so the next refinement keeps it: `pinned` is what the sidebar
   * sends in `context.base_points`. Only the flag changes — the line on the map
   * does not, so there is nothing to refetch.
   */
  const togglePin = (i: number) => {
    setWaypoint(
      waypoints.map((wp, idx) =>
        idx === i ? { ...wp, pinned: !wp.pinned } : wp
      )
    );
    onChanged();
  };

  const nameOf = (wp: Waypoint): string => {
    const sel =
      wp.geocodeResults.find((r) => r.selected) ?? wp.geocodeResults[0];
    return sel?.title ?? wp.userInput ?? '—';
  };

  if (waypoints.length === 0) {
    return null;
  }

  return (
    <ol className="flex flex-col">
      {waypoints.map((wp, i) => {
        // "my location" is the start, not a stop: it takes no number, so the
        // tourist's stops stay numbered 1..N exactly as on the map.
        const isMe = wp.id === ME_WAYPOINT_ID;
        const stopNumber =
          waypoints.slice(0, i).filter((w) => w.id !== ME_WAYPOINT_ID).length +
          1;
        const isLast = i === waypoints.length - 1;
        const isDragging = dragIndex === i;
        const isDragTarget =
          dragIndex !== null && dragIndex !== i && i === (dragIndex ?? -1) + 1;
        const name = nameOf(wp);
        const details =
          wp.placeId != null ? placeDetails[wp.placeId] : undefined;
        const visitMinutes = details?.visitMinutes ?? null;
        return (
          <li
            key={wp.id}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', String(i));
              setDragIndex(i);
            }}
            onDragOver={(e) => {
              if (dragIndex === null) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = 'move';
            }}
            onDragEnd={() => setDragIndex(null)}
            onDrop={(e) => {
              e.preventDefault();
              const from = Number(e.dataTransfer.getData('text/plain'));
              setDragIndex(null);
              if (!Number.isFinite(from)) return;
              move(from, i);
            }}
            className={cn(
              'group flex min-h-[52px] items-center gap-2 rounded-xl px-1.5 transition-colors hover:bg-muted',
              isDragging && 'opacity-40',
              isDragTarget && 'bg-muted ring-1 ring-primary/40'
            )}
          >
            <button
              type="button"
              className="shrink-0 cursor-grab text-muted-foreground active:cursor-grabbing"
              aria-label={`переместить: ${name}`}
              title="перетащить · стрелки вверх/вниз"
              onKeyDown={(e) => {
                if (e.key === 'ArrowUp' && i > 0) {
                  e.preventDefault();
                  move(i, i - 1);
                } else if (e.key === 'ArrowDown' && i < waypoints.length - 1) {
                  e.preventDefault();
                  move(i, i + 1);
                }
              }}
            >
              <GripVertical className="h-4 w-4" aria-hidden="true" />
            </button>

            {/* The number column carries the 1px timeline rule between rows. */}
            <div className="relative flex w-6 shrink-0 self-stretch items-center justify-center">
              {!isLast && (
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border"
                />
              )}
              {isMe ? (
                <span
                  aria-label="старт маршрута"
                  title="старт"
                  className="relative h-2.5 w-2.5 rounded-full bg-sky-500 ring-4 ring-card"
                />
              ) : (
                <span
                  className="relative flex h-6 w-6 items-center justify-center rounded-full bg-muted text-[11px] font-semibold text-foreground"
                  title={`остановка ${stopNumber}`}
                >
                  {stopNumber}
                </span>
              )}
            </div>

            <PlaceIcon category={details?.category} />

            <span className="min-w-0 flex-1 truncate text-[14px]" title={name}>
              {name}
            </span>

            {visitMinutes != null && (
              <span className="shrink-0 text-[12px] text-muted-foreground">
                ~{visitMinutes} мин
              </span>
            )}

            {/* Always reachable on touch (no hover), revealed on hover on desktop. */}
            <div className="flex shrink-0 items-center gap-0.5 transition-opacity md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100">
              <button
                type="button"
                onClick={() => togglePin(i)}
                aria-pressed={wp.pinned === true}
                aria-label={wp.pinned ? 'открепить' : 'закрепить'}
                title={
                  wp.pinned
                    ? 'уточнение не будет убирать эту точку'
                    : 'закрепить: уточнение не уберёт эту точку'
                }
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-full transition-colors hover:bg-muted',
                  wp.pinned
                    ? 'text-primary'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                <Pin
                  className={cn('h-3.5 w-3.5', wp.pinned && 'fill-current')}
                  aria-hidden="true"
                />
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                aria-label="удалить"
                title="удалить"
                className="flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </li>
        );
      })}
    </ol>
  );
};
