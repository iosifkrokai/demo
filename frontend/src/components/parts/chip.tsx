import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { DENSITY } from '@/components/mobile/density';
import { cn } from '@/lib/utils';

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean;
  children: ReactNode;
}

/** Chip: h-8, rounded-full, hairline border; a solid fill once picked. */
export const Chip = ({
  selected = false,
  className,
  children,
  ...props
}: ChipProps) => (
  <button
    type="button"
    aria-pressed={selected}
    className={cn(
      'h-8 shrink-0 rounded-full border px-3 text-label font-medium transition-colors pointer-coarse:h-11',
      DENSITY.chip,
      'disabled:pointer-events-none disabled:opacity-40',
      selected
        ? 'border-foreground bg-foreground text-background'
        : 'border-border bg-card text-foreground hover:bg-muted',
      className
    )}
    {...props}
  >
    {children}
  </button>
);
