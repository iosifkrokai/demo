/**
 * Where the route request got to (`GET /routes/progress/{id}`).
 *
 * The panel used to have two sentences for a request that takes half a minute,
 * because those were all the client could observe. The pipeline now reports its
 * own stages — but in **codes**, never in prose: the backend does not speak one
 * language, so localising them is the client's job (same rule as reason codes).
 *
 * A 404 is not an error to shout about: it means the server does not know this
 * id (a restart, a TTL, a second worker). The caller then falls back to what it
 * can observe itself, never to an invented stage.
 */

/** The stages the pipeline reports, in the order it reaches them. */
export type RouteStageCode =
  | 'interpreting_request'
  | 'searching_places'
  | 'selecting_candidates'
  | 'measuring_legs'
  | 'ordering_stops'
  | 'drawing_line'
  | 'checking_requirements'
  | 'done';

export interface RouteProgress {
  stage: RouteStageCode;
  done: boolean;
  failed: boolean;
  elapsedMs: number;
}

/**
 * An id for one attempt. `crypto.randomUUID` needs a secure context; the demo
 * also runs over plain http on a LAN, where it is missing — the fallback keeps
 * the id unique per attempt without pretending to be a UUID.
 */
export const newProgressId = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/** Same-origin by default; VITE_AGENT_URL only points at a remote agent. */
const AGENT_URL = (import.meta.env.VITE_AGENT_URL as string | undefined) ?? '';

const STAGE_CODES: ReadonlySet<string> = new Set<RouteStageCode>([
  'interpreting_request',
  'searching_places',
  'selecting_candidates',
  'measuring_legs',
  'ordering_stops',
  'drawing_line',
  'checking_requirements',
  'done',
]);

/** Unknown codes are dropped rather than shown: the client never invents a stage. */
export const asStageCode = (value: unknown): RouteStageCode | null =>
  typeof value === 'string' && STAGE_CODES.has(value)
    ? (value as RouteStageCode)
    : null;

/**
 * The current stage, or `null` when the server cannot say (unknown id, or a
 * network that never reached it). `null` is information too, and the caller has
 * a truthful sentence to show in that case.
 */
export const fetchRouteProgress = async (
  progressId: string,
  signal: AbortSignal
): Promise<RouteProgress | null> => {
  const response = await fetch(
    `${AGENT_URL}/routes/progress/${encodeURIComponent(progressId)}`,
    { signal }
  );
  if (response.status === 404) return null;
  if (!response.ok) return null;

  const body = (await response.json()) as Record<string, unknown>;
  const stage = asStageCode(body.stage);
  if (!stage) return null;

  return {
    stage,
    done: body.done === true,
    failed: body.failed === true,
    elapsedMs: typeof body.elapsed_ms === 'number' ? body.elapsed_ms : 0,
  };
};
