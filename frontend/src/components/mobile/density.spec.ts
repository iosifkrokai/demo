import { describe, it, expect } from 'vitest';

import { DENSITY } from './density';

/** Tailwind heights are a 4px scale: h-8 = 32, h-10 = 40, h-11 = 44. */
const heightOf = (token: string): number[] =>
  [...token.matchAll(/\bh-(\d+)\b/g)].map((m) => Number(m[1]) * 4);

describe('плотность телефона', () => {
  it('ни один токен высоты не опускается ниже порога пальца', () => {
    for (const [name, token] of Object.entries(DENSITY)) {
      for (const height of heightOf(token)) {
        expect(
          height,
          `${name} задаёт высоту ${height}px — ниже 40`
        ).toBeGreaterThanOrEqual(40);
      }
    }
  });

  it('на телефоне каждое условие указателя названо явно', () => {
    for (const name of ['segmentedItem', 'chip', 'languageItem'] as const) {
      expect(DENSITY[name]).toMatch(/pointer-coarse:max-md:/);
    }
  });

  it('плотность — только для телефона, десктопные значения не затрагивает', () => {
    for (const token of Object.values(DENSITY)) {
      expect(token).toMatch(/max-md:/);
    }
  });
});
