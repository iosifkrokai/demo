/** The full point catalogue (`GET /places`) for the «все точки» tab. */

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
