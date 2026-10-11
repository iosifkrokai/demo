/** The full point catalogue for the panel's «Все точки» tab. */

import { useQuery } from '@tanstack/react-query';

import { fetchPlaces } from '@/api/places';
import type { PlacesAnswer } from '@/api/types';

export function usePlaces(options: { enabled?: boolean } = {}) {
  const query = useQuery<PlacesAnswer>({
    queryKey: ['places'],
    queryFn: ({ signal }) => fetchPlaces(signal),
    staleTime: 30 * 60 * 1000,
    retry: 1,
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
