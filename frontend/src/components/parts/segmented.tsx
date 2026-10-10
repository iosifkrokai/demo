import { useRef } from 'react';
import type { KeyboardEvent } from 'react';
import type { LucideIcon } from 'lucide-react';
import { DENSITY } from '@/components/mobile/density';
import { cn } from '@/lib/utils';

export interface SegmentedItem<T extends string> {
  value: T;
  label: string;
  /** Shorter text for the pill when four items share a 380px column («Планирование» → «План»). */
  short?: string;
  icon?: LucideIcon;
}

interface SegmentedProps<T extends string> {
  items: ReadonlyArray<SegmentedItem<T>>;
  value: T;
  onChange: (value: T) => void;
  /** Accessible name of the group («режим», «на чём»). */
  label: string;
  /** Icon above the label. */
  stacked?: boolean;
  disabled?: boolean;
  className?: string;
  testId?: (value: T) => string | undefined;
}

/** DESIGN.md segmented control: grey track, the active item is a raised white pill. */
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
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const select = (index: number) => {
    const item = items[index];
    if (!item) return;
    onChange(item.value);
    refs.current[index]?.focus();
  };

  const onKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number
  ) => {
    const count = items.length;
    if (count === 0) return;
    const move = (delta: number) => select((index + delta + count) % count);
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault();
        move(1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault();
        move(-1);
        break;
      case 'Home':
        event.preventDefault();
        select(0);
        break;
      case 'End':
        event.preventDefault();
        select(count - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        'flex rounded-full bg-muted p-1',
        DENSITY.segmented,
        className
      )}
    >
      {items.map((item, index) => {
        const Icon = item.icon;
        const active = item.value === value;
        return (
          <button
            key={item.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            disabled={disabled}
            data-testid={testId?.(item.value)}
            aria-label={item.short ? item.label : undefined}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            title={item.label}
            className={cn(
              'flex min-w-0 flex-1 items-center justify-center rounded-full transition-colors',
              stacked
                ? 'min-h-11 flex-col gap-0.5 px-1 py-1.5 text-badge'
                : cn(
                    'h-8 gap-1 px-2 text-label pointer-coarse:h-11',
                    DENSITY.segmentedItem
                  ),
              'disabled:pointer-events-none disabled:opacity-50',
              active
                ? 'bg-card font-semibold text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {Icon && (
              <Icon
                className="h-4 w-4 shrink-0 max-md:hidden"
                aria-hidden="true"
              />
            )}
            <span className="truncate">{item.short ?? item.label}</span>
          </button>
        );
      })}
    </div>
  );
}
