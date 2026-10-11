import { describe, expect, it } from 'vitest';

import {
  courseAlongLine,
  courseAtPoint,
  type CourseLine,
} from './guide-course';

/** A straight line between two points ~1 km apart, with cumulative metres. */
const straight = (
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number
): CourseLine => {
  const dLat = (lat2 - lat1) * 111_320;
  const dLon = (lon2 - lon1) * 111_320 * Math.cos((lat1 * Math.PI) / 180);
  const distance = Math.hypot(dLat, dLon);
  return {
    points: [
      { lat: lat1, lon: lon1 },
      { lat: lat2, lon: lon2 },
    ],
    cum: [0, distance],
  };
};

const close = (value: number | null, expected: number, tolerance = 2) => {
  expect(value).not.toBeNull();
  expect(Math.abs((value as number) - expected)).toBeLessThan(tolerance);
};

describe('курс вдоль маршрута', () => {
  it('на север — ноль, на восток — девяносто', () => {
    close(courseAlongLine(straight(53.7, 23.8, 53.8, 23.8), 100), 0);
    close(courseAlongLine(straight(53.7, 23.8, 53.7, 23.9), 100), 90);
  });

  it('на юг — сто восемьдесят, на запад — двести семьдесят', () => {
    close(courseAlongLine(straight(53.8, 23.8, 53.7, 23.8), 100), 180);
    close(courseAlongLine(straight(53.7, 23.9, 53.7, 23.8), 100), 270);
  });

  it('на повороте курс берётся с участка, до которого дошёл турист', () => {
    const line: CourseLine = {
      points: [
        { lat: 53.7, lon: 23.8 },
        { lat: 53.7, lon: 23.81 },
        { lat: 53.71, lon: 23.81 },
      ],
      cum: [0, 669, 1781],
    };

    close(courseAlongLine(line, 100), 90);
    close(courseAlongLine(line, 1000), 0);
  });

  it('без линии курса нет — и карта не выдумывает поворот', () => {
    expect(courseAlongLine(null, 0)).toBeNull();
    expect(
      courseAlongLine({ points: [{ lat: 53.7, lon: 23.8 }], cum: [0] }, 0)
    ).toBeNull();
  });

  it('курс можно найти по точке: «по курсу» работает ещё до первого хода', () => {
    const line: CourseLine = {
      points: [
        { lat: 53.7, lon: 23.8 },
        { lat: 53.7, lon: 23.81 },
        { lat: 53.71, lon: 23.81 },
      ],
      cum: [0, 669, 1781],
    };

    close(courseAtPoint(line, 53.7, 23.805), 90);
    close(courseAtPoint(line, 53.705, 23.81), 0);
    close(courseAtPoint(line, 53.7005, 23.804), 90);
  });

  it('по точке без линии курса нет', () => {
    expect(courseAtPoint(null, 53.7, 23.8)).toBeNull();
  });

  it('конец линии не уводит за неё: курс последнего участка', () => {
    close(courseAlongLine(straight(53.7, 23.8, 53.7, 23.9), 100_000), 90);
  });
});
