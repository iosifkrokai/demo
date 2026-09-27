/**
 * What the panel may say while a route request is in flight.
 *
 * The UI never claims what it does not know, so the stages are exactly the ones
 * the client can observe from the outside:
 *
 *   requesting — the POST /routes/generate request is in flight (sent, no
 *                answer yet). Nothing is known about what the agent is doing
 *                inside that request, so nothing is claimed about it.
 *   drawing    — the plan has arrived and the app is asking for the line
 *                (Valhalla /route) that will be drawn on the map.
 *
 * There is deliberately no "проверяю требования" stage: the client cannot see
 * the agent's internal steps, and inventing them would be a lie.
 */
export type RouteStage = 'requesting' | 'drawing';

export const routeStageText = (stage: RouteStage): string =>
  stage === 'requesting'
    ? 'отправил запрос — жду план от агента'
    : 'план пришёл — рисую маршрут по дорогам';

/** After this many seconds the panel may say the wait is a long one. */
export const LONG_WAIT_SECONDS = 12;

/** Honest long-wait note: the request is still pending, nothing more. */
export const routeLongWaitText =
  'агент всё ещё строит — иногда это занимает до минуты';

/** «0 с», «12 с» — the elapsed time shown next to the stage. */
export const routeElapsedText = (seconds: number): string =>
  `${Math.max(0, Math.round(seconds))} с`;
