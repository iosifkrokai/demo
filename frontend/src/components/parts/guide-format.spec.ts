import { describe, it, expect } from 'vitest';

import {
  fmtMin,
  fmtDist,
  fmtStops,
  agentErrorMessage,
  metresBetween,
} from './guide-format';

describe('fmtMin', () => {
  it('keeps the abbreviated minute under an hour', () => {
    expect(fmtMin(40)).toBe('40 мин');
    expect(fmtMin(59)).toBe('59 мин');
  });

  it('splits hours and minutes', () => {
    expect(fmtMin(70)).toBe('1 ч 10 мин');
    expect(fmtMin(95)).toBe('1 ч 35 мин');
  });

  it('drops the minutes when they are zero', () => {
    expect(fmtMin(120)).toBe('2 ч');
  });
});

describe('fmtDist', () => {
  it('uses metres and a comma decimal, never a dot', () => {
    expect(fmtDist(240)).toBe('240 м');
    expect(fmtDist(1300)).toBe('1,3 км');
    expect(fmtDist(1500)).toBe('1,5 км');
    expect(fmtDist(4500)).toBe('4,5 км');
  });
});

describe('fmtStops', () => {
  it('inflects остановка', () => {
    expect(fmtStops(1)).toBe('1 остановка');
    expect(fmtStops(3)).toBe('3 остановки');
    expect(fmtStops(5)).toBe('5 остановок');
  });
});

describe('agentErrorMessage', () => {
  it('tells the truth about a 404 — a mispointed app, not an empty answer', () => {
    const message = agentErrorMessage(404);

    expect(message).toContain('404');
    expect(message).toContain('настройки');
    expect(message).not.toContain('не найдено место');
    expect(message).toMatch(/найдено/); // only inside the «а не …» denial
  });

  it('keeps the plain status wording for a server error', () => {
    expect(agentErrorMessage(503)).toBe(
      'агент ответил ошибкой 503 — попробуйте ещё раз'
    );
  });

  it('names an access problem and a bad request for what they are', () => {
    expect(agentErrorMessage(403)).toContain('403');
    expect(agentErrorMessage(422)).toContain('422');
  });
});

describe('metresBetween', () => {
  it('is zero for the same point', () => {
    expect(
      metresBetween({ lat: 53.68, lon: 23.83 }, { lat: 53.68, lon: 23.83 })
    ).toBe(0);
  });
});
