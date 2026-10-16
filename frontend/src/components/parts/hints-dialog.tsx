import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import { Chip } from '@/components/parts/chip';
import type { HintChip, HintGroup } from '@/components/parts/hint-chips';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface HintsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: HintGroup[];
  onPick: (hint: HintChip) => void;
}

/** The whole row of starters at once, grouped by transport instead of scrolled past. */
export const HintsDialog = ({
  open,
  onOpenChange,
  groups,
  onPick,
}: HintsDialogProps) => {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="hints-dialog">
        <DialogHeader>
          <DialogTitle>{t('ask.hintsTitle')}</DialogTitle>
          <DialogClose
            aria-label={t('ask.hintsClose')}
            data-testid="hints-dialog-close"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </DialogClose>
        </DialogHeader>
        <DialogDescription className="sr-only">
          {t('ask.hintsIntro')}
        </DialogDescription>
        <div className="slim-scroll flex min-h-0 flex-col gap-3 overflow-y-auto">
          {groups.map((group) => (
            <div key={group.key} className="flex flex-col gap-1.5">
              {group.label && (
                <span className="text-meta text-muted-foreground">
                  {group.label}
                </span>
              )}
              <div className="flex flex-wrap gap-1.5">
                {group.chips.map((hint) => (
                  <Chip
                    key={hint.id}
                    className="h-auto max-md:h-auto pointer-coarse:max-md:h-auto w-full py-2 text-left"
                    onClick={() => onPick(hint)}
                    data-testid={`dialog-hint-${hint.id}`}
                  >
                    {hint.text}
                  </Chip>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
};
