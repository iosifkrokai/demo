import { describe, it, expect } from 'vitest';

import { DEFAULT_TRAVEL_MODE, guideModeFor } from './guide-mode';

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
      expect(guideModeFor(value).id).toBe('foot');
    }
    expect(guideModeFor(undefined)).toBe(DEFAULT_TRAVEL_MODE);
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
});
