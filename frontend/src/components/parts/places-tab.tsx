import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, MapPin, Search } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useCommonStore } from '@/stores/common-store';
import type { Place } from '@/api/types';
import { PlaceIcon } from './place-icon';
import { POINT_FORMS, pluralCountRu } from '@/utils/plural';
import { Input } from '@/components/ui/input';

interface PlacesTabProps {
  places: readonly Place[];
  total: number;
  isLoading?: boolean;
  error?: unknown;
  onReload?: () => void;
}

/** Fold ё→е and lower-case, so «костёл»/«костел» match the same way the backend
 * does — the search is a browse aid, not a second, divergent matcher. */
const fold = (text: string): string =>
  text.trim().toLowerCase().replace(/ё/g, 'е');

/**
 * «Все точки» — the whole catalogue, browsable.
 *
 * The map already draws every point; this tab is the list beside it. It is
 * grouped by category and collapsible, because the dataset is ~2.5k rows and a
 * flat list is a wall. A row only flies the map to the point (`focusOn`) — the
 * card opens from the map marker itself, so the list and the map stay one screen
 * rather than two competing popups.
 */
export function PlacesTab({
  places,
  total,
  isLoading = false,
  error,
  onReload,
}: PlacesTabProps) {
  const { t } = useTranslation();
  const focusOn = useCommonStore((s) => s.focusOn);
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const groups = useMemo(() => groupByCategory(places), [places]);
  const matches = useMemo(() => {
    const q = fold(query);
    if (!q) return null;
    return places
      .filter((place) =>
        [place.name, place.town, place.district, place.category]
          .filter((v): v is string => Boolean(v))
          .some((value) => fold(value).includes(q))
      )
      .slice(0, 200);
  }, [places, query]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (isLoading) {
    return (
      <p
        className="flex items-center gap-2 text-meta text-muted-foreground"
        data-testid="places-loading"
        role="status"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t('places.loading')}
      </p>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-2" data-testid="places-error">
        <p className="text-meta text-muted-foreground" role="alert">
          {t('places.loadFailed')}
        </p>
        {onReload && (
          <button
            type="button"
            onClick={() => onReload()}
            className="self-start rounded-full border border-border px-3 py-1.5 text-meta transition-colors hover:bg-muted max-md:min-h-11 pointer-coarse:min-h-11"
          >
            {t('places.retry')}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="places-tab">
      <p className="text-meta text-muted-foreground">
        {t('places.intro', { count: pluralCountRu(total, POINT_FORMS) })}
      </p>

      <div className="relative">
        <Search
          className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('places.search')}
          aria-label={t('places.search')}
          className="pl-9"
        />
      </div>

      {matches !== null ? (
        matches.length === 0 ? (
          <p
            className="text-meta text-muted-foreground"
            data-testid="places-no-match"
          >
            {t('places.noMatch')}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5" data-testid="places-matches">
            {matches.map((place) => (
              <PlaceRow key={place.place_id} place={place} onFocus={focusOn} />
            ))}
          </ul>
        )
      ) : (
        <ul className="flex flex-col gap-1.5">
          {groups.map((group) => {
            const open = expanded.has(group.key);
            return (
              <li key={group.key}>
                <button
                  type="button"
                  data-testid={`places-group-${group.key}`}
                  aria-expanded={open}
                  onClick={() => toggle(group.key)}
                  className="flex w-full items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-meta transition-colors hover:bg-muted"
                >
                  <PlaceIcon category={group.category} />
                  <span className="font-medium text-foreground">
                    {group.label}
                  </span>
                  <span className="text-muted-foreground">
                    {group.items.length}
                  </span>
                  <span
                    className={cn(
                      'ml-auto text-muted-foreground transition-transform',
                      open && 'rotate-90'
                    )}
                    aria-hidden="true"
                  >
                    ›
                  </span>
                </button>
                {open && (
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {group.items.map((place) => (
                      <PlaceRow
                        key={place.place_id}
                        place={place}
                        onFocus={focusOn}
                      />
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface PlaceGroup {
  key: string;
  label: string;
  category: string | null;
  items: Place[];
}

/** Group by category, most populated first; a missing category is «другое». */
function groupByCategory(places: readonly Place[]): PlaceGroup[] {
  const byCategory = new Map<string, Place[]>();
  for (const place of places) {
    const key = place.category ?? 'other';
    const list = byCategory.get(key) ?? [];
    list.push(place);
    byCategory.set(key, list);
  }
  return [...byCategory.entries()]
    .map(([category, items]) => ({
      key: category,
      label: category,
      category,
      items,
    }))
    .sort((a, b) => b.items.length - a.items.length);
}

/** One row: the point's face, its name, and where it is. */
function PlaceRow({
  place,
  onFocus,
}: {
  place: Place;
  onFocus: (lng: number, lat: number) => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-testid={`focus-place-${place.place_id}`}
        onClick={() => onFocus(place.lon, place.lat)}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-meta transition-colors hover:bg-muted"
      >
        <PlaceIcon category={place.category} className="h-4 w-4" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-foreground">
            {place.name}
          </span>
          {place.town && (
            <span className="block truncate text-muted-foreground">
              {place.town}
            </span>
          )}
        </span>
        <MapPin
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
      </button>
    </li>
  );
}
