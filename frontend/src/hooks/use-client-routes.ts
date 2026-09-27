/**
 * Saved routes for the anonymous client (spec 003 §4).
 *
 * Three rules from the spec drive this hook:
 *
 *  - **Saving is the tourist's action, never a side effect.** Nothing here
 *    saves on its own; `saveRoute` runs only when a button calls it. Silently
 *    collecting routes makes the list useless.
 *  - **A route that was saved is saved somewhere, and the UI is told where.**
 *    It goes to the server, and on `503 storage_unavailable` (or no network) it
 *    stays as a local copy and `saveRoute` returns `ok: false` with
 *    `storageUnavailable: true` — never a success.
 *  - **`plan` is opaque and passed through.** It is what the tourist saw when
 *    they saved; restoring re-applies exactly that, it does not rebuild a route.
 *
 * The list deliberately exposes only the light summary fields the list endpoint
 * promises — never a plan, never geometry. A local-only copy has no
 * `stop_count`/`distance_m`/`duration_min` to show, and says `null` rather than
 * inventing a number.
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
  ClientApiError,
  createClientRoute,
  deleteClient,
  deleteClientRoute,
  getClientRoute,
  isSavingUnavailable,
  listClientRoutes,
  renameClientRoute,
} from '@/api/client';
import type { ClientApiErrorCode, SavedRouteListItem } from '@/api/types';

import { clearLocalPreferences } from './use-client-preferences';

export const LOCAL_ROUTES_STORAGE_KEY = 'grodno-client-routes';

/** A route the server never took: this browser's own fallback copy. */
export interface LocalRouteCopy {
  id: string;
  name: string | null;
  query: string;
  created_at: string;
  plan: unknown;
  visit_overrides: Record<string, number> | null;
}

export const loadLocalRoutes = (): LocalRouteCopy[] => {
  try {
    const raw = localStorage.getItem(LOCAL_ROUTES_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (row): row is LocalRouteCopy =>
        !!row &&
        typeof row === 'object' &&
        typeof (row as { id?: unknown }).id === 'string'
    );
  } catch {
    // unreadable storage — start with no local copies
    return [];
  }
};

export const saveLocalRoutes = (routes: LocalRouteCopy[]): void => {
  try {
    if (routes.length === 0) {
      // No copies left: the key goes, so «удалить мои данные» really leaves
      // nothing behind in this browser.
      localStorage.removeItem(LOCAL_ROUTES_STORAGE_KEY);
      return;
    }
    localStorage.setItem(LOCAL_ROUTES_STORAGE_KEY, JSON.stringify(routes));
  } catch {
    // private mode / quota — the copies stay in memory for this session
  }
};

export const clearLocalRoutes = (): void => {
  try {
    localStorage.removeItem(LOCAL_ROUTES_STORAGE_KEY);
  } catch {
    // storage unavailable: nothing to remove
  }
};

/** A name the tourist can act on, keyed off the agent's machine code. */
export const describeClientError = (error: unknown): string => {
  if (error instanceof ClientApiError) {
    const byCode: Partial<Record<ClientApiErrorCode, string>> = {
      storage_unavailable: 'сервер не сохраняет данные — попробуйте позже',
      network_unavailable: 'нет связи с сервером — попробуйте позже',
      too_many_routes: 'слишком много сохранённых маршрутов — удалите ненужные',
      route_not_found: 'маршрут не найден на сервере',
      invalid_client_id: 'сервер отклонил идентификатор — обновите страницу',
    };
    return byCode[error.code] ?? 'не получилось — попробуйте ещё раз';
  }
  return 'не получилось — попробуйте ещё раз';
};

export interface SaveRouteInput {
  query: string;
  plan: unknown;
  name?: string | null;
  visit_overrides?: Record<string, number> | null;
}

export type SaveRouteOutcome =
  | { ok: true; storage: 'server'; id: string }
  | {
      ok: false;
      storage: 'local';
      storageUnavailable: true;
      localId: string;
      message: string;
    }
  | {
      ok: false;
      storage: 'none';
      storageUnavailable: false;
      message: string;
    };

