/** The full point catalogue (`GET /places`) for the «все точки» tab. */

import { ApiError, request } from './request';
import type { PlacesAnswer } from './types';

export class PlacesError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'PlacesError';
    this.status = status;
  }
}

export async function fetchPlaces(signal?: AbortSignal): Promise<PlacesAnswer> {
  let body: Partial<PlacesAnswer>;
  try {
    body = await request<Partial<PlacesAnswer>>('/places', {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw new PlacesError(error.message, error.status);
    }
    throw error;
  }

  if (!Array.isArray(body?.items)) {
    throw new PlacesError('agent answer has no places', null);
  }
  return {
    items: body.items,
    total: body.total ?? body.items.length,
    capped: body.capped ?? false,
  };
}
