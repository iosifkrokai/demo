/**
 * «сохранить маршрут» — the one explicit action that saves (spec 003 §4).
 *
 * Nothing else in this feature saves a route: mounting this button saves
 * nothing, and a finished plan is not collected in the background. The tourist
 * clicks, optionally names the route, and hears the truth about where it went:
 * on the server, or — when storage is unavailable — only in this browser, said
 * in as many words. A failed save never shows «сохранён».
 */

import { Bookmark, Check, TriangleAlert } from 'lucide-react';
import { useState } from 'react';

import type { SaveRouteOutcome } from '@/hooks/use-client-routes';

export interface SaveRouteButtonProps {
  onSave: (name: string | null) => Promise<SaveRouteOutcome>;
  disabled?: boolean;
  /** Prefill the name field, e.g. with the tourist's own query. */
  defaultName?: string | null;
}

type Status = 'idle' | 'saving' | 'saved' | 'local' | 'error';

interface Feedback {
  status: Status;
  message: string;
}

const IDLE: Feedback = { status: 'idle', message: '' };

export const SaveRouteButton = ({
  onSave,
  disabled = false,
  defaultName = null,
}: SaveRouteButtonProps) => {
  const [name, setName] = useState(defaultName ?? '');
  const [feedback, setFeedback] = useState<Feedback>(IDLE);

  const save = async () => {
    setFeedback({ status: 'saving', message: 'сохраняю…' });

    const outcome = await onSave(name.trim() || null);

    if (outcome.ok) {
      setFeedback({ status: 'saved', message: 'маршрут сохранён на сервере' });
      return;
    }
    if (outcome.storage === 'local') {
      // `outcome.message` already says saving is unavailable; do not repeat it.
      setFeedback({ status: 'local', message: outcome.message });
      return;
    }
    setFeedback({ status: 'error', message: outcome.message });
  };

  const saving = feedback.status === 'saving';

  return (
    <div className="flex flex-col gap-2">
      <label className="flex flex-col gap-1">
        <span className="text-meta text-muted-foreground">
          название (необязательно)
        </span>
        <input
          data-testid="save-route-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="как назвать маршрут"
          disabled={disabled || saving}
          className="h-10 rounded-xl border border-border bg-background px-3 text-body outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
        />
      </label>

      <button
        type="button"
        data-testid="save-route-button"
        onClick={() => void save()}
        disabled={disabled || saving}
        className="flex h-11 items-center justify-center gap-2 rounded-xl bg-primary font-semibold text-primary-foreground transition hover:brightness-[0.97] active:scale-[0.99] disabled:opacity-40"
      >
        <Bookmark className="h-4 w-4" aria-hidden="true" />
        {saving ? 'сохраняю…' : 'сохранить маршрут'}
      </button>

      <p
        data-testid="save-route-status"
        role="status"
        aria-live="polite"
        className={
          feedback.status === 'saved'
            ? 'flex items-center gap-1.5 text-meta text-foreground'
            : 'flex items-center gap-1.5 text-meta text-muted-foreground'
        }
      >
        {feedback.status === 'saved' && (
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {(feedback.status === 'local' || feedback.status === 'error') && (
          <TriangleAlert className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {feedback.message}
      </p>
    </div>
  );
};
