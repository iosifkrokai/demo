import { Link } from '@tanstack/react-router';
import { Loader2, MapPinned, Plus, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { PlaceMap } from '@/components/place-map/place-map';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/hooks/use-auth';
import { usePlaces } from '@/hooks/use-places';
import { useToggleVisited, useVisited } from '@/hooks/use-visited';

/** «Мои посещённые места». */

const fmtDate = (value: string | null): string | null => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
};

const where = (town: string | null, district: string | null): string =>
  [town, district].filter(Boolean).join(', ');

export function VisitedPage() {
  const { authenticated, isLoading } = useAuth();
  const visited = useVisited(authenticated);
  const places = usePlaces({ enabled: authenticated });
  const toggle = useToggleVisited();
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const visitedIds = useMemo(
    () => new Set(visited.items.map((item) => item.place_id)),
    [visited.items]
  );

  useEffect(() => {
    if (selectedId == null) return;
    document
      .querySelector(`[data-testid="visited-item-${selectedId}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length < 2) return [];
    return places.places
      .filter((place) => !visitedIds.has(place.place_id))
      .filter(
        (place) =>
          place.name.toLowerCase().includes(needle) ||
          (place.town ?? '').toLowerCase().includes(needle)
      )
      .slice(0, 30);
  }, [places.places, query, visitedIds]);

  if (isLoading) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div
          data-testid="visited-loading"
          className="flex items-center gap-2 text-meta text-muted-foreground"
        >
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          загружаю…
        </div>
      </main>
    );
  }

  if (!authenticated) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <div className="flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-border bg-card p-6 text-center shadow-card">
          <MapPinned
            className="size-8 text-muted-foreground"
            aria-hidden="true"
          />
          <h1 className="text-title font-semibold">Мои посещённые места</h1>
          <p className="text-meta text-muted-foreground">
            Здесь видны места, которые вы отметили. Чтобы вести этот список,
            нужно войти.
          </p>
          <Button asChild>
            <Link to="/login">Войти</Link>
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-4xl flex-col gap-4 bg-background p-4">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-title font-semibold">Мои посещённые</h1>
        <Button asChild size="sm" variant="ghost">
          <Link to="/$activeTab" params={{ activeTab: 'directions' }}>
            к карте
          </Link>
        </Button>
      </header>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <section className="flex flex-col gap-2" data-testid="visited-list">
            <div className="text-meta font-medium text-muted-foreground">
              отмечено: {visited.count}
            </div>

            {visited.items.length === 0 && (
              <p className="rounded-2xl border border-border bg-card px-3 py-2 text-meta text-muted-foreground">
                пока пусто — отметьте место на карте или найдите его ниже.
              </p>
            )}

            {visited.items.length > 0 && (
              <ul className="flex flex-col gap-2">
                {visited.items.map((place) => (
                  <li
                    key={place.place_id}
                    data-testid={`visited-item-${place.place_id}`}
                    data-selected={
                      selectedId === place.place_id ? 'true' : 'false'
                    }
                    className={`flex items-start justify-between gap-2 rounded-2xl border bg-card p-3 shadow-card ${
                      selectedId === place.place_id
                        ? 'border-primary ring-1 ring-primary/30'
                        : 'border-border'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(place.place_id)}
                      data-testid={`visited-select-${place.place_id}`}
                      className="min-w-0 flex-1 rounded-lg text-left transition-colors hover:bg-muted/60"
                    >
                      <div className="truncate text-body font-semibold">
                        {place.name}
                      </div>
                      <div className="mt-0.5 text-meta text-muted-foreground">
                        {[
                          place.category,
                          where(place.town, place.district),
                          fmtDate(place.visited_at),
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    </button>
                    <button
                      type="button"
                      data-testid={`visited-remove-${place.place_id}`}
                      disabled={toggle.isPending}
                      onClick={() =>
                        toggle.mutate({
                          placeId: place.place_id,
                          visited: true,
                        })
                      }
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                      убрать
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="flex flex-col gap-2">
            <label className="text-meta font-medium" htmlFor="visited-search">
              добавить место
            </label>
            <Input
              id="visited-search"
              data-testid="visited-search"
              placeholder="название или город"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />

            {places.isLoading && query.trim().length >= 2 && (
              <p className="text-meta text-muted-foreground">
                загружаю каталог…
              </p>
            )}

            {query.trim().length >= 2 &&
              results.length === 0 &&
              !places.isLoading && (
                <p className="text-meta text-muted-foreground">
                  ничего не нашлось
                </p>
              )}

            {results.length > 0 && (
              <ul className="flex flex-col gap-1.5">
                {results.map((place) => (
                  <li
                    key={place.place_id}
                    className="flex items-center justify-between gap-2 rounded-xl border border-border bg-card px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-meta font-medium">
                        {place.name}
                      </div>
                      <div className="text-meta text-muted-foreground">
                        {[place.category, where(place.town, place.district)]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      data-testid={`visited-add-${place.place_id}`}
                      disabled={toggle.isPending}
                      onClick={() =>
                        toggle.mutate({
                          placeId: place.place_id,
                          visited: false,
                        })
                      }
                    >
                      <Plus className="size-4" aria-hidden="true" />
                      отметить
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside className="w-full shrink-0 lg:sticky lg:top-4 lg:w-[22rem]">
          <PlaceMap
            places={visited.items}
            selectedId={selectedId}
            onSelect={setSelectedId}
            className="h-[20rem] lg:h-[24rem]"
          />
        </aside>
      </div>
    </main>
  );
}
