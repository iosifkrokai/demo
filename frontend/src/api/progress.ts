/** Where the route request got to (`GET /routes/progress/{id}`). */

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

/** The route's current stage; `null` when the server cannot say (unknown or unreachable). */
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
