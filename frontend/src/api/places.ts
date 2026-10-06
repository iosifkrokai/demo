/**
 * The full point catalogue (`GET /places`) — every point in the dataset, for the
 * «все точки» tab.
 *
 * One request, same-origin like every other agent call (the dev Vite proxy and
 * the production nginx both forward `/routes/*` and `/places`). No model is
 * involved on the agent side, so this cannot be slow in the way a plan request
 * can — and when it does fail, the tab says so instead of showing an empty list,
 * which would read as «точек нет», which is a different, untrue story.
 */

import type { PlacesAnswer } from './types';

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

export class PlacesError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'PlacesError';
    this.status = status;
  }
}

export async function fetchPlaces(signal?: AbortSignal): Promise<PlacesAnswer> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_URL}/places`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (error) {
    // The request never reached the agent — a different failure from a refusal.
    throw new PlacesError(
      error instanceof Error ? error.message : 'network',
      null
    );
  }

  if (!response.ok) {
    throw new PlacesError(`agent answered ${response.status}`, response.status);
  }

  const body = (await response.json()) as Partial<PlacesAnswer>;
  if (!Array.isArray(body?.items)) {
    throw new PlacesError('agent answer has no places', response.status);
  }
  return {
    items: body.items,
    total: body.total ?? body.items.length,
    capped: body.capped ?? false,
  };
}
