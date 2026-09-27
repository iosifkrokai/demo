/**
 * Secondary points beside a route (`POST /routes/services`).
 *
 * The question is «что есть по пути», and the answer is deliberately narrow:
 * the agent measures how far a café or a toilet sits from the line the tourist
 * is walking and how far along it that is. It does not build a detour, so the
 * payload carries `detour_confirmed: false` and no screen may turn that into a
 * walking time.
 *
 * Same-origin like every other agent call (the dev Vite proxy and the
 * production nginx both forward `/routes/*`). A failure here must not look like
 * «рядом ничего нет»: the caller shows «не удалось проверить» instead.
 */

import type { RouteLine, ServicesAlongAnswer } from './types';

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

export class ServicesError extends Error {
  readonly status: number | null;
  /** The agent's machine-readable reason, when it sent one. */
  readonly reason: string | null;

  constructor(message: string, status: number | null = null, reason: string | null = null) {
    super(message);
    this.name = 'ServicesError';
    this.status = status;
    this.reason = reason;
  }
}

const reasonFrom = (body: unknown): string | null => {
  if (!body || typeof body !== 'object') return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const reason = (detail as { reason?: unknown }).reason;
    if (typeof reason === 'string') return reason;
  }
  return null;
};

export interface FetchServicesOptions {
  /** Valhalla costing name, as everywhere else; the agent picks the gate. */
  profile?: string;
  /** Category codes from the taxonomy; only service ones are honoured. */
  categories?: string[];
  signal?: AbortSignal;
}

export async function fetchServicesAlong(
  shape: RouteLine,
  { profile = 'pedestrian', categories, signal }: FetchServicesOptions = {}
): Promise<ServicesAlongAnswer> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_URL}/routes/services`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shape,
        profile,
        ...(categories ? { categories } : {}),
      }),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw new ServicesError('агент недоступен — «что по пути» проверить нельзя');
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ServicesError(
      `агент ответил ошибкой ${response.status}`,
      response.status,
      reasonFrom(body)
    );
  }

  return (await response.json()) as ServicesAlongAnswer;
}
