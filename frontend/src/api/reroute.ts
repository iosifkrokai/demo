/** The route the user is holding, measured over again (`POST /routes/reroute`). No
 * model runs here: the client sends the stops it has and gets the answer back. */

import type { AgentRouteResponse } from '@/components/types';
import { request } from './request';

/** One stop as the client has it: a place it found, a point it placed, or both. */
export interface RerouteStop {
  /** The client's own name for the stop; the answer echoes it in walking order. */
  key: string;
  place_id?: number;
  lat?: number;
  lon?: number;
  name?: string;
}

export interface RerouteRequest {
  stops: RerouteStop[];
  profile?: string;
  /** Where the tourist is standing, when they pinned it as the route's start. */
  origin?: { lat: number; lon: number };
  round_trip?: boolean;
  time_budget_minutes?: number;
  /** True while the stops carry the user's own order: measure it, do not re-order it. */
  preserve_order?: boolean;
}

export async function recalculateRoute(
  body: RerouteRequest,
  signal?: AbortSignal
): Promise<AgentRouteResponse> {
  return request<AgentRouteResponse>('/routes/reroute', {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  });
}
