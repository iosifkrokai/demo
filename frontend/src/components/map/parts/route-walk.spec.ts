import { describe, expect, it } from 'vitest';

import { metresBetween, splitAtPosition } from './route-walk';

/** A straight line south to north along the meridian: ~111.3 m per 0.001°. */
const LINE: [number, number][] = [
  [23.83, 53.67],
  [23.83, 53.68],
  [23.83, 53.69],
];

describe('splitAtPosition', () => {
  it('режет маршрут ровно под туристом', () => {
    const split = splitAtPosition(LINE, { lat: 53.685, lon: 23.83 });

    expect(split).not.toBeNull();
    expect(split!.walked.at(-1)).toEqual([23.83, 53.685]);
    expect(split!.remaining[0]).toEqual([23.83, 53.685]);
    expect(split!.travelled).toBeGreaterThan(1600);
    expect(split!.travelled).toBeLessThan(1700);
    expect(split!.total).toBeGreaterThan(split!.travelled);
  });

  it('до пройденного остаётся только точка туриста', () => {
    const split = splitAtPosition(LINE, { lat: 53.67, lon: 23.83 });

    expect(split!.walked).toEqual([[23.83, 53.67]]);
    expect(split!.remaining.length).toBe(3);
  });

  it('позади весь маршрут, впереди — одна точка', () => {
    const split = splitAtPosition(LINE, { lat: 53.69, lon: 23.83 });

    expect(split!.walked.length).toBe(3);
    expect(split!.remaining).toEqual([[23.83, 53.69]]);
    expect(split!.travelled).toBeCloseTo(split!.total, 0);
  });

  it('молчит, когда турист далеко от маршрута', () => {
    expect(splitAtPosition(LINE, { lat: 53.9, lon: 24.5 })).toBeNull();
  });

  it('молчит на вырожденной линии', () => {
    expect(
      splitAtPosition([[23.83, 53.67]], { lat: 53.67, lon: 23.83 })
    ).toBeNull();
  });

  it('считает метры, а не градусы', () => {
    const oneDegree = metresBetween(
      { lat: 53.67, lon: 23.83 },
      { lat: 53.68, lon: 23.83 }
    );
    expect(oneDegree).toBeGreaterThan(1100);
    expect(oneDegree).toBeLessThan(1120);
  });
});
