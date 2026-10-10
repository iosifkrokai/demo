import { useEffect, useMemo, useRef, useState } from 'react';

import { fetchServicesAlong, ServicesError } from '@/api/services';
import type { ServiceAlong } from '@/api/types';
import type { ParsedDirectionsGeometry } from '@/components/types';

export type ServicesState = 'idle' | 'loading' | 'ready' | 'unavailable';

export interface ServicesAlongResult {
  items: ServiceAlong[];
  state: ServicesState;
  /** What the answer measured — shown next to the list, never as a detour. */
  measured: string | null;
  maxOffLineM: number | null;
  /** The agent's reason code when the answer was empty on purpose. */
  reason: string | null;
  capped: boolean;
}

const EMPTY: ServicesAlongResult = {
  items: [],
  state: 'idle',
  measured: null,
  maxOffLineM: null,
  reason: null,
  capped: false,
};

/** The GeoJSON line the tourist is walking, from the geometry already on screen. */
export const lineFromGeometry = (
  data: ParsedDirectionsGeometry | null | undefined
): { type: 'LineString'; coordinates: [number, number][] } | null => {
  const points = data?.decodedGeometry;
  if (!points || points.length < 2) return null;
  return {
    type: 'LineString',
    coordinates: points.map(
      ([lat, lon]) => [lon ?? 0, lat ?? 0] as [number, number]
    ),
  };
};

/** Secondary points beside the route: cafés, toilets, hotels along the way. */
export const useServicesAlong = (
  data: ParsedDirectionsGeometry | null | undefined,
  { enabled, profile = 'pedestrian' }: { enabled: boolean; profile?: string }
): ServicesAlongResult => {
  const shape = useMemo(() => lineFromGeometry(data), [data]);
  const key = useMemo(() => (shape ? JSON.stringify(shape) : null), [shape]);
  const shapeRef = useRef(shape);
  const [result, setResult] = useState<ServicesAlongResult>(EMPTY);

  useEffect(() => {
    shapeRef.current = shape;
  }, [shape]);

  useEffect(() => {
    const shapeNow = shapeRef.current;
    if (!enabled || !key || !shapeNow) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResult(EMPTY);
      return;
    }
    const controller = new AbortController();
    setResult((prev) => ({ ...prev, state: 'loading' }));

    fetchServicesAlong(shapeNow, { profile, signal: controller.signal })
      .then((answer) => {
        setResult({
          items: answer.items,
          state: 'ready',
          measured: answer.measured,
          maxOffLineM: answer.max_off_line_m,
          reason: answer.reason ?? null,
          capped: answer.capped,
        });
      })
      .catch((error: unknown) => {
        if ((error as Error)?.name === 'AbortError') return;
        setResult({
          items: [],
          state: 'unavailable',
          measured: null,
          maxOffLineM: null,
          reason: error instanceof ServicesError ? error.reason : null,
          capped: false,
        });
      });

    return () => controller.abort();
  }, [enabled, key, profile]);

  return result;
};
