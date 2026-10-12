/** The planning journal (`GET /routes/threads/:sessionId`).
 *
 * A thread is one client run: the first query and the refinements that followed
 * it, each a turn the server recorded. The client reads it back to replay a turn
 * instead of re-uploading the whole route it already holds.
 */

import { ApiError, request } from './request';

/** One stop as the measurement recorded it — the journal's own answer. */
export interface ThreadStop {
  id: number;
  name: string;
  category?: string | null;
  town?: string | null;
  lat: number;
  lon: number;
  visit_minutes?: number | null;
}

/** The code's measurement of a turn, whole; the line and the numbers ride along. */
export interface ThreadMeasurement {
  results?: ThreadStop[];
  status?: string | null;
  walk_minutes?: number;
  visit_minutes?: number;
  total_minutes?: number;
  fits_budget?: boolean;
  length_km?: number | null;
  shape?: { type?: string; coordinates?: number[][] } | null;
}

/** One turn: what was asked, what the agent proposed, what was measured. */
export interface ThreadTurn {
  revision: number;
  query: string;
  submission: Record<string, unknown>;
  measurement: ThreadMeasurement;
  created_at: string | null;
}

/** A whole thread, oldest turn first. */
export interface ThreadJournal {
  session_id: string;
  created_at: string | null;
  turns: ThreadTurn[];
}

/** A failed thread read; the store treats it as best-effort, never as a failed route. */
export class ThreadError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'ThreadError';
    this.status = status;
  }
}

export async function fetchThread(
  sessionId: string,
  signal?: AbortSignal
): Promise<ThreadJournal> {
  let body: Partial<ThreadJournal>;
  try {
    body = await request<Partial<ThreadJournal>>(
      `/routes/threads/${encodeURIComponent(sessionId)}`,
      { method: 'GET', headers: { Accept: 'application/json' }, signal }
    );
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ThreadError(error.message, error.status);
    }
    throw error;
  }

  if (!Array.isArray(body?.turns)) {
    throw new ThreadError('agent answer has no thread turns', null);
  }
  return {
    session_id: body.session_id ?? sessionId,
    created_at: body.created_at ?? null,
    turns: body.turns,
  };
}
