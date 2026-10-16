/** Measuring the route the user is holding, the moment they change it: every edit
 * goes to the server without the model, and a superseded answer is ignored. */

import { useCallback } from 'react';
import { toast } from 'sonner';

import {
  recalculateRoute,
  type RerouteRequest,
  type RerouteStop,
} from '@/api/reroute';
import i18n from '@/i18n';
import type { Waypoint } from '@/stores/directions-store';
import { ME_WAYPOINT_ID, useDirectionsStore } from '@/stores/directions-store';
import { asMapShape } from '@/utils/agent-shape';
import { storedMeCoords } from '@/utils/me-waypoint';
import { summaryOf } from '@/utils/route-summary';

import { setAgentRoute, useDirectionsQuery } from './use-directions-queries';

/** One debounce for the whole app: any edit within the window replaces the last. */
export const RECALCULATE_DEBOUNCE_MS = 300;

let controller: AbortController | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let seq = 0;

/** The stop as the server needs it: a catalogue id, or the point itself. */
const asStop = (waypoint: Waypoint): RerouteStop | null => {
  if (waypoint.placeId != null) {
    return { key: waypoint.id, place_id: waypoint.placeId };
  }
  const geo =
    waypoint.geocodeResults.find((g) => g.selected) ??
    waypoint.geocodeResults[0];
  const [lon, lat] = geo?.sourcelnglat ?? geo?.displaylnglat ?? [];
  if (lat === undefined || lon === undefined) return null;
  return {
    key: waypoint.id,
    lat,
    lon,
    name: waypoint.userInput || geo?.title,
  };
};

export const useRouteRecalculate = () => {
  const { refetch } = useDirectionsQuery();

  const run = useCallback(async () => {
    const state = useDirectionsStore.getState();
    const stops = state.waypoints
      .filter((waypoint) => waypoint.id !== ME_WAYPOINT_ID)
      .map(asStop)
      .filter((stop): stop is RerouteStop => stop !== null);
    if (stops.length < 2) return;

    controller?.abort();
    controller = new AbortController();
    const mine = ++seq;
    const body: RerouteRequest = {
      stops,
      profile: state.costing ?? undefined,
      origin: storedMeCoords(state.waypoints) ?? undefined,
      round_trip: state.roundTrip || undefined,
      time_budget_minutes:
        state.timeBudget >= 15 ? state.timeBudget : undefined,
      preserve_order: state.manualOrder || undefined,
    };
    state.setRecalculating(true);
    try {
      const answer = await recalculateRoute(body, controller.signal);
      if (mine !== seq) return;
      useDirectionsStore.getState().setCosting(answer.costing ?? null);
      useDirectionsStore.getState().applyReroute({
        keys: (answer.points ?? []).map((point) => point.key ?? ''),
        summary: summaryOf(answer),
      });
      setAgentRoute({
        shape: asMapShape(answer.shape),
        summary: {
          length_km: answer.summary?.length_km ?? null,
          time_seconds: answer.summary?.time_seconds ?? null,
        },
        costing: answer.costing ?? null,
      });
      void refetch();
    } catch (error) {
      if ((error as Error)?.name === 'AbortError') return;
      if (mine !== seq) return;
      useDirectionsStore.getState().setRecalculating(false);
      toast.error(i18n.t('toast.recalcFailedTitle'), {
        description: i18n.t('toast.recalcFailedBody'),
        position: 'bottom-center',
        closeButton: true,
      });
    }
  }, [refetch]);

  const recalculate = useCallback(
    (delayMs: number = RECALCULATE_DEBOUNCE_MS) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void run();
      }, delayMs);
    },
    [run]
  );

  const cancel = useCallback(() => {
    if (timer) clearTimeout(timer);
    timer = null;
    controller?.abort();
    controller = null;
    seq += 1;
    useDirectionsStore.getState().setRecalculating(false);
  }, []);

  return { recalculate, cancel };
};
