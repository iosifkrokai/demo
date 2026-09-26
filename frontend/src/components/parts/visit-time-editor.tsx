import { useState } from 'react';
import { Clock, Minus, Plus, RotateCcw } from 'lucide-react';

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

/**
 * «≈ 40 мин» with the tourist's own number behind one tap.
 *
 * The estimate is a hint from the dataset, never a claim about this person's
 * visit: the UI says «≈», and the moment they pick their own time that number
 * decides every total that depends on visit length. Resetting hands the
 * estimate back.
 */
export const VisitTimeEditor = ({
  estimate,
  value,
  onChange,
  compact = false,
  className,
}: VisitTimeEditorProps) => {
  const [open, setOpen] = useState(false);
  const shown = value ?? estimate;
  const hasEstimate = estimate != null;

  if (!hasEstimate && value == null) return null;

  const step = (delta: number) => {
    const from = value ?? estimate ?? 0;
    const next = clampVisit(from + delta);
    if (next === estimate) onChange(null);
    else onChange(next);
  };

  const buttonClass = cn(
    'inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5',
    'text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
    compact && 'px-1.5'
  );

  return (
    <span
      className={cn('inline-flex flex-wrap items-center gap-1.5', className)}
      role="group"
      aria-label="время осмотра"
    >
      {/* Open: − [число] + ↻ — the number is the chip itself, so it is not
          repeated next to it. */}
      {open && (
        <button
          type="button"
          onClick={() => step(-VISIT_STEP)}
          aria-label={`убавить время осмотра на ${VISIT_STEP} минут`}
          data-testid="visit-time-minus"
          className="flex h-6 w-6 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Minus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      )}

      <button
        type="button"
        aria-expanded={open}
        aria-label={
          value == null
            ? `время осмотра: примерно ${shown} минут, изменить`
            : `время осмотра: ${shown} минут, изменить`
        }
        onClick={() => setOpen((v) => !v)}
        className={buttonClass}
        data-testid="visit-time-chip"
      >
        {!open && <Clock className="h-3.5 w-3.5" aria-hidden="true" />}
        {value == null ? `≈ ${shown} мин` : `${shown} мин`}
      </button>

      {open && (
        <>
          <button
            type="button"
            onClick={() => step(VISIT_STEP)}
            aria-label={`прибавить время осмотра на ${VISIT_STEP} минут`}
            data-testid="visit-time-plus"
            className="flex h-6 w-6 items-center justify-center rounded-full border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          {hasEstimate && (
            <button
              type="button"
              onClick={() => onChange(null)}
              disabled={value == null}
              aria-label="вернуть примерное время"
              title={`вернуть примерно ${estimate} мин`}
              data-testid="visit-time-reset"
              className="flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          {value == null && hasEstimate && (
            <span className="text-meta text-muted-foreground">
              обычно здесь оставляют ≈ {estimate} мин
            </span>
          )}
        </>
      )}
    </span>
  );
};
