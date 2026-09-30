import { CircleCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * Shown instead of the next stop once the walk is over — the panel keeps its
 * shape so the last stop does not make everything above it jump.
 */
export const GuideRouteDone = ({ total }: { total: number }) => {
  const { t } = useTranslation();

  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-card">
      <div className="flex items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <CircleCheck className="h-5 w-5" />
        </span>
        <div>
          <div className="text-title font-semibold leading-tight">
            {t('guide.routeDoneTitle')}
          </div>
          <div className="mt-0.5 text-meta text-muted-foreground">
            {t('guide.routeDoneBody', { total })}
          </div>
        </div>
      </div>
    </div>
  );
};
