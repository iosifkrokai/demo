/** «удалить мои данные». */

import { Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { DeleteMyDataOutcome } from '@/hooks/use-client-routes';

export interface DeleteMyDataProps {
  onDelete: () => Promise<DeleteMyDataOutcome>;
}

type Phase = 'idle' | 'confirm' | 'deleting' | 'done' | 'failed';

export const DeleteMyData = ({ onDelete }: DeleteMyDataProps) => {
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState('');

  const remove = async () => {
    setPhase('deleting');
    setMessage('удаляю…');

    const outcome = await onDelete();

    if (outcome.ok) {
      setPhase('done');
      setMessage(
        'данные удалены — маршруты и предпочтения стёрты на сервере и здесь'
      );
      return;
    }
    setPhase('failed');
    setMessage(outcome.message);
  };

  return (
    <section
      data-testid="delete-my-data"
      className="rounded-2xl border border-border bg-card p-3 shadow-card"
      aria-label="удаление моих данных"
    >
      <div className="text-meta font-medium text-muted-foreground">
        мои данные
      </div>

      {phase === 'idle' && (
        <>
          <p className="mt-1 text-meta text-muted-foreground">
            хранятся анонимно: маршруты и предпочтения без имени и почты.
          </p>
          <button
            type="button"
            data-testid="delete-my-data-start"
            onClick={() => setPhase('confirm')}
            className="mt-2 inline-flex h-9 items-center gap-1.5 rounded-xl px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            удалить мои данные
          </button>
        </>
      )}

      {(phase === 'confirm' || phase === 'deleting') && (
        <div className="mt-2">
          <p className="text-meta text-muted-foreground">
            удалим маршруты и предпочтения на сервере и их локальные копии.
            Отменить нельзя.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="delete-my-data-confirm"
              onClick={() => void remove()}
              disabled={phase === 'deleting'}
              className="h-9 rounded-xl bg-primary px-3 text-meta font-semibold text-primary-foreground transition hover:brightness-[0.97] disabled:opacity-40"
            >
              удалить
            </button>
            <button
              type="button"
              data-testid="delete-my-data-cancel"
              onClick={() => setPhase('idle')}
              disabled={phase === 'deleting'}
              className="h-9 rounded-xl px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              отмена
            </button>
          </div>
        </div>
      )}

      <p
        data-testid="delete-my-data-status"
        role="status"
        aria-live="polite"
        className="mt-2 text-meta text-muted-foreground"
      >
        {message}
      </p>
    </section>
  );
};
