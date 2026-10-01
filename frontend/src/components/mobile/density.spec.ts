import { describe, it, expect } from 'vitest';

import { DENSITY, DENSITY_MAX_WIDTH, MIN_TAP_PX } from './density';
import { MOBILE_MAX_WIDTH } from './use-is-mobile';

/** Tailwind heights are a 4px scale: h-8 = 32, h-10 = 40, h-11 = 44. */
const heightOf = (token: string): number[] =>
  [...token.matchAll(/\bh-(\d+)\b/g)].map((m) => Number(m[1]) * 4);

describe('плотность телефона', () => {
  it('одна граница с шеллом: тот же брейкпоинт, что решает, кто на экране', () => {
    expect(DENSITY_MAX_WIDTH).toBe(MOBILE_MAX_WIDTH);
    expect(DENSITY_MAX_WIDTH).toBe(767);
  });

  it('ни один токен высоты не опускается ниже порога пальца', () => {
    expect(MIN_TAP_PX).toBe(40);
    for (const [name, token] of Object.entries(DENSITY)) {
      for (const height of heightOf(token)) {
        expect(
          height,
          `${name} задаёт высоту ${height}px — ниже ${MIN_TAP_PX}`
        ).toBeGreaterThanOrEqual(MIN_TAP_PX);
      }
    }
  });

  it('на телефоне каждое условие указателя названо явно', () => {
    // Если токен переопределяет то, что уже задано через `pointer-coarse:`,
    // он обязан назвать комбинацию: иначе на телефоне совпадают оба условия и
    // побеждает порядок в CSS — то есть случайность.
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
