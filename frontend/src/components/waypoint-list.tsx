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
  onChanged: () => void; // called after any local mutation that needs a route refetch
}

/**
 * Which route the visit times belong to. Built from the set of stops, not their
 * order, so dragging a row around does not throw the tourist's numbers away;
 * a route rebuilt from other places gets its own saved times.
 */
export const plannerVisitKey = (waypoints: Waypoint[]): string =>
  `planner:${waypoints
    .filter((wp) => wp.id !== ME_WAYPOINT_ID)
    .map((wp) => (wp.placeId != null ? `p${wp.placeId}` : `w${wp.id}`))
    .sort()
    .join(',')}`;

/** The key a stop's time is stored under: its place, its slot as a fallback. */
const stopTimeId = (wp: Waypoint): string =>
  wp.placeId != null ? String(wp.placeId) : wp.id;

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
        // The dataset's estimate is only a hint: the editor shows it with a
        // «≈», and the tourist's own number takes over the moment they set it.
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
              // A phone cannot fit six controls — grip, number, icon, name,
              // visit time, pin, remove — so `flex-wrap` broke the row across
              // up to four lines and the tail dropped below: measured on
              // 390x844 a row came out 152px tall instead of 52px, which is the
              // «text stretches down» report, and six stops needed 539px of a
              // 760px sheet. On a phone the row is a grid of one line instead:
              // the category emoji is dropped (the stop number already orders
              // the route) and the name truncates rather than wrapping, so what
              // is left fits and every control keeps its own column. From md up
              // it stays the single flex row it has always been.
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

            {/* The number column carries the 1px timeline rule between rows. */}
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

            <PlaceIcon
              category={details?.category}
              // Dropped on a phone: as its own grid cell it stole a column from
              // a name that has ~150px, and the stop number already says where
              // the stop is in the route. The emoji is decorative anyway.
              className="max-md:hidden"
            />

            {/* Tapping the name looks at the place on the map: picking a stop
                in the panel and then hunting for it on the map was the gap. */}
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
              // The name is a button whose text is only 22px tall inside a 52px
              // row, so a finger has to land on the letters themselves — and the
              // whole point of the name being a button is to look at the place
              // on the map. On a phone the button therefore fills the row's
              // height (`self-stretch`) with the line centred in it, which makes
              // the target the full 52px. Truncation keeps it on one line, so
              // the taller button does not make the row taller.
              //
              // An earlier attempt used a pseudo-element stretched by `inset-y-0`;
              // that resolves against the *button's* box, not the row's, so it
              // widened the target by 4px sideways and left the height at 22px —
              // measured, and the comment claimed otherwise.
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

            {/* Always reachable on touch (no hover), revealed on hover on desktop. */}
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
