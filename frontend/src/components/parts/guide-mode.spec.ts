import { describe, it, expect, afterEach } from 'vitest';

import i18n from '@/i18n';
import { defaultTravelMode, guideModeFor } from './guide-mode';

afterEach(async () => {
  await i18n.changeLanguage('ru');
});

describe('guideModeFor', () => {
  it('walks when the plan says pedestrian', () => {
    const mode = guideModeFor('pedestrian');

    expect(mode.id).toBe('foot');
    expect(mode.verb).toBe('идти');
    expect(mode.doneWord).toBe('пройдено');
    expect(mode.arrivalHint).toBeUndefined();
  });

  it('walks when nothing is stated — that is what a route without a transport means', () => {
    for (const value of [undefined, null, '', 'unknown-costing']) {
      expect(guideModeFor(value).id).toBe(defaultTravelMode().id);
    }
    expect(guideModeFor(undefined).id).toBe('foot');
    expect(guideModeFor(undefined).label).toBe(defaultTravelMode().label);
  });

  it('rides when the plan says bicycle', () => {
    const mode = guideModeFor('bicycle');

    expect(mode.id).toBe('bike');
    expect(mode.verb).toBe('ехать');
    expect(mode.doneWord).toBe('проехано');
    expect(mode.label).toBe('на велосипеде');
    expect(mode.arrivalHint).toBeUndefined();
  });

  it('drives on a car route, and says where the car goes on arrival', () => {
    for (const costing of ['auto', 'car', 'truck', 'bus', 'motorcycle']) {
      const mode = guideModeFor(costing);
      expect(mode.id).toBe('car');
      expect(mode.verb).toBe('ехать');
      expect(mode.label).toBe('на машине');
      expect(mode.arrivalHint).toBeTruthy();
    }
  });

  it('is case-insensitive about the costing name', () => {
    expect(guideModeFor('AUTO').id).toBe('car');
    expect(guideModeFor('Bicycle').id).toBe('bike');
  });

  it('gives every mode an icon to draw', () => {
    for (const costing of ['pedestrian', 'bicycle', 'auto']) {
      expect(guideModeFor(costing).icon).toBeTruthy();
    }
  });

  it('speaks English when the interface is English', async () => {
    await i18n.changeLanguage('en');

    expect(guideModeFor('pedestrian').verb).toBe('walk');
    expect(guideModeFor('bicycle').label).toBe('by bike');
    expect(guideModeFor('auto').arrivalHint).toBe('park by the stop');
    // The number of modes does not change with the language.
    expect(defaultTravelMode().id).toBe('foot');
  });
});
