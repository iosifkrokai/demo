import { Pencil, Plus, Trash2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError } from '@/hooks/use-auth';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import {
  useAdminPlaces,
  useCreatePlace,
  useDeletePlace,
  useUpdatePlace,
} from '@/hooks/use-admin';
import type { AdminPlaceInput, Place } from '@/api/types';

import { PlaceMap } from '@/components/place-map/place-map';

import {
  fieldsOf,
  formatCoord,
  toPlacePatch,
  type EditableFields,
} from './place-fields';

/**
 * The places half of the admin panel (spec 005 §3).
 *
 * List and map are two windows on the same page, side by side and always both
 * visible: clicking a row points the map at it, clicking a pin highlights its
 * row. «изменить» makes that pin draggable — dragging rewrites the coordinate
 * fields, and «сохранить» PATCHes them like any other field.
 *
 * Editing a place changes the same row the map, the «все точки» tab and the
 * planner read; there is no separate admin copy.
 */

const fieldLabel = 'flex flex-col gap-1 text-meta font-medium';

/** A typed coordinate, or the place's own when the box is blank/garbage. */
const finiteOr = (raw: string, fallback: number): number => {
  const trimmed = raw.trim();
  if (trimmed === '') return fallback;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export function PlacesPanel({ enabled }: { enabled: boolean }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  // The inputs stay instant; only the debounced value reaches the query key, so
  // a burst of typing is one request rather than one per character.
  const debouncedQuery = useDebouncedValue(query);
  const debouncedCategory = useDebouncedValue(category);
  const places = useAdminPlaces(enabled, debouncedQuery, debouncedCategory);
  const updatePlace = useUpdatePlace();
  const deletePlace = useDeletePlace();
  const createPlace = useCreatePlace();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<EditableFields | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const editing = editingId != null && draft != null;
  const selected = places.items.find((p) => p.place_id === selectedId) ?? null;

  // A pin clicked on the map is off-screen in a long list more often than not.
  useEffect(() => {
    if (selectedId == null) return;
    document
      .querySelector(`[data-testid="admin-place-${selectedId}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  const startEdit = (place: Place) => {
    setSelectedId(place.place_id);
    setEditingId(place.place_id);
    setDraft(fieldsOf(place));
  };

  const stopEdit = () => {
    setEditingId(null);
    setDraft(null);
  };

  const save = (placeId: number) => {
    if (!draft) return;
    updatePlace.mutate(
      { placeId, patch: toPlacePatch(draft) },
      {
        onSuccess: () => stopEdit(),
        onError: (error) => toast.error(describeAccountError(error)),
      }
    );
  };

  const remove = (place: Place) => {
    if (!window.confirm(`Удалить место «${place.name}»?`)) return;
    deletePlace.mutate(place.place_id, {
      onSuccess: () => {
        if (selectedId === place.place_id) setSelectedId(null);
      },
      onError: (error) => toast.error(describeAccountError(error)),
    });
  };

  const moveMarker = (lat: number, lon: number) =>
    setDraft((value) =>
      value ? { ...value, lat: formatCoord(lat), lon: formatCoord(lon) } : value
    );

  const editLat = editing && draft ? finiteOr(draft.lat, 0) : undefined;
  const editLon = editing && draft ? finiteOr(draft.lon, 0) : undefined;

  return (
    <section
      className="flex flex-col gap-3 lg:flex-row lg:items-start"
      data-testid="admin-places"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            data-testid="admin-places-search"
            placeholder="поиск по названию или городу"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="max-w-xs"
          />
          <Input
            data-testid="admin-places-category"
            placeholder="категория"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="max-w-[10rem]"
          />
          <span className="text-meta text-muted-foreground">
            всего: {places.total}
          </span>
          <Button
            type="button"
            size="sm"
            onClick={() => setCreating((value) => !value)}
            data-testid="admin-place-new"
          >
            <Plus className="size-4" aria-hidden="true" />
            новое место
          </Button>
        </div>

        {creating && (
          <CreatePlaceForm
            busy={createPlace.isPending}
            onCancel={() => setCreating(false)}
            onSubmit={(input) =>
              createPlace.mutate(input, {
                onSuccess: () => setCreating(false),
                onError: (error) => toast.error(describeAccountError(error)),
              })
            }
          />
        )}

        {places.isLoading && !places.error && (
          <p className="text-meta text-muted-foreground">загружаю…</p>
        )}

        {places.error && (
          <div
            data-testid="admin-places-error"
            className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-destructive/40 bg-card px-3 py-2"
          >
            <span className="text-meta text-muted-foreground">
              {describeAccountError(places.error)}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="admin-places-retry"
              onClick={() => void places.refetch()}
            >
              повторить
            </Button>
          </div>
        )}

        <ul className="flex flex-col gap-2">
          {places.items.map((place) => (
            <li
              key={place.place_id}
              data-testid={`admin-place-${place.place_id}`}
              data-selected={selectedId === place.place_id ? 'true' : 'false'}
              className={`rounded-2xl border bg-card p-3 shadow-card ${
                selectedId === place.place_id
                  ? 'border-primary ring-1 ring-primary/30'
                  : 'border-border'
              }`}
            >
              {editingId === place.place_id && draft ? (
                <div className="flex flex-col gap-2">
                  <div className="grid grid-cols-2 gap-2">
                    <label className={fieldLabel}>
                      название
                      <Input
                        value={draft.name}
                        onChange={(e) =>
                          setDraft({ ...draft, name: e.target.value })
                        }
                        data-testid="admin-place-name"
                      />
                    </label>
                    <label className={fieldLabel}>
                      категория
                      <Input
                        value={draft.category}
                        onChange={(e) =>
                          setDraft({ ...draft, category: e.target.value })
                        }
                        data-testid="admin-place-category"
                      />
                    </label>
                    <label className={fieldLabel}>
                      город
                      <Input
                        value={draft.town}
                        onChange={(e) =>
                          setDraft({ ...draft, town: e.target.value })
                        }
                      />
                    </label>
                    <label className={fieldLabel}>
                      время осмотра, мин
                      <Input
                        type="number"
                        min={0}
                        value={draft.visitMinutes}
                        onChange={(e) =>
                          setDraft({ ...draft, visitMinutes: e.target.value })
                        }
                      />
                    </label>
                    <label className={fieldLabel}>
                      широта
                      <Input
                        type="number"
                        step="any"
                        value={draft.lat}
                        onChange={(e) =>
                          setDraft({ ...draft, lat: e.target.value })
                        }
                        data-testid="admin-place-lat"
                      />
                    </label>
                    <label className={fieldLabel}>
                      долгота
                      <Input
                        type="number"
                        step="any"
                        value={draft.lon}
                        onChange={(e) =>
                          setDraft({ ...draft, lon: e.target.value })
                        }
                        data-testid="admin-place-lon"
                      />
                    </label>
                  </div>
                  <label className={fieldLabel}>
                    описание
                    <textarea
                      rows={2}
                      value={draft.blurb}
                      onChange={(e) =>
                        setDraft({ ...draft, blurb: e.target.value })
                      }
                      data-testid="admin-place-blurb"
                      className="w-full rounded-xl border border-border bg-background px-3 py-2 text-meta outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                  </label>
                  <label className={fieldLabel}>
                    любопытный факт
                    <Input
                      value={draft.funFact}
                      onChange={(e) =>
                        setDraft({ ...draft, funFact: e.target.value })
                      }
                    />
                  </label>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      disabled={updatePlace.isPending}
                      onClick={() => save(place.place_id)}
                      data-testid="admin-place-save"
                    >
                      сохранить
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={stopEdit}
                    >
                      отмена
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  {/* The name is the list's half of the two-way link: clicking it
                      points the map at this place. A real button, so it is also
                      reachable by keyboard. */}
                  <button
                    type="button"
                    onClick={() => setSelectedId(place.place_id)}
                    data-testid={`admin-place-select-${place.place_id}`}
                    className="min-w-0 flex-1 rounded-lg text-left transition-colors hover:bg-muted/60"
                  >
                    <div className="truncate text-body font-semibold">
                      {place.name}
                    </div>
                    <div className="text-meta text-muted-foreground">
                      {[
                        place.category,
                        place.town,
                        place.visit_minutes != null
                          ? `${place.visit_minutes} мин`
                          : null,
                        `${place.lat.toFixed(5)}, ${place.lon.toFixed(5)}`,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                    {place.blurb && (
                      <div className="mt-0.5 line-clamp-2 text-meta text-muted-foreground">
                        {place.blurb}
                      </div>
                    )}
                  </button>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid={`admin-place-edit-${place.place_id}`}
                      onClick={() => startEdit(place)}
                    >
                      <Pencil className="size-4" aria-hidden="true" />
                      изменить
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-testid={`admin-place-delete-${place.place_id}`}
                      disabled={deletePlace.isPending}
                      onClick={() => remove(place)}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>

        {!places.isLoading && !places.error && places.items.length === 0 && (
          <p className="text-meta text-muted-foreground">ничего не найдено</p>
        )}
      </div>

      <aside className="w-full shrink-0 lg:sticky lg:top-4 lg:w-[22rem]">
        <PlaceMap
          places={places.items}
          selectedId={selectedId}
          editingId={editingId}
          editLat={editLat}
          editLon={editLon}
          onSelect={setSelectedId}
          onMove={editing ? moveMarker : undefined}
        />
        {selected && (
          <p
            data-testid="admin-selected-name"
            className="mt-1.5 truncate text-meta text-muted-foreground"
          >
            выбрано: {selected.name}
          </p>
        )}
      </aside>
    </section>
  );
}

function CreatePlaceForm({
  busy,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  onSubmit: (input: AdminPlaceInput) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [category, setCategory] = useState('');
  const [town, setTown] = useState('');

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    onSubmit({
      name: name.trim(),
      lat: Number(lat),
      lon: Number(lon),
      source_url: sourceUrl.trim(),
      category: category.trim() || null,
      town: town.trim() || null,
    });
  };

  return (
    <form
      onSubmit={submit}
      data-testid="admin-place-create"
      className="flex flex-col gap-2 rounded-2xl border border-dashed border-border bg-card p-3"
    >
      <div className="flex items-center justify-between">
        <span className="text-meta font-semibold">новое место</span>
        <Button type="button" size="icon-sm" variant="ghost" onClick={onCancel}>
          <X className="size-4" aria-hidden="true" />
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className={fieldLabel}>
          название
          <Input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className={fieldLabel}>
          категория
          <Input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </label>
        <label className={fieldLabel}>
          широта
          <Input
            required
            type="number"
            step="any"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
          />
        </label>
        <label className={fieldLabel}>
          долгота
          <Input
            required
            type="number"
            step="any"
            value={lon}
            onChange={(e) => setLon(e.target.value)}
          />
        </label>
        <label className={fieldLabel}>
          город
          <Input value={town} onChange={(e) => setTown(e.target.value)} />
        </label>
        <label className={fieldLabel}>
          ссылка-источник
          <Input
            required
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
          />
        </label>
      </div>
      <Button
        type="submit"
        size="sm"
        disabled={busy}
        data-testid="admin-place-create-submit"
      >
        создать
      </Button>
    </form>
  );
}
