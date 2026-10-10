/** What the panel may say while a route request is in flight. */
export type RouteStage = 'requesting' | 'drawing';

export const routeStageText = (stage: RouteStage): string =>
  stage === 'requesting'
    ? 'отправил запрос — жду план от агента'
    : 'план пришёл — рисую маршрут по дорогам';

/** After this many seconds the panel may say the wait is a long one. */
export const LONG_WAIT_SECONDS = 12;

/** Map the server's pipeline stage code to its i18n key. */
export const routeServerStageKey = (stage: string | null): string | null =>
  stage ? `sidebar.progress.${stage}` : null;

/** Honest long-wait note: the request is still pending, nothing more. */
export const routeLongWaitText =
  'агент всё ещё строит — иногда это занимает до минуты';

/** «0 с», «12 с» — the elapsed time shown next to the stage. */
/** Elapsed seconds as the progress line shows them: whole, never negative. */
export const routeElapsedSeconds = (seconds: number): number =>
  Math.max(0, Math.round(seconds));
