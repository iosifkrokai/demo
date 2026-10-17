/** Secondary points beside a route (`POST /routes/services`). */

import type { RouteLine, ServicesAlongAnswer } from './types';
import { ApiError, request } from './request';

export class ServicesError extends Error {
  readonly status: number | null;
  /** The agent's machine-readable reason, when it sent one. */
  readonly reason: string | null;

  constructor(
    message: string,
    status: number | null = null,
    reason: string | null = null
  ) {
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
  try {
    return await request<ServicesAlongAnswer>('/routes/services', {
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
    if (error instanceof ApiError) {
      throw new ServicesError(
        error.status === null
          ? 'агент недоступен — «что по пути» проверить нельзя'
          : error.message,
        error.status,
        reasonFrom(error.body)
      );
    }
    throw error;
  }
}
