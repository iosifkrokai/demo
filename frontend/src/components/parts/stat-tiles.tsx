import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { minutesLabel, pointsLabel, stopsLabel } from '@/utils/plural';

/** A counted unit a stat tile can label itself with. */
export type StatUnit = 'points' | 'stops' | 'minutes';

const UNIT_LABELS: Record<StatUnit, (count: number) => string> = {
  points: pointsLabel,
  stops: stopsLabel,
  minutes: minutesLabel,
};

interface StatTileProps {
  /** The number that matters — 18–20px semibold (DESIGN.md). */
  value: ReactNode;
  /** 12px muted label, unit included ("км", "мин в пути"). */
  label?: string;
  /** The counted value behind `unit`, when the label is a counted noun. */
  count?: number;
  /** Which noun to inflect — takes precedence over `label`. */
  unit?: StatUnit;
  className?: string;
}

export const StatTile = ({
  value,
  label,
  count,
  unit,
  className,
}: StatTileProps) => {
  const resolvedLabel =
    unit != null && count != null ? UNIT_LABELS[unit](count) : label;

  return (
    <div
      className={cn(
        'rounded-xl border border-border bg-card px-2 py-2.5 text-center',
        'shadow-card',
        className
      )}
    >
      <div className="text-stat font-semibold leading-tight text-foreground">
        {value}
      </div>
      <div className="mt-0.5 text-meta text-muted-foreground">
        {resolvedLabel}
      </div>
    </div>
  );
};

/** DESIGN.md: three stat tiles in a row. */
export const StatTiles = ({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) => <div className={cn('grid grid-cols-3 gap-2', className)}>{children}</div>;
