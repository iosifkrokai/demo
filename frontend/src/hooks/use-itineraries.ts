/** Ready-made routes for the panel's «Готовые» tab. */

import { useQuery } from '@tanstack/react-query';

import { fetchItineraries } from '@/api/itineraries';
import type { ItineraryList } from '@/api/types';

export function useItineraries(options: { enabled?: boolean } = {}) {
  const query = useQuery<ItineraryList>({
    queryKey: ['itineraries'],
    queryFn: ({ signal }) => fetchItineraries(signal),
    staleTime: 30 * 60 * 1000,
    retry: 1,
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
