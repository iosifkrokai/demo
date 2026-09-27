/**
 * The tourist's preferences, loaded at startup and saved as they change
 * (spec 003 §4): transport, time budget, party, interests, language and the
 * «мой обычный темп» per category, all prefilled from the server so the guide
 * opens on the tourist's own numbers.
 *
 * There is no «сохранить» button on purpose. An edit is written to localStorage
 * immediately and pushed to the server on a short debounce, so nothing is lost
 * if the page closes and nothing depends on the tourist remembering to confirm.
 *
 * Honest degradation: when the server cannot store (`503 storage_unavailable`,
 * or a network that never reached it) the hook keeps working on the local copy
 * and reports `savingUnavailable: true` — the UI says so plainly rather than
 * pretending the change reached the server.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
  getClientPreferences,
  isSavingUnavailable,
  putClientPreferences,
} from '@/api/client';
import type { ClientPreferences, ClientPreferencesPatch } from '@/api/types';

export const CLIENT_PREFERENCES_STORAGE_KEY = 'grodno-client-preferences';

/** Everything unstated: `null` is «не указано», never a defaulted value. */
export const EMPTY_PREFERENCES: ClientPreferences = {
  transport: null,
  time_budget_minutes: null,
  party_adults: null,
  party_children: null,
  interests: null,
  language: null,
  visit_minutes_by_category: null,
};

/** The local fallback copy — a reserve, not a second source of truth. */
export const loadLocalPreferences = (): ClientPreferences => {
  try {
    const raw = localStorage.getItem(CLIENT_PREFERENCES_STORAGE_KEY);
    if (!raw) return EMPTY_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<ClientPreferences>;
    return { ...EMPTY_PREFERENCES, ...parsed };
  } catch {
    // unreadable storage — start from "everything unstated"
    return EMPTY_PREFERENCES;
  }
};

export const saveLocalPreferences = (preferences: ClientPreferences): void => {
  try {
    localStorage.setItem(
      CLIENT_PREFERENCES_STORAGE_KEY,
      JSON.stringify(preferences)
    );
  } catch {
    // private mode / quota — the values stay in memory for this session
  }
};

/** Drop the local copy (used by «удалить мои данные», spec §5). */
export const clearLocalPreferences = (): void => {
  try {
    localStorage.removeItem(CLIENT_PREFERENCES_STORAGE_KEY);
  } catch {
    // storage unavailable: nothing to remove
  }
};

export interface UseClientPreferencesOptions {
  /** How long an edit waits before it is pushed; tests shorten it. */
  debounceMs?: number;
}

export interface ClientPreferencesApi {
  preferences: ClientPreferences;
  isLoading: boolean;
  /** `true` when the last write could not reach storage on the server. */
  savingUnavailable: boolean;
  /** Apply an edit: changes the UI at once, saves to the server after a pause. */
  update: (patch: ClientPreferencesPatch) => void;
  /** Send pending changes now instead of waiting for the debounce. */
  flush: () => Promise<void>;
}

export const useClientPreferences = (
  options: UseClientPreferencesOptions = {}
): ClientPreferencesApi => {
  const debounceMs = options.debounceMs ?? 500;

  const [preferences, setPreferences] =
    useState<ClientPreferences>(loadLocalPreferences);
  const [savingUnavailable, setSavingUnavailable] = useState(false);

  // The committed values, readable from callbacks without re-creating them.
  const preferencesRef = useRef(preferences);
  // Edits not yet accepted by the server.
  const pendingRef = useRef<ClientPreferencesPatch>({});
  // The tourist changed something: a late server answer must not clobber it.
  const dirtyRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const query = useQuery({
    queryKey: ['client', 'preferences'],
    queryFn: getClientPreferences,
    retry: false,
  });

  // Server is the source of truth at startup — unless the tourist already
  // edited, in which case their edit is the newer fact. The server is an
  // external system: adopting its answer into local state is the sync.
  /* eslint-disable react-hooks/set-state-in-effect -- server preferences are an external system, adopted into local state */
  useEffect(() => {
    if (!query.data || dirtyRef.current) return;
    preferencesRef.current = query.data;
    setPreferences(query.data);
    saveLocalPreferences(query.data);
    setSavingUnavailable(false);
  }, [query.data]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // A load that fails for lack of storage is the same honest state as a failed
  // save: the guide works locally and says saving is off. Derived, not stored.
  const loadUnavailable = query.isError && isSavingUnavailable(query.error);

  const flush = useCallback(async () => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    const sent: ClientPreferencesPatch = { ...pendingRef.current };
    if (Object.keys(sent).length === 0) return;

    try {
      const saved = await putClientPreferences(sent);
      // Keep anything the tourist changed while the request was in flight.
      pendingRef.current = Object.fromEntries(
        Object.entries(pendingRef.current).filter(
          ([key, value]) =>
            JSON.stringify(value) !==
            JSON.stringify(sent[key as keyof ClientPreferencesPatch])
        )
      ) as ClientPreferencesPatch;
      preferencesRef.current = saved;
      setPreferences(saved);
      saveLocalPreferences(saved);
      setSavingUnavailable(false);
    } catch (error) {
      if (isSavingUnavailable(error)) {
        setSavingUnavailable(true);
      }
      // The local copy already holds the change: nothing is lost, it is just
      // not on the server. A later edit will try again.
    }
  }, []);

  const update = useCallback(
    (patch: ClientPreferencesPatch) => {
      dirtyRef.current = true;
      pendingRef.current = { ...pendingRef.current, ...patch };

      const next = { ...preferencesRef.current, ...patch };
      preferencesRef.current = next;
      setPreferences(next);
      saveLocalPreferences(next);

      if (timerRef.current != null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        void flush();
      }, debounceMs);
    },
    [debounceMs, flush]
  );

  // No trailing save after unmount: the debounce is short and the local copy
  // already has the change.
  useEffect(
    () => () => {
      if (timerRef.current != null) clearTimeout(timerRef.current);
    },
    []
  );

  return {
    preferences,
    isLoading: query.isLoading,
    savingUnavailable: savingUnavailable || loadUnavailable,
    update,
    flush,
  };
};
