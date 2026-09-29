/**
 * Ready-made routes (`GET /routes/itineraries`).
 *
 * One request, same-origin like every other agent call (the dev Vite proxy and
 * the production nginx both forward `/routes/*`). No model is involved on the
 * agent side, so this cannot be slow in the way a plan request can — and when
 * it does fail, the panel says so instead of showing an empty list, which would
 * read as «у нас нет готовых маршрутов».
 */

import type { ItineraryList } from './types';

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

export class ItinerariesError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'ItinerariesError';
    this.status = status;
  }
}

export async function fetchItineraries(
  signal?: AbortSignal
): Promise<ItineraryList> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_URL}/routes/itineraries`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (error) {
    // The request never reached the agent — a different failure from a refusal.
    throw new ItinerariesError(
      error instanceof Error ? error.message : 'network',
      null
    );
  }

  if (!response.ok) {
    throw new ItinerariesError(
      `agent answered ${response.status}`,
      response.status
    );
  }

  const body = (await response.json()) as Partial<ItineraryList>;
  if (!Array.isArray(body?.items)) {
    throw new ItinerariesError(
      'agent answer has no itineraries',
      response.status
    );
  }
  return { items: body.items, missing: body.missing ?? [] };
}
