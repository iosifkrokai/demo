import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  currentFix,
  installGeoSim,
  isSimulating,
  metres,
  readSimOptions,
  resetSim,
  setSimPath,
  simSpeed,
  tick,
} from './geo-sim';

/** Two Grodno coordinates about 1.1 km apart — a walk of a few ticks. */
const START: [number, number] = [53.6778, 23.8295];
const NEXT: [number, number] = [53.6878, 23.8295];

const realGeolocation = window.navigator.geolocation;

afterEach(() => {
  resetSim();
  Object.defineProperty(window.navigator, 'geolocation', {
    value: realGeolocation,
    configurable: true,
  });
});

describe('readSimOptions', () => {
  it('молчит, пока её не позвали', () => {
    expect(readSimOptions('')).toEqual({ enabled: false, speed: 1 });
    expect(readSimOptions('?sim=drive')).toEqual({ enabled: false, speed: 1 });
    expect(readSimOptions('?a=1&b=2')).toEqual({ enabled: false, speed: 1 });
  });

  it('включается только явным параметром и приносит скорость', () => {
    expect(readSimOptions('?sim=walk')).toEqual({ enabled: true, speed: 1 });
    expect(readSimOptions('?sim=walk&sim-speed=20')).toEqual({
      enabled: true,
      speed: 20,
    });
  });

  it('не верит мусору в скорости', () => {
    expect(readSimOptions('?sim=walk&sim-speed=abc').speed).toBe(1);
    expect(readSimOptions('?sim=walk&sim-speed=-4').speed).toBe(1);
    expect(readSimOptions('?sim=walk&sim-speed=0').speed).toBe(1);
    // A runaway multiplier would make the walk untestable rather than fast.
    expect(readSimOptions('?sim=walk&sim-speed=9999').speed).toBe(200);
  });
});

describe('installGeoSim', () => {
  it('подменяет источник позиции, только когда её попросили', () => {
    expect(installGeoSim('')).toBe(false);
    expect(isSimulating()).toBe(false);
    expect(window.navigator.geolocation).toBe(realGeolocation);

    expect(installGeoSim('?sim=walk&sim-speed=10')).toBe(true);
    expect(isSimulating()).toBe(true);
    expect(simSpeed()).toBe(10);
    expect(window.navigator.geolocation).not.toBe(realGeolocation);
  });

  it('второй вызов не заводит второй источник', () => {
    expect(installGeoSim('?sim=walk')).toBe(true);
    const first = window.navigator.geolocation;
    expect(installGeoSim('?sim=walk')).toBe(true);
    expect(window.navigator.geolocation).toBe(first);
  });
});

describe('the walk', () => {
  it('идёт по остановкам и останавливается на последней', () => {
    installGeoSim('?sim=walk&sim-speed=20');
    setSimPath([START, NEXT]);

    const seen: Array<{ lat: number; lon: number }> = [];
    window.navigator.geolocation.watchPosition((pos) => {
      seen.push({ lat: pos.coords.latitude, lon: pos.coords.longitude });
    });

    const at = () => currentFix()!;
    const before = metres(at().lat, at().lon, START[0], START[1]);

    // 20 × 1.4 m/s = 28 m a tick; the leg is ~1.1 km, so 45 ticks is the whole
    // walk and then some — the last ones must not wander past the final stop.
    for (let i = 0; i < 60; i += 1) tick();

    const end = at();
    expect(metres(end.lat, end.lon, NEXT[0], NEXT[1])).toBeLessThan(5);
    expect(metres(end.lat, end.lon, START[0], START[1])).toBeGreaterThan(before);
    // Every tick reached the subscriber: the panel would have seen a tourist
    // standing still otherwise.
    expect(seen.length).toBeGreaterThanOrEqual(58);
    expect(seen[seen.length - 1]!.lat).toBeCloseTo(end.lat, 6);
  });

  it('не двигает никого, пока маршрута нет', () => {
    installGeoSim('?sim=walk');
    const onFix = vi.fn();
    window.navigator.geolocation.watchPosition(onFix);

    tick();

    expect(onFix).not.toHaveBeenCalled();
    expect(currentFix()).toBeNull();
  });

  it('отдаёт текущее положение по запросу, а не только потоком', () => {
    installGeoSim('?sim=walk');
    setSimPath([START, NEXT]);
    tick();

    const onSuccess = vi.fn();
    window.navigator.geolocation.getCurrentPosition(onSuccess);

    expect(onSuccess).toHaveBeenCalledTimes(1);
    const pos = onSuccess.mock.calls[0]![0] as GeolocationPosition;
    expect(pos.coords.latitude).toBeGreaterThan(START[0]);
    expect(pos.coords.accuracy).toBeGreaterThan(0);
  });

  it('после сброса ничего не помнит', () => {
    installGeoSim('?sim=walk');
    setSimPath([START, NEXT]);
    resetSim();

    expect(isSimulating()).toBe(false);
    expect(currentFix()).toBeNull();
  });
});
