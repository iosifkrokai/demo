import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean;
  children: ReactNode;
}

/**
 * DESIGN.md chip: h-8, rounded-full, hairline border, quiet until it is picked —
 * then a solid fill. Used for the query hints and the time presets.
 */
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
      // `max-md` / `pointer-coarse` raise the 32px chip to the 44px minimum tap
      // target on a phone (DESIGN.md "Touch"); the desktop look is unchanged.
      'h-8 shrink-0 rounded-full border px-3 text-label font-medium transition-colors',
      'max-md:h-11 max-md:px-4 pointer-coarse:h-11',
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
