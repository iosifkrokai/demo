import { MapPin, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { describeAccountError } from '@/hooks/use-auth';
import {
  useAdminPlaces,
  useCreatePlace,
  useDeletePlace,
  useUpdatePlace,
} from '@/hooks/use-admin';
import type { AdminPlaceInput, Place } from '@/api/types';

import { AdminPlaceMap } from './admin-place-map';
import {
  fieldsOf,
  formatCoord,
  toPlacePatch,
  type EditableFields,
} from './place-fields';

/**
 * The places half of the admin panel (spec 005 §3).
 *
 * A list alone cannot answer «туда ли поставлена точка», so the panel is two
 * columns: the list, and a map of the row you picked. Selecting «изменить» makes
 * the pin draggable — dragging rewrites the coordinate fields, and «сохранить»
 * PATCHes them like any other field. Editing a place changes the same row the map,
 * the «все точки» tab and the planner read; there is no separate admin copy.
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
  const places = useAdminPlaces(enabled, query, category);
  const updatePlace = useUpdatePlace();
  const deletePlace = useDeletePlace();
  const createPlace = useCreatePlace();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<EditableFields | null>(null);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Place | null>(null);

  const editing = editingId != null && draft != null;

  const startEdit = (place: Place) => {
    setSelected(place);
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
        if (selected?.place_id === place.place_id) setSelected(null);
      },
      onError: (error) => toast.error(describeAccountError(error)),
    });
  };

  const moveMarker = (lat: number, lon: number) =>
    setDraft((value) =>
      value ? { ...value, lat: formatCoord(lat), lon: formatCoord(lon) } : value
    );

  // What the map should show: the draft while editing, the picked row otherwise.
  let map: {
    key: string;
    lat: number;
    lon: number;
    label: string;
    draggable: boolean;
  } | null = null;
  if (editing && draft && selected) {
    map = {
      key: `edit-${editingId}`,
      lat: finiteOr(draft.lat, selected.lat),
      lon: finiteOr(draft.lon, selected.lon),
      label: draft.name || selected.name,
      draggable: true,
    };
  } else if (selected) {
    map = {
      key: `view-${selected.place_id}`,
      lat: selected.lat,
      lon: selected.lon,
      label: selected.name,
      draggable: false,
    };
  }

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

        {places.isLoading && (
          <p className="text-meta text-muted-foreground">загружаю…</p>
        )}

        <ul className="flex flex-col gap-2">
          {places.items.map((place) => (
            <li
              key={place.place_id}
              data-testid={`admin-place-${place.place_id}`}
              className={`rounded-2xl border bg-card p-3 shadow-card ${
                selected?.place_id === place.place_id
                  ? 'border-primary'
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
                  <div className="min-w-0">
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
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid={`admin-place-show-${place.place_id}`}
                      onClick={() => setSelected(place)}
                    >
                      <MapPin className="size-4" aria-hidden="true" />
                      на карте
                    </Button>
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

        {!places.isLoading && places.items.length === 0 && (
          <p className="text-meta text-muted-foreground">ничего не найдено</p>
        )}
      </div>

      <aside className="w-full shrink-0 lg:sticky lg:top-4 lg:w-[22rem]">
        {map ? (
          <AdminPlaceMap
            placeKey={map.key}
            lat={map.lat}
            lon={map.lon}
            label={map.label}
            draggable={map.draggable}
            onMove={map.draggable ? moveMarker : undefined}
          />
        ) : (
          <div className="flex h-[20rem] items-center justify-center rounded-2xl border border-dashed border-border bg-card p-4 text-center text-meta text-muted-foreground">
            выберите место слева («на карте») — покажу точку; в режиме правки
            маркер можно перетащить.
          </div>
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