export interface RestoredRoute {
  id: string;
  name: string | null;
  query: string;
  plan: unknown;
  visitOverrides: Record<string, number> | null;
  source: 'server' | 'local';
}

export type RestoreRouteOutcome =
  | { ok: true; route: RestoredRoute }
  | { ok: false; storageUnavailable: boolean; message: string };

export type RoutesActionResult =
  | { ok: true }
  | { ok: false; storageUnavailable: boolean; message: string };

export type DeleteMyDataOutcome =
  | { ok: true; message: string }
  | {
      ok: false;
      storageUnavailable: boolean;
      serverDeleted: false;
      message: string;
    };

export interface ClientRoutesApi {
  routes: SavedRouteListItem[];
  isLoading: boolean;
  savingUnavailable: boolean;
  saveRoute: (input: SaveRouteInput) => Promise<SaveRouteOutcome>;
  restoreRoute: (id: string) => Promise<RestoreRouteOutcome>;
  renameRoute: (id: string, name: string | null) => Promise<RoutesActionResult>;
  deleteRoute: (id: string) => Promise<RoutesActionResult>;
  deleteMyData: () => Promise<DeleteMyDataOutcome>;
}

const localRouteId = (): string => {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === 'function') return `local-${c.randomUUID()}`;
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
};

const toRestored = (
  copy: LocalRouteCopy,
  source: 'server' | 'local'
): RestoredRoute => ({
  id: copy.id,
  name: copy.name,
  query: copy.query,
  plan: copy.plan,
  visitOverrides: copy.visit_overrides,
  source,
});

const byNewest = (a: SavedRouteListItem, b: SavedRouteListItem): number =>
  (b.created_at ?? '').localeCompare(a.created_at ?? '');

