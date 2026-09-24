import { useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  GripVertical,
  LocateFixed,
  MapPin,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';
import { useDirectionsQuery } from '@/hooks/use-directions-queries';

interface Props {
  onChanged: () => void; // called after any local mutation that needs a route refetch
}

/**
 * List of waypoints with full edit controls:
 *   - drag handle (HTML5 native drag-drop, vertical list reorder)
 *   - up / down arrow buttons (alternative to drag)
 *   - x (delete) — preserves a minimum of 2 waypoints (Valhalla needs at least that)
 *
 * Reads from useDirectionsStore; mutates via setWaypoint / doRemoveWaypoint and
 * asks the parent to refetch the route.
 */
export const WaypointList = ({ onChanged }: Props) => {
  const waypoints = useDirectionsStore((s) => s.waypoints);
  const setWaypoint = useDirectionsStore((s) => s.setWaypoint);
  const doRemoveWaypoint = useDirectionsStore((s) => s.doRemoveWaypoint);
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
    doRemoveWaypoint({ index: i });
    onChanged();
    refetch();
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
    <ol className="flex flex-col gap-1.5">
      {waypoints.map((wp, i) => {
        // "my location" is the start, not a stop: it takes no number, so the
        // tourist's stops stay numbered 1..N exactly as on the map.
        const isMe = wp.id === ME_WAYPOINT_ID;
        const stopNumber =
          waypoints.slice(0, i).filter((w) => w.id !== ME_WAYPOINT_ID).length +
          1;
        const isDragging = dragIndex === i;
        const isDragTarget =
          dragIndex !== null && dragIndex !== i && i === (dragIndex ?? -1) + 1;
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
            className={[
              'flex items-center gap-2 rounded-lg border bg-card px-2 py-1.5 text-sm transition-opacity',
              isDragging ? 'opacity-40' : '',
              isDragTarget
                ? 'border-primary/60 ring-1 ring-primary/30'
                : 'border-border/60',
            ].join(' ')}
          >
            <span
              className="cursor-grab text-muted-foreground active:cursor-grabbing"
              aria-label="перетащить"
              title="перетащить"
            >
              <GripVertical className="h-4 w-4" />
            </span>
            <span
              className={[
                'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium',
                isMe
                  ? 'bg-sky-500/15 text-sky-600'
                  : 'bg-primary/10 text-primary',
              ].join(' ')}
              title={isMe ? 'старт' : `остановка ${stopNumber}`}
            >
              {isMe ? <LocateFixed className="h-3.5 w-3.5" /> : stopNumber}
            </span>
            <MapPin className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate" title={nameOf(wp)}>
              {nameOf(wp)}
            </span>
            <div className="flex shrink-0 items-center gap-0.5">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                disabled={i === 0}
                onClick={() => move(i, i - 1)}
                aria-label="вверх"
                title="вверх"
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                disabled={i === waypoints.length - 1}
                onClick={() => move(i, i + 1)}
                aria-label="вниз"
                title="вниз"
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                onClick={() => remove(i)}
                aria-label="удалить"
                title="удалить"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          </li>
        );
      })}
    </ol>
  );
};
