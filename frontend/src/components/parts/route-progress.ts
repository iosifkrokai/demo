/** The synthetic stages the panel shows while the answer is on its way. */
export type RouteStage = 'requesting' | 'drawing';

/** After this many seconds the panel may say the wait is a long one. */
export const LONG_WAIT_SECONDS = 12;

/** Map the server's pipeline stage code to its i18n key. */
export const routeServerStageKey = (stage: string | null): string | null =>
  stage ? `sidebar.progress.${stage}` : null;

/** Elapsed seconds as the progress line shows them: whole, never negative. */
export const routeElapsedSeconds = (seconds: number): number =>
  Math.max(0, Math.round(seconds));
