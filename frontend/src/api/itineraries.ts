/** Ready-made routes (`GET /routes/itineraries`). */

import { ApiError, request } from './request';
import type { ItineraryList } from './types';

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
  let body: Partial<ItineraryList>;
  try {
    body = await request<Partial<ItineraryList>>('/routes/itineraries', {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ItinerariesError(error.message, error.status);
    }
    throw error;
  }

  if (!Array.isArray(body?.items)) {
    throw new ItinerariesError('agent answer has no itineraries', null);
  }
  return { items: body.items, missing: body.missing ?? [] };
}
