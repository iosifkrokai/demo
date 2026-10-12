import { describe, it, expect } from 'vitest';

import { metresBetween } from './geo';

describe('metresBetween', () => {
  it('is zero for the same point', () => {
    expect(
      metresBetween({ lat: 53.68, lon: 23.83 }, { lat: 53.68, lon: 23.83 })
    ).toBe(0);
  });

  it('counts metres, not degrees — one degree of latitude is ~111 km', () => {
    const oneDegree = metresBetween(
      { lat: 53.67, lon: 23.83 },
      { lat: 54.67, lon: 23.83 }
    );
    expect(oneDegree).toBeGreaterThan(110_000);
    expect(oneDegree).toBeLessThan(112_000);
  });

  it('agrees with the great-circle: a degree of longitude shrinks with latitude', () => {
    const atEquator = metresBetween({ lat: 0, lon: 0 }, { lat: 0, lon: 1 });
    const atGrodno = metresBetween(
      { lat: 53.68, lon: 23.83 },
      { lat: 53.68, lon: 24.83 }
    );
    expect(atGrodno).toBeLessThan(atEquator * 0.62);
    expect(atGrodno).toBeGreaterThan(atEquator * 0.58);
  });
});
