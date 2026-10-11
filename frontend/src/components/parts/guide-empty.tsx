import { MapPinned } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Nothing to walk yet. */
export const GuideEmpty = () => {
  const { t } = useTranslation();

  return (
    <div
      data-testid="guide-empty"
      className="rounded-2xl border border-border bg-card p-6 text-center shadow-card"
    >
      <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <MapPinned className="size-6" />
      </span>
      <div className="mt-3 text-body font-semibold">
        {t('guide.emptyTitle')}
      </div>
      <p className="mx-auto mt-1 max-w-[26ch] text-label text-muted-foreground">
        {t('guide.emptyBody')}
      </p>
    </div>
  );
};
