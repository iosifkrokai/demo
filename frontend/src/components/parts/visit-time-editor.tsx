import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Minus, Plus, RotateCcw } from 'lucide-react';

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { VISIT_STEP, clampVisit } from '@/utils/visit-time';

interface VisitTimeEditorProps {
  /** The dataset's estimate — «столько здесь обычно и оставляют». */
  estimate: number | null;
  /** The tourist's own minutes, or null while the estimate stands. */
  value: number | null;
  /** null on the reset button: back to the estimate. */
  onChange: (minutes: number | null) => void;
  /** Quiet inline variant for list rows. */
  compact?: boolean;
  className?: string;
}

/** Common visit lengths, so nobody taps «+» eight times for two hours. */
const PRESETS = [15, 30, 45, 60, 90, 120];

/**
 * «≈ 40 мин» with the tourist's own number one tap away.
 *
 * The estimate is a hint from the dataset, never a claim about this person's
 * visit: the UI says «≈», and the moment they pick their own time that number
 * decides every total that depends on visit length. Resetting hands the
 * estimate back.
 *
 * The controls live in a popover rather than unfolding inside the row: an
 * expanding inline group pushed the stop's name and the hint text around, so
 * the row moved under the finger that was trying to tap it.
 */
export const VisitTimeEditor = ({
  estimate,
  value,
  onChange,
  compact = false,
  className,
}: VisitTimeEditorProps) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const shown = value ?? estimate;
  const hasEstimate = estimate != null;

  if (!hasEstimate && value == null) return null;

  /** Keep the estimate authoritative when the number lands back on it. */
  const commit = (minutes: number) => {
    const next = clampVisit(minutes);
    if (next === estimate) onChange(null);
    else onChange(next);
  };

  const step = (delta: number) => commit((value ?? estimate ?? 0) + delta);

  const chipClass = cn(
    'inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5',
    'text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
    'data-[state=open]:border-ring data-[state=open]:text-foreground',
    compact && 'px-1.5'
  );

  const stepClass =
    'flex h-8 w-8 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground pointer-coarse:h-11 pointer-coarse:w-11';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={
            value == null
              ? t('sidebar.visit.openEstimate', { minutes: shown })
              : t('sidebar.visit.open', { minutes: shown })
          }
          onClick={(event) => event.stopPropagation()}
          className={cn(chipClass, className)}
          data-testid="visit-time-chip"
        >
          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
          <span>
            {value == null
              ? t('sidebar.visit.chipEstimate', { minutes: shown })
              : t('sidebar.visit.chipMine', { minutes: shown })}
          </span>
        </button>
      </PopoverTrigger>

      <PopoverContent
        // Anchored to the row that was tapped and kept on screen: with the
        // default bottom placement a row near the edge made the panel flip to
        // the middle of the screen, nowhere near the finger that opened it.
        side="top"
        align="center"
        sideOffset={8}
        collisionPadding={12}
        className="w-[min(15rem,calc(100vw-2rem))] p-2.5"
        data-testid="visit-time-menu"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-meta text-muted-foreground">
            {value == null
              ? t('sidebar.visit.estimate')
              : t('sidebar.visit.mine')}
          </span>
          {hasEstimate && (
            <button
              type="button"
              onClick={() => onChange(null)}
              disabled={value == null}
              title={t('sidebar.visit.resetHint', { minutes: estimate })}
              data-testid="visit-time-reset"
              className="flex items-center gap-1 rounded-full px-2 py-0.5 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 pointer-coarse:min-h-11"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              {t('sidebar.visit.reset')}
            </button>
          )}
        </div>

        {/* − [число] + : the number sits between the two keys that change it. */}
        <div className="mt-2 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => step(-VISIT_STEP)}
            aria-label={t('sidebar.visit.minus', { minutes: VISIT_STEP })}
            data-testid="visit-time-minus"
            className={stepClass}
          >
            <Minus className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <span
            className="text-body font-medium tabular-nums"
            data-testid="visit-time-value"
          >
            {shown} {t('sidebar.visit.unit')}
          </span>
          <button
            type="button"
            onClick={() => step(VISIT_STEP)}
            aria-label={t('sidebar.visit.plus', { minutes: VISIT_STEP })}
            data-testid="visit-time-plus"
            className={stepClass}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-2.5 grid grid-cols-3 gap-1.5">
          {PRESETS.map((minutes) => (
            <button
              key={minutes}
              type="button"
              onClick={() => commit(minutes)}
              data-testid={`visit-time-preset-${minutes}`}
              aria-pressed={shown === minutes}
              className={cn(
                'rounded-lg border border-border px-2 py-1 text-meta transition-colors hover:bg-muted pointer-coarse:min-h-11',
                shown === minutes
                  ? 'border-ring bg-muted text-foreground'
                  : 'text-muted-foreground'
              )}
            >
              {t('sidebar.visit.chipMine', { minutes })}
            </button>
          ))}
        </div>

        {value == null && hasEstimate && (
          <p className="mt-2 text-meta text-muted-foreground">
            {t('sidebar.visit.estimatedHint', { minutes: estimate })}
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
};
