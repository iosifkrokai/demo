/**
 * The saved-routes list (spec 003 §4).
 *
 * It renders summaries and nothing else. The list endpoint deliberately leaves
 * the plan and its geometry behind, and this component has no access to them:
 * it shows name, query, stop count, distance, duration and whether the copy is
 * on the server or only in this browser. A value the server did not send stays
 * unstated — no `0 км`, no invented stop count.
 */

import { Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { SavedRouteListItem } from '@/api/types';
import { fmtDist, fmtMin, fmtStops } from '@/components/parts/guide-format';

export interface SavedRoutesListProps {
  routes: SavedRouteListItem[];
  isLoading?: boolean;
  /** `true` when the server cannot store: shown plainly, not hidden. */
  storageUnavailable?: boolean;
  onRestore: (id: string) => void;
  onRename?: (id: string, name: string | null) => void;
  onDelete: (id: string) => void;
}

const titleOf = (route: SavedRouteListItem): string =>
  route.name?.trim() || route.query.trim() || 'маршрут без названия';

/** Only the facts the server gave us; every missing one is simply absent. */
const detailsOf = (route: SavedRouteListItem): string => {
  const parts: string[] = [];
  if (route.stop_count != null && route.stop_count > 0) {
    parts.push(fmtStops(route.stop_count));
  }
  if (route.distance_m != null) parts.push(fmtDist(route.distance_m));
  if (route.duration_min != null) parts.push(fmtMin(route.duration_min));
  return parts.join(' · ');
};

const dateOf = (createdAt: string | null): string | null => {
  if (!createdAt) return null;
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
};

export const SavedRoutesList = ({
  routes,
  isLoading = false,
  storageUnavailable = false,
  onRestore,
  onRename,
  onDelete,
}: SavedRoutesListProps) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const startRename = (route: SavedRouteListItem) => {
    setEditingId(route.id);
    setDraft(route.name ?? '');
  };

  const submitRename = (id: string) => {
    onRename?.(id, draft.trim() || null);
    setEditingId(null);
  };

  return (
    <section
      data-testid="saved-routes-list"
      className="flex flex-col gap-2"
      aria-label="сохранённые маршруты"
    >
      <div className="text-meta font-medium text-muted-foreground">
        сохранённые маршруты
      </div>

      {storageUnavailable && (
        <p
          data-testid="saved-routes-unavailable"
          className="rounded-xl border border-border bg-card px-3 py-2 text-meta text-muted-foreground"
        >
          сохранение недоступно — список ниже не синхронизируется с сервером.
        </p>
      )}

      {isLoading && routes.length === 0 && (
        <p className="text-meta text-muted-foreground">загружаю…</p>
      )}

      {!isLoading && routes.length === 0 && (
        <p
          data-testid="saved-routes-empty"
          className="text-meta text-muted-foreground"
        >
          пока ничего не сохранено — сохраните маршрут, и он появится здесь.
        </p>
      )}

      {routes.length > 0 && (
        <ul className="flex flex-col gap-2">
          {routes.map((route) => {
            const details = detailsOf(route);
            const created = dateOf(route.created_at);
            return (
              <li
                key={route.id}
                data-testid={`saved-route-${route.id}`}
                className="rounded-2xl border border-border bg-card p-3 shadow-card"
              >
                <div className="min-w-0">
                  <div className="truncate text-body font-semibold">
                    {titleOf(route)}
                  </div>

                  {(details || created) && (
                    <div
                      data-testid={`saved-route-meta-${route.id}`}
                      className="mt-0.5 text-meta text-muted-foreground"
                    >
                      {[details, created].filter(Boolean).join(' · ')}
                    </div>
                  )}

                  {route.local_only && (
                    <div
                      data-testid={`saved-route-local-${route.id}`}
                      className="mt-1 text-meta text-muted-foreground"
                    >
                      только в этом браузере — на сервере не сохранён
                    </div>
                  )}
                </div>

                {editingId === route.id ? (
                  <form
                    className="mt-2 flex items-center gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      submitRename(route.id);
                    }}
                  >
                    <input
                      data-testid={`saved-route-rename-input-${route.id}`}
                      aria-label="новое название"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder="как назвать маршрут"
                      className="h-9 min-w-0 flex-1 rounded-xl border border-border bg-background px-3 text-body outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                    <button
                      type="submit"
                      data-testid={`saved-route-rename-save-${route.id}`}
                      className="h-9 shrink-0 rounded-xl bg-primary px-3 text-meta font-semibold text-primary-foreground transition hover:brightness-[0.97]"
                    >
                      сохранить
                    </button>
                    <button
                      type="button"
                      data-testid={`saved-route-rename-cancel-${route.id}`}
                      onClick={() => setEditingId(null)}
                      className="h-9 shrink-0 rounded-xl px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      отмена
                    </button>
                  </form>
                ) : (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      data-testid={`saved-route-open-${route.id}`}
                      onClick={() => onRestore(route.id)}
                      className="h-9 rounded-xl bg-primary px-3 text-meta font-semibold text-primary-foreground transition hover:brightness-[0.97]"
                    >
                      открыть
                    </button>
                    {onRename && (
                      <button
                        type="button"
                        data-testid={`saved-route-rename-${route.id}`}
                        onClick={() => startRename(route)}
                        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-border px-3 text-meta transition-colors hover:bg-muted"
                      >
                        <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                        переименовать
                      </button>
                    )}
                    <button
                      type="button"
                      data-testid={`saved-route-delete-${route.id}`}
                      onClick={() => onDelete(route.id)}
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl px-3 text-meta text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      удалить
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
