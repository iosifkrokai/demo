import { describe, expect, it } from 'vitest';

import { MIN_TURN_GAP_M, mergeMicroManeuvers } from './guide-maneuvers';

/** A maneuver as the panel builds it: an instruction at a distance along the line. */
const m = (along: number, instruction: string) => ({ along, instruction });

describe('слияние микроповоротов', () => {
  it('оставляет один поворот из цепочки мелких и берёт последний', () => {
    // Three steps 5 m apart are one move; the one that leaves the chain is the
    // one worth saying («выйти на Рабочую» и есть поворот).
    const merged = mergeMicroManeuvers([
      m(0, 'идите по дорожке'),
      m(5, 'поверните направо'),
      m(12, 'поверните налево'),
      m(300, 'поверните направо на Рабочую'),
    ]);

    expect(merged.map((x) => x.instruction)).toEqual([
      'поверните налево',
      'поверните направо на Рабочую',
    ]);
  });

  it('не трогает повороты, между которыми реальное расстояние', () => {
    const list = [m(0, 'раз'), m(80, 'два'), m(200, 'три')];
    expect(mergeMicroManeuvers(list)).toEqual(list);
  });

  it('ровно на границе — разные повороты', () => {
    const merged = mergeMicroManeuvers([m(0, 'раз'), m(MIN_TURN_GAP_M, 'два')]);
    expect(merged).toHaveLength(2);
  });

  it('пустой список и одиночный поворот остаются собой', () => {
    expect(mergeMicroManeuvers([])).toEqual([]);
    expect(mergeMicroManeuvers([m(42, 'один')])).toEqual([m(42, 'один')]);
  });

  it('порядок сохраняется: результат — по-прежнему «следующий впереди»', () => {
    const merged = mergeMicroManeuvers([
      m(0, 'a'),
      m(3, 'b'),
      m(50, 'c'),
      m(52, 'd'),
      m(400, 'e'),
    ]);
    const alongs = merged.map((x) => x.along);
    expect(alongs).toEqual([...alongs].sort((p, q) => p - q));
  });
});
