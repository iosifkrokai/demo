import { describe, it, expect } from 'vitest';

import {
  pluralRu,
  pluralCountRu,
  pointsLabel,
  stopsLabel,
  minutesLabel,
  decimalRu,
  formatDistanceRu,
  POINT_FORMS,
} from './plural';

describe('pluralRu', () => {
  it('takes the singular for 1 and 21 (and 101)', () => {
    expect(pluralRu(1, POINT_FORMS)).toBe('точка');
    expect(pluralRu(21, POINT_FORMS)).toBe('точка');
    expect(pluralRu(101, POINT_FORMS)).toBe('точка');
  });

  it('takes the few form for 2–4 and 22–24', () => {
    expect(pluralRu(2, POINT_FORMS)).toBe('точки');
    expect(pluralRu(3, POINT_FORMS)).toBe('точки');
    expect(pluralRu(4, POINT_FORMS)).toBe('точки');
    expect(pluralRu(23, POINT_FORMS)).toBe('точки');
  });

  it('takes the many form for 5–20', () => {
    expect(pluralRu(5, POINT_FORMS)).toBe('точек');
    expect(pluralRu(11, POINT_FORMS)).toBe('точек');
    expect(pluralRu(12, POINT_FORMS)).toBe('точек');
    expect(pluralRu(14, POINT_FORMS)).toBe('точек');
    expect(pluralRu(0, POINT_FORMS)).toBe('точек');
  });

  it('keeps 11–14 on the many form even though they end in 1–4', () => {
    expect(pluralRu(11, POINT_FORMS)).toBe('точек');
    expect(pluralRu(111, POINT_FORMS)).toBe('точек');
    expect(pluralRu(112, POINT_FORMS)).toBe('точек');
    expect(pluralRu(114, POINT_FORMS)).toBe('точек');
    // …while 115 goes back to the few form.
    expect(pluralRu(121, POINT_FORMS)).toBe('точка');
  });

  it('counts negatives by their magnitude', () => {
    expect(pluralRu(-3, POINT_FORMS)).toBe('точки');
  });
});

describe('pluralCountRu and the counted labels', () => {
  it('joins the number and the right form', () => {
    expect(pluralCountRu(3, POINT_FORMS)).toBe('3 точки');
    expect(pluralCountRu(1, POINT_FORMS)).toBe('1 точка');
    expect(pluralCountRu(5, POINT_FORMS)).toBe('5 точек');
  });

  it('inflects точки, остановки and минуты', () => {
    expect(pointsLabel(3)).toBe('точки');
    expect(pointsLabel(1)).toBe('точка');
    expect(pointsLabel(7)).toBe('точек');

    expect(stopsLabel(1)).toBe('остановка');
    expect(stopsLabel(2)).toBe('остановки');
    expect(stopsLabel(5)).toBe('остановок');

    expect(minutesLabel(1)).toBe('минута');
    expect(minutesLabel(3)).toBe('минуты');
    expect(minutesLabel(40)).toBe('минут');
  });
});

describe('decimalRu', () => {
  it('uses the Russian comma, not a dot', () => {
    expect(decimalRu(1.3)).toBe('1,3');
    expect(decimalRu(4.5)).toBe('4,5');
    expect(decimalRu(15)).toBe('15,0');
  });

  it('honours the digit count it is given', () => {
    expect(decimalRu(1.25, 2)).toBe('1,25');
    expect(decimalRu(12.6, 0)).toBe('13');
  });
});

describe('formatDistanceRu', () => {
  it('keeps metres below a kilometre', () => {
    expect(formatDistanceRu(240)).toBe('240 м');
    expect(formatDistanceRu(999)).toBe('999 м');
  });

  it('switches to kilometres with a comma decimal', () => {
    expect(formatDistanceRu(1300)).toBe('1,3 км');
    expect(formatDistanceRu(1500)).toBe('1,5 км');
    expect(formatDistanceRu(4500)).toBe('4,5 км');
  });
});
