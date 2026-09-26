import { describe, it, expect, beforeEach } from 'vitest';

import {
  VISIT_MAX,
  VISIT_MIN,
  clampVisit,
  loadVisitOverrides,
  saveVisitOverrides,
  visitMinutesFor,
} from './visit-time';

beforeEach(() => localStorage.clear());

describe('clampVisit', () => {
  it('keeps a sensible visit inside the allowed range', () => {
    expect(clampVisit(40)).toBe(40);
    expect(clampVisit(1)).toBe(VISIT_MIN);
    expect(clampVisit(10_000)).toBe(VISIT_MAX);
  });

  it('rounds to whole minutes', () => {
    expect(clampVisit(42.4)).toBe(42);
  });
});

describe('visit overrides', () => {
  it('reads back what was saved for the same route', () => {
    saveVisitOverrides('route-a', { '2892': 25 });

    expect(loadVisitOverrides('route-a')).toEqual({ '2892': 25 });
  });

  it('does not carry one route’s numbers into another', () => {
    saveVisitOverrides('route-a', { '2892': 25 });

    expect(loadVisitOverrides('route-b')).toEqual({});
  });

  it('survives unreadable storage instead of throwing', () => {
    localStorage.setItem('grodno-guide-visit-minutes', 'not json');

    expect(loadVisitOverrides('route-a')).toEqual({});
  });

  it('drops values that are not numbers', () => {
    localStorage.setItem(
      'grodno-guide-visit-minutes',
      JSON.stringify({ route: 'route-a', overrides: { a: 30, b: 'soon' } })
    );

    expect(loadVisitOverrides('route-a')).toEqual({ a: 30 });
  });
});

describe('visitMinutesFor', () => {
  it('prefers the tourist’s own number over the estimate', () => {
    expect(visitMinutesFor('a', 40, { a: 70 })).toBe(70);
  });

  it('falls back to the dataset estimate', () => {
    expect(visitMinutesFor('a', 40, {})).toBe(40);
  });

  it('admits it has nothing when neither exists', () => {
    expect(visitMinutesFor('a', null, {})).toBeNull();
    expect(visitMinutesFor('a', undefined, {})).toBeNull();
  });
});
