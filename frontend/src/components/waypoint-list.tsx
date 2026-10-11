import { useMemo, useState } from 'react';
import { GripVertical, Pin, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import {
  ME_WAYPOINT_ID,
  useDirectionsStore,
  type Waypoint,
} from '@/stores/directions-store';
import { useDirectionsQuery } from '@/hooks/use-directions-queries';
import { useVisitOverrides } from '@/hooks/use-visit-overrides';
import { PlaceIcon } from './parts/place-icon';
import { useCommonStore } from '@/stores/common-store';
import { VisitTimeEditor } from './parts/visit-time-editor';

interface Props {
  onChanged: () => void;
}

/** Which route the visit times belong to. */
export const plannerVisitKey = (waypoints: Waypoint[]): string =>
  `planner:${waypoints
    .filter((wp) => wp.id !== ME_WAYPOINT_ID)
    .map((wp) => (wp.placeId != null ? `p${wp.placeId}` : `w${wp.id}`))
    .sort()
    .join(',')}`;

/** The key a stop's time is stored under: its place, its slot as a fallback. */
const stopTimeId = (wp: Waypoint): string =>
  wp.placeId != null ? String(wp.placeId) : wp.id;

/** Stops timeline: numbered circles, category, name, suggested stay, remove/pin on hover. */
export const WaypointList = ({ onChanged }: Props) => {
  const { t } = useTranslation();
  const waypoints = useDirectionsStore((s) => s.waypoints);
  const setWaypoint = useDirectionsStore((s) => s.setWaypoint);
  const doRemoveWaypoint = useDirectionsStore((s) => s.doRemoveWaypoint);
  const excludeStops = useDirectionsStore((s) => s.excludeStops);
  const placeDetails = useDirectionsStore((s) => s.placeDetails);
  const focusOn = useCommonStore((s) => s.focusOn);
  const { refetch } = useDirectionsQuery();
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const routeKey = useMemo(() => plannerVisitKey(waypoints), [waypoints]);
  const { overrides, setVisitMinutes, effectiveMinutesFor } =
    useVisitOverrides(routeKey);

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
    const placeId = waypoints[i]?.placeId;
    doRemoveWaypoint({ index: i });
    if (placeId != null) excludeStops({ placeIds: [placeId] });
    onChanged();
    refetch();
  };

  /** Pin a stop so refinement keeps it (`pinned` → `context.base_points`). */
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
        const timeId = stopTimeId(wp);
        const estimate = details?.visitMinutes ?? null;
        const effective = effectiveMinutesFor(timeId, estimate);
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
              'group flex min-h-[52px] flex-wrap items-center gap-2 gap-y-1.5 rounded-xl px-1.5 transition-colors hover:bg-muted',
              'max-md:grid max-md:grid-cols-[2.5rem_1.25rem_minmax(0,1fr)_auto_auto] max-md:gap-x-1.5 max-md:flex-nowrap',
              isDragging && 'opacity-40',
              isDragTarget && 'bg-muted ring-1 ring-primary/40'
            )}
          >
            <button
              type="button"
              className="flex h-9 w-9 shrink-0 cursor-grab items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted active:cursor-grabbing max-md:col-start-1 max-md:row-start-1 max-md:h-10 max-md:w-10 pointer-coarse:h-11 pointer-coarse:w-11"
              aria-label={t('sidebar.waypoints.move', { name })}
              title={t('sidebar.waypoints.dragHint')}
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

            <div className="relative flex w-6 shrink-0 self-stretch items-center justify-center max-md:col-start-2 max-md:row-start-1 max-md:w-5 max-md:self-center">
              {!isLast && (
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border"
                />
              )}
              {isMe ? (
                <span
                  aria-label={t('sidebar.ui.start')}
                  title={t('sidebar.waypoints.start')}
                  className="relative h-2.5 w-2.5 rounded-full bg-sky-500 ring-4 ring-card"
                />
              ) : (
                <span
                  className="relative flex h-6 w-6 items-center justify-center rounded-full bg-muted text-badge font-semibold text-foreground"
                  title={t('sidebar.waypoints.stop', { number: stopNumber })}
                >
                  {stopNumber}
                </span>
              )}
            </div>

            <PlaceIcon category={details?.category} className="max-md:hidden" />

            <button
              type="button"
              onClick={() => {
                const selected =
                  wp.geocodeResults.find((g) => g.selected) ??
                  wp.geocodeResults[0];
                const lngLat =
                  selected?.sourcelnglat ?? selected?.displaylnglat;
                if (lngLat) focusOn(lngLat[0], lngLat[1]);
              }}
              className="min-w-0 flex-1 break-words text-left text-body transition-colors hover:text-primary max-md:col-start-3 max-md:row-start-1 max-md:flex-none max-md:self-stretch max-md:truncate max-md:break-normal max-md:flex max-md:items-center max-md:py-0"
              title={name}
              data-testid={`focus-place-${wp.id}`}
            >
              {name}
            </button>

            {effective != null && (
              <VisitTimeEditor
                compact
                estimate={estimate}
                value={overrides[timeId] ?? null}
                onChange={(minutes) => setVisitMinutes(timeId, minutes)}
                className="max-md:col-start-4 max-md:row-start-1 max-md:self-center"
              />
            )}

            <div className="flex shrink-0 items-center gap-0.5 transition-opacity max-md:col-start-5 max-md:row-start-1 max-md:self-center max-md:gap-0 md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100">
              <button
                type="button"
                onClick={() => togglePin(i)}
                aria-pressed={wp.pinned === true}
                aria-label={
                  wp.pinned
                    ? t('sidebar.waypoints.unpin')
                    : t('sidebar.waypoints.pin')
                }
                title={
                  wp.pinned
                    ? t('sidebar.waypoints.pinnedHint')
                    : t('sidebar.waypoints.pinHint')
                }
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-full transition-colors hover:bg-muted max-md:h-10 max-md:w-10 pointer-coarse:h-11 pointer-coarse:w-11',
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
                aria-label={t('sidebar.waypoints.remove')}
                title={t('sidebar.waypoints.remove')}
                className="flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-destructive max-md:h-10 max-md:w-10 pointer-coarse:h-11 pointer-coarse:w-11"
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
