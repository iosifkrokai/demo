import { Route as RouteIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * The visible label of the planner entry point. It is a real word in the
 * product's language — the previous control was an icon-only button whose only
 * name was the English tooltip «Directions», which left a phone with no
 * readable way into the planner at all.
 */
export const PLANNER_ENTRY_LABEL = 'Планировать маршрут';

interface PlannerEntryProps {
  onClick: () => void;
  /** The panel is open — the button then reads as an expanded toggle. */
  open?: boolean;
  className?: string;
}

/**
 * The one entry point to the planner on top of the map. A labelled pill, not an
 * icon: the tourist should not have to guess what a picture does, and on a
 * phone this button is the whole product's front door. 44px tall so it is a
 * comfortable tap target everywhere (DESIGN.md "Touch").
 */
export const PlannerEntry = ({
  onClick,
  open = false,
  className,
}: PlannerEntryProps) => (
  <button
    type="button"
    onClick={onClick}
    aria-expanded={open}
    aria-label={open ? 'панель маршрута открыта' : PLANNER_ENTRY_LABEL}
    title={open ? 'панель маршрута открыта' : PLANNER_ENTRY_LABEL}
    data-testid="tab-directions-button"
    className={cn(
      'inline-flex h-11 min-h-11 items-center gap-2 rounded-full border border-border bg-card px-4',
      'text-label font-medium text-foreground shadow-card transition-colors hover:bg-muted',
      className
    )}
  >
    <RouteIcon className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
    <span>{PLANNER_ENTRY_LABEL}</span>
  </button>
);
