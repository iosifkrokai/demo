/**
 * Ready-made routes for the panel's «Готовые» tab.
 *
 * The list is authored, not generated: it changes when someone edits
 * `backend/data/itineraries.json`, which is not something that happens while a
 * tourist is looking at the panel. So it is fetched once and kept — refetching
 * on every tab switch would only add waiting.
 *
 * The failure is surfaced as an error the tab prints; an empty list would be
 * indistinguishable from «маршрутов нет», which is a different, untrue story.
 */

import { useQuery } from '@tanstack/react-query';

import { fetchItineraries } from '@/api/itineraries';
import type { ItineraryList } from '@/api/types';

export function useItineraries(options: { enabled?: boolean } = {}) {
  const query = useQuery<ItineraryList>({
    queryKey: ['itineraries'],
    queryFn: ({ signal }) => fetchItineraries(signal),
    staleTime: 30 * 60 * 1000,
    retry: 1,
    // Nothing is fetched until the tab that shows them is actually opened.
    enabled: options.enabled ?? true,
  });

  return {
    itineraries: query.data?.items ?? [],
    missing: query.data?.missing ?? [],
    isLoading: query.isLoading,
    error: query.error,
    reload: query.refetch,
  };
}