export const useClientRoutes = (): ClientRoutesApi => {
  const [localRoutes, setLocalRoutes] =
    useState<LocalRouteCopy[]>(loadLocalRoutes);
  const localRef = useRef(localRoutes);
  const [savingUnavailable, setSavingUnavailable] = useState(false);

  const {
    data: serverRows,
    isError,
    error,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['client', 'routes'],
    queryFn: () => listClientRoutes(),
    retry: false,
  });

  const serverList = useMemo(() => serverRows ?? [], [serverRows]);

  const routes = useMemo<SavedRouteListItem[]>(() => {
    const serverIds = new Set(serverList.map((route) => route.id));
    const localOnly = localRoutes
      .filter((copy) => !serverIds.has(copy.id))
      .map<SavedRouteListItem>((copy) => ({
        id: copy.id,
        name: copy.name,
        query: copy.query,
        created_at: copy.created_at,
        // The list endpoint never gave us these for a local copy, so they stay
        // unstated instead of guessed.
        stop_count: null,
        distance_m: null,
        duration_min: null,
        local_only: true,
      }));

    return [
      ...serverList.map<SavedRouteListItem>((route) => ({
        ...route,
        local_only: false,
      })),
      ...localOnly,
    ].sort(byNewest);
  }, [serverList, localRoutes]);

  const setLocals = useCallback((next: LocalRouteCopy[]) => {
    localRef.current = next;
    setLocalRoutes(next);
    saveLocalRoutes(next);
  }, []);

  const saveRoute = useCallback(
    async (input: SaveRouteInput): Promise<SaveRouteOutcome> => {
      try {
        const created = await createClientRoute({
          query: input.query,
          plan: input.plan,
          name: input.name ?? null,
          visit_overrides: input.visit_overrides ?? null,
        });

        // The server now owns this route: drop any local fallback for the same
        // query so the list does not show it twice.
        setLocals(
          localRef.current.filter((copy) => copy.query !== input.query)
        );
        await refetch();
        setSavingUnavailable(false);
        return { ok: true, storage: 'server', id: created.id };
      } catch (error) {
        if (isSavingUnavailable(error)) {
          const copy: LocalRouteCopy = {
            id: localRouteId(),
            name: input.name ?? null,
            query: input.query,
            created_at: new Date().toISOString(),
            plan: input.plan,
            visit_overrides: input.visit_overrides ?? null,
          };
          setLocals([copy, ...localRef.current]);
          setSavingUnavailable(true);
          return {
            ok: false,
            storage: 'local',
            storageUnavailable: true,
            localId: copy.id,
            message:
              'сохранение недоступно — маршрут останется только в этом браузере',
          };
        }

        return {
          ok: false,
          storage: 'none',
          storageUnavailable: false,
          message: describeClientError(error),
        };
      }
    },
    [refetch, setLocals]
  );

  const restoreRoute = useCallback(
    async (id: string): Promise<RestoreRouteOutcome> => {
      const local = localRef.current.find((copy) => copy.id === id);
      const onServer = serverList.some((route) => route.id === id);

      if (local && !onServer) {
        return { ok: true, route: toRestored(local, 'local') };
      }

      try {
        const full = await getClientRoute(id);
        return {
          ok: true,
          route: {
            id: full.id,
            name: full.name,
            query: full.query,
            plan: full.plan,
            visitOverrides: full.visit_overrides,
            source: 'server',
          },
        };
      } catch (error) {
        // The server no longer has it but this browser does: hand over the
        // local copy rather than pretend the route is gone.
        if (
          error instanceof ClientApiError &&
          error.code === 'route_not_found' &&
          local
        ) {
          return { ok: true, route: toRestored(local, 'local') };
        }
        return {
          ok: false,
          storageUnavailable: isSavingUnavailable(error),
          message: describeClientError(error),
        };
      }
    },
    [serverList]
  );

  const renameRoute = useCallback(
    async (id: string, name: string | null): Promise<RoutesActionResult> => {
      const local = localRef.current.find((copy) => copy.id === id);
      const onServer = serverList.some((route) => route.id === id);

      if (local && !onServer) {
        setLocals(
          localRef.current.map((copy) =>
            copy.id === id ? { ...copy, name } : copy
          )
        );
        return { ok: true };
      }

      try {
        await renameClientRoute(id, name);
        await refetch();
        setSavingUnavailable(false);
        return { ok: true };
      } catch (error) {
        if (isSavingUnavailable(error)) setSavingUnavailable(true);
        return {
          ok: false,
          storageUnavailable: isSavingUnavailable(error),
          message: describeClientError(error),
        };
      }
    },
    [refetch, serverList, setLocals]
  );

  const deleteRoute = useCallback(
    async (id: string): Promise<RoutesActionResult> => {
      // The local copy goes regardless: the tourist asked for it gone.
      setLocals(localRef.current.filter((copy) => copy.id !== id));

      const onServer = serverList.some((route) => route.id === id);
      if (!onServer) return { ok: true };

      try {
        await deleteClientRoute(id);
        await refetch();
        setSavingUnavailable(false);
        return { ok: true };
      } catch (error) {
        if (isSavingUnavailable(error)) setSavingUnavailable(true);
        return {
          ok: false,
          storageUnavailable: isSavingUnavailable(error),
          message: describeClientError(error),
        };
      }
    },
    [refetch, serverList, setLocals]
  );

  const deleteMyData = useCallback(async (): Promise<DeleteMyDataOutcome> => {
    try {
      await deleteClient();
      // The local copies are deleted in the same action, as the UI promises.
      setLocals([]);
      clearLocalPreferences();
      await refetch();
      setSavingUnavailable(false);
      return { ok: true, message: 'данные удалены' };
    } catch (error) {
      // The tourist's intent is to erase their data; the local copies go even
      // when the server cannot confirm. What stays unknown is said plainly.
      setLocals([]);
      clearLocalPreferences();
      if (isSavingUnavailable(error)) {
        setSavingUnavailable(true);
        return {
          ok: false,
          storageUnavailable: true,
          serverDeleted: false,
          message:
            'сервер недоступен — данные на сервере могли остаться; локальные копии удалены',
        };
      }
      return {
        ok: false,
        storageUnavailable: false,
        serverDeleted: false,
        message:
          'не удалось подтвердить удаление на сервере; локальные копии удалены',
      };
    }
  }, [refetch, setLocals]);

  const listUnavailable = isError && isSavingUnavailable(error);

  return {
    routes,
    isLoading,
    savingUnavailable: savingUnavailable || listUnavailable,
    saveRoute,
    restoreRoute,
    renameRoute,
    deleteRoute,
    deleteMyData,
  };
};
