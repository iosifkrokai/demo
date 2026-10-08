/**
 * The signed-in tourist's «посещённые места» (spec 005 §3).
 *
 * This is the durable «я здесь был» registry, not walk progress: the guide's
 * per-walk ticks still live in localStorage (spec 003 §4). Marking is the
 * tourist's explicit action, and it is idempotent on the server, so a double tap
 * cannot double-count.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';

import {
  listVisited,
  markVisited,
  markVisitedBulk,
  unmarkVisited,
} from '@/api/account';
import type { VisitedPlace } from '@/api/types';

export const VISITED_QUERY_KEY = ['visited'] as const;

export interface VisitedApi {
  items: VisitedPlace[];
  count: number;
  isLoading: boolean;
  error: unknown;
}

/** `enabled` is usually `auth.authenticated` — nobody fetches another's list. */
export function useVisited(enabled: boolean): VisitedApi {
  const query = useQuery({
    queryKey: VISITED_QUERY_KEY,
    queryFn: listVisited,
    enabled,
    staleTime: 60 * 1000,
    retry: false,
  });

  return {
    items: query.data?.items ?? [],
    count: query.data?.count ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}

/** The set of visited place ids — one lookup for every toggle in the UI. */
export function useVisitedIds(enabled: boolean): Set<number> {
  const { items } = useVisited(enabled);
  return useMemo(() => new Set(items.map((item) => item.place_id)), [items]);
}

export interface ToggleVisitedInput {
  placeId: number;
  /** Current state: the mutation flips it. */
  visited: boolean;
}

/**
 * Flip one place's visited mark. No optimistic cache surgery here: the mark is a
 * two-state fact, and refetching the list is cheap — a wrong optimistic guess
 * about a place the server refused would be worse than a short spinner.
 */
export function useToggleVisited() {
  const client = useQueryClient();
  return useMutation<unknown, unknown, ToggleVisitedInput>({
    mutationFn: ({ placeId, visited }) =>
      visited ? unmarkVisited(placeId) : markVisited(placeId),
    onSettled: () => client.invalidateQueries({ queryKey: VISITED_QUERY_KEY }),
  });
}

/** Mark a whole set at once (e.g. every stop of a walked route). */
export function useMarkVisitedBulk() {
  const client = useQueryClient();
  return useMutation<{ marked: number[] }, unknown, number[]>({
    mutationFn: (placeIds) => markVisitedBulk(placeIds),
    onSettled: () => client.invalidateQueries({ queryKey: VISITED_QUERY_KEY }),
  });
}
