import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SegmentedItem<T extends string> {
  value: T;
  label: string;
  icon?: LucideIcon;
}

interface SegmentedProps<T extends string> {
  items: ReadonlyArray<SegmentedItem<T>>;
  value: T;
  onChange: (value: T) => void;
  /** Accessible name of the group («режим», «на чём»). */
  label: string;
  /**
   * Icon above the label. Four transports have to fit a 360px column, and the
   * labels ("велосипед") do not survive a horizontal row next to an icon.
   */
  stacked?: boolean;
  disabled?: boolean;
  className?: string;
  testId?: (value: T) => string | undefined;
}

/**
 * DESIGN.md segmented control: grey track, the active item is a raised white
 * pill. Single-select, so it is a radiogroup rather than a row of toggles.
 */
export function Segmented<T extends string>({
  items,
  value,
  onChange,
  label,
  stacked = false,
  disabled = false,
  className,
  testId,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn('flex rounded-full bg-muted p-1', className)}
    >
      {items.map((item) => {
        const Icon = item.icon;
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            data-testid={testId?.(item.value)}
            onClick={() => onChange(item.value)}
            title={item.label}
            className={cn(
              'flex min-w-0 flex-1 items-center justify-center rounded-full transition-colors',
              stacked
                ? 'flex-col gap-0.5 px-1 py-1.5 text-[11px]'
                : 'h-8 gap-1 px-2 text-[13px]',
              'disabled:pointer-events-none disabled:opacity-50',
              active
                ? 'bg-card font-semibold text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {Icon && <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />}
            <span className="truncate">{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}
