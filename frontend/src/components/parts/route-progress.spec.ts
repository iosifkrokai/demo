import { describe, it, expect } from 'vitest';

import {
  LONG_WAIT_SECONDS,
  routeElapsedSeconds,
  routeLongWaitText,
  routeStageText,
} from './route-progress';

/**
 * The waiting copy may only describe what the client can observe. There is no
 * "checking requirements" here: the client cannot see inside the agent's request,
 * and claiming it would be a lie to the user.
 */
describe('routeStageText', () => {
  it('says the request is sent and the plan is being waited for', () => {
    expect(routeStageText('requesting')).toBe(
      'отправил запрос — жду план от агента'
    );
  });

  it('says the plan arrived and the line is being drawn', () => {
    expect(routeStageText('drawing')).toBe(
      'план пришёл — рисую маршрут по дорогам'
    );
  });

  it('claims no stage the client cannot see', () => {
    const text = `${routeStageText('requesting')} ${routeStageText('drawing')}`;
    expect(text).not.toMatch(/проверя|анализ|дума|подбир|требован/i);
  });
});

describe('routeElapsedSeconds', () => {
  it('reads as seconds in Russian', () => {
    expect(routeElapsedSeconds(0)).toBe(0);
    expect(routeElapsedSeconds(12.4)).toBe(12);
  });

  it('never shows a negative counter', () => {
    expect(routeElapsedSeconds(-3)).toBe(0);
  });
});

describe('routeLongWaitText', () => {
  it('admits the wait is long without inventing a reason', () => {
    expect(LONG_WAIT_SECONDS).toBeGreaterThan(0);
    expect(routeLongWaitText).toMatch(/всё ещё/);
    expect(routeLongWaitText).not.toMatch(/проверя|анализ|требован/i);
  });
});
