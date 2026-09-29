import { ChevronLeft, ChevronRight } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * The panel's handle, on the panel's own edge.
 *
 * It used to be a labelled pill floating over the map («Подобрать маршрут»),
 * which competed with the map's own controls in the same corner and read as one
 * more button among them. The panel already has an edge — the one the tourist
 * drags to resize — so opening and closing it belongs there: a quiet chevron on
 * that line, in the middle of the left edge, pointing the way the panel moves.
 *
 * It is a toggle, so it is present in both states (unlike the pill, which
 * vanished once the panel was open): the same control opens and closes. That is
 * also why the header's separate ✕ is gone — one control, one meaning.
 *
 * Its `className` comes from the caller, which places it on the panel's own edge
 * and clamps that position so the handle stays on screen where the panel is
 * wider than the viewport (a phone); otherwise the only control would sit
 * off-screen exactly when it is needed.
 */
export interface PanelToggleProps {
  open: boolean;
  onToggle: () => void;
  label: string;
  className?: string;
}

export const PanelToggle = ({
  open,
  onToggle,
  label,
  className,
}: PanelToggleProps) => {
  const Icon = open ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      data-testid="panel-toggle"
      aria-expanded={open}
      aria-label={label}
      title={label}
      onClick={onToggle}
      className={cn(
        'group absolute top-1/2 z-20 flex h-14 w-6 -translate-y-1/2 items-center justify-center',
        'rounded-r-md border border-l-0 border-border bg-card/95 text-muted-foreground shadow-card',
        'transition-colors hover:text-foreground pointer-coarse:h-16 pointer-coarse:w-8',
        className
      )}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  );
};
