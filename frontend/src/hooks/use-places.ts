/**
 * The full point catalogue for the panel's «Все точки» tab.
 *
 * The list is the raw dataset: it changes when someone re-seeds the database,
 * not while a tourist looks at the panel. So it is fetched once and kept —
 * refetching on every tab switch would only add waiting.
 *
 * The same hook is called from the panel (for the list) and from the map (for
 * the markers): TanStack Query deduplicates the two calls into one request on
 * the shared `['places']` key, so nothing is fetched twice.
 */

import { useQuery } from '@tanstack/react-query';

import { fetchPlaces } from '@/api/places';
import type { PlacesAnswer } from '@/api/types';

export function usePlaces(options: { enabled?: boolean } = {}) {
  const query = useQuery<PlacesAnswer>({
    queryKey: ['places'],
    queryFn: ({ signal }) => fetchPlaces(signal),
    staleTime: 30 * 60 * 1000,
    retry: 1,
    // Nothing is fetched until the tab that shows them is actually opened.
    enabled: options.enabled ?? true,
  });

  return {
    places: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    capped: query.data?.capped ?? false,
    isLoading: query.isLoading,
    error: query.error,
    reload: query.refetch,
  };
}
