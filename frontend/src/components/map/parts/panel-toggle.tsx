import { ChevronLeft, ChevronRight } from 'lucide-react';

import { cn } from '@/lib/utils';

/** The panel's handle, on the panel's own edge. */
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
        'group absolute top-1/2 z-20 hidden h-14 w-6 -translate-y-1/2 items-center justify-center md:flex',
        'rounded-r-md border border-l-0 border-border bg-card/95 text-muted-foreground shadow-card',
        'transition-colors hover:text-foreground pointer-coarse:h-20 pointer-coarse:w-11',
        className
      )}
    >
      <Icon
        className="h-4 w-4 pointer-coarse:h-5 pointer-coarse:w-5"
        aria-hidden="true"
      />
    </button>
  );
};
