import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface MobileSectionProps {
  /** Stable suffix for the test ids of the header and the panel. */
  id: string;
  /** The group's title, as the desktop panel spells it. */
  title: string;
  /** What is already chosen inside, so a collapsed row still says something. */
  summary?: ReactNode;
  /** Secondary groups start folded on a phone; the ones the route needs start open. */
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * One filter group in the plan panel: a titled block on desktop, a collapsed row
 * on a phone.
 *
 * Measured at 390x844 before this: opening «Больше фильтров» dropped the tourist
 * into one long wall — participants, four amenity pairs, interests, avoid, result
 * type — and the only way to reach the field again was a long scroll inside a
 * 41px window. Folding each secondary group to its own row keeps the open list
 * short and keeps what is chosen visible in the row itself.
 *
 * Desktop is not conditional on JS: the plain title and the open content are the
 * default, and the phone-only variants hide them. A state flip can therefore
 * never change the desktop panel.
 */
export const MobileSection = ({
  id,
  title,
  summary,
  defaultOpen = false,
  children,
}: MobileSectionProps) => {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = `${useId()}-${id}`;

  return (
    <div className="flex flex-col gap-1.5">
      {/* Phone: a 36px row that says what is inside. Desktop keeps the group's
          own heading above the open body — the words the panel has always
          shown. (The inner contents carry no heading of their own; the row and
          this span are the only two places the group is named.) */}
      <span
        data-testid={`section-title-${id}`}
        className="text-meta text-muted-foreground max-md:hidden"
      >
        {title}
      </span>
      <button
        type="button"
        data-testid={`section-toggle-${id}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="hidden w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-left text-label transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 max-md:flex max-md:min-h-11"
      >
        <span className="min-w-0 truncate font-medium">{title}</span>
        {summary && (
          <span
            data-testid={`section-count-${id}`}
            className="ml-auto shrink-0 text-meta text-muted-foreground"
          >
            {summary}
          </span>
        )}
        <ChevronDown
          className={cn(
            'h-4 w-4 shrink-0 text-muted-foreground transition-transform',
            summary ? '' : 'ml-auto',
            open && 'rotate-180'
          )}
          aria-hidden="true"
        />
      </button>

      {/* Closed on a phone: hidden. Always open on desktop — the desktop panel
          must not depend on a JS flag that only a phone can flip. */}
      <div
        id={panelId}
        data-testid={`section-body-${id}`}
        className={cn('flex flex-col', !open && 'max-md:hidden')}
      >
        {children}
      </div>
    </div>
  );
};
