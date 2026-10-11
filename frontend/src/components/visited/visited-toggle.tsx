/** «Отметить посещённым» — the one control that writes to the tourist's visited registry. */

import { Check, MapPinPlus } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { describeAccountError, useAuth } from '@/hooks/use-auth';
import { useToggleVisited, useVisitedIds } from '@/hooks/use-visited';

export interface VisitedToggleProps {
  /** The DB `places.id`; nothing renders when it is unknown. */
  placeId: number | null | undefined;
  className?: string;
}

export function VisitedToggle({ placeId, className }: VisitedToggleProps) {
  const { authenticated } = useAuth();
  const visitedIds = useVisitedIds(authenticated);
  const toggle = useToggleVisited();

  if (placeId == null) return null;

  if (!authenticated) {
    return (
      <p
        data-testid="visited-login-hint"
        className={`text-meta text-muted-foreground ${className ?? ''}`}
      >
        войдите, чтобы отмечать посещённые места
      </p>
    );
  }

  const visited = visitedIds.has(placeId);

  return (
    <Button
      type="button"
      variant={visited ? 'secondary' : 'outline'}
      size="sm"
      data-testid={`visited-toggle-${placeId}`}
      aria-pressed={visited}
      disabled={toggle.isPending}
      onClick={() =>
        toggle.mutate(
          { placeId, visited },
          { onError: (error) => toast.error(describeAccountError(error)) }
        )
      }
      className={className}
    >
      {visited ? (
        <Check className="size-4" aria-hidden="true" />
      ) : (
        <MapPinPlus className="size-4" aria-hidden="true" />
      )}
      {visited ? 'посещено' : 'отметить посещённым'}
    </Button>
  );
}
