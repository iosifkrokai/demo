import { describe, it, expect } from 'vitest';

import { LONG_WAIT_SECONDS, routeElapsedSeconds } from './route-progress';

describe('routeElapsedSeconds', () => {
  it('rounds to whole seconds', () => {
    expect(routeElapsedSeconds(0)).toBe(0);
    expect(routeElapsedSeconds(12.4)).toBe(12);
  });

  it('never shows a negative counter', () => {
    expect(routeElapsedSeconds(-3)).toBe(0);
  });
});

describe('LONG_WAIT_SECONDS', () => {
  it('waits before calling a request slow', () => {
    expect(LONG_WAIT_SECONDS).toBeGreaterThan(0);
  });
});
