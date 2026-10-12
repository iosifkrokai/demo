import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/** An inline confirmation for a destructive action: the trigger swaps to
    «(удалить?)» with «Удалить» / «Отменить» beside it. A native `window.confirm`
    blocks the whole page and cannot be styled, so nothing calls it any more. */
export interface InlineConfirmProps {
  /** The question shown once armed, e.g. «Удалить место «Старый замок»?». */
  question: string;
  /** Runs when the tourist presses «Удалить». */
  onConfirm: () => void;
  /** The idle trigger's contents — an icon, a label, or both. */
  children: ReactNode;
  /** The trigger's accessible name when it shows an icon alone. */
  label?: string;
  /** The confirm button's label; defaults to «Удалить». */
  confirmLabel?: string;
  /** A request is in flight: the confirm button waits. */
  busy?: boolean;
  /** The trigger is unavailable (e.g. another row's delete is pending). */
  disabled?: boolean;
  className?: string;
  'data-testid'?: string;
}

export function InlineConfirm({
  question,
  onConfirm,
  children,
  label,
  confirmLabel,
  busy = false,
  disabled = false,
  className,
  'data-testid': testId,
}: InlineConfirmProps) {
  const { t } = useTranslation();
  const [armed, setArmed] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  /** The confirm button takes focus, so the keyboard lands on the safe choice. */
  useEffect(() => {
    if (armed) confirmRef.current?.focus();
  }, [armed]);

  if (!armed) {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled}
        aria-label={label}
        onClick={() => setArmed(true)}
        data-testid={testId}
        className={cn(
          'text-muted-foreground hover:text-destructive',
          className
        )}
      >
        {children}
      </Button>
    );
  }

  return (
    <span
      role="group"
      aria-label={question}
      className="inline-flex flex-wrap items-center gap-2"
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          setArmed(false);
        }
      }}
    >
      <span className="text-meta text-muted-foreground">{question}</span>
      <Button
        ref={confirmRef}
        type="button"
        variant="destructive"
        size="sm"
        disabled={busy}
        onClick={onConfirm}
        data-testid={testId ? `${testId}-confirm` : undefined}
      >
        {confirmLabel ?? t('actions.delete')}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => setArmed(false)}
        data-testid={testId ? `${testId}-cancel` : undefined}
      >
        {t('actions.cancel')}
      </Button>
    </span>
  );
}
