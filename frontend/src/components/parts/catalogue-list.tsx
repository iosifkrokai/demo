import { useTranslation } from 'react-i18next';
import { MapPin } from 'lucide-react';
import { useCommonStore } from '@/stores/common-store';
import type { AgentPoint } from '@/components/types';
import { PlaceIcon } from './place-icon';

interface Props {
  /** The places a catalogue answer returned — chosen, not ordered. */
  places: readonly AgentPoint[];
}

/** A catalogue answer: a browsable list of points, never a route. */
export function CatalogueList({ places }: Props) {
  const { t } = useTranslation();
  const focusOn = useCommonStore((s) => s.focusOn);

  return (
    <section
      data-testid="catalogue-list"
      className="flex flex-col gap-2"
      aria-label={t('sidebar.catalogue.title')}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-label font-semibold">
          {t('sidebar.catalogue.title')}
        </h2>
        <span className="text-meta text-muted-foreground">
          {t('sidebar.catalogue.count', { count: places.length })}
        </span>
      </div>
      <p className="text-meta text-muted-foreground">
        {t('sidebar.catalogue.intro')}
      </p>
      <ul className="flex flex-col gap-1.5">
        {places.map((place) => (
          <li key={place.id}>
            <button
              type="button"
              data-testid={`catalogue-place-${place.id}`}
              onClick={() => focusOn(place.lon, place.lat)}
              className="flex w-full items-start gap-2 rounded-2xl border border-border bg-card px-3 py-2.5 text-left shadow-card transition-colors hover:bg-muted"
            >
              <PlaceIcon category={place.category} className="mt-0.5" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-label font-medium text-foreground">
                  {place.name}
                </span>
                {(place.category || place.town) && (
                  <span className="block truncate text-meta text-muted-foreground">
                    {[place.category, place.town].filter(Boolean).join(' · ')}
                  </span>
                )}
                {place.blurb && (
                  <span className="mt-0.5 block text-meta text-muted-foreground">
                    {place.blurb}
                  </span>
                )}
              </span>
              <MapPin
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
