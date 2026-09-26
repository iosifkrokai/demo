import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface StatTileProps {
  /** The number that matters — 18–20px semibold (DESIGN.md). */
  value: ReactNode;
  /** 12px muted label, unit included ("км", "мин в пути"). */
  label: string;
  className?: string;
}

export const StatTile = ({ value, label, className }: StatTileProps) => (
  <div
    className={cn(
      'rounded-xl border border-border bg-card px-2 py-2.5 text-center',
      'shadow-[0_1px_2px_rgba(0,0,0,0.06)]',
      className
    )}
  >
    <div className="text-[19px] font-semibold leading-tight text-foreground">
      {value}
    </div>
    <div className="mt-0.5 text-[12px] text-muted-foreground">{label}</div>
  </div>
);

/** DESIGN.md: three stat tiles in a row. */
export const StatTiles = ({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) => <div className={cn('grid grid-cols-3 gap-2', className)}>{children}</div>;
