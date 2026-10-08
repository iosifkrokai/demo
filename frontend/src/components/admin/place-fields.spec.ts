import { describe, it, expect } from 'vitest';

import type { Place } from '@/api/types';

import { fieldsOf, formatCoord, toPlacePatch } from './place-fields';

const place = (overrides: Partial<Place> = {}): Place => ({
  place_id: 7,
  source_url: 'city:old-castle',
  name: 'Старый замок',
  category: 'замок',
  town: 'Гродно',
  district: null,
  lat: 53.6768,
  lon: 23.8223,
  visit_minutes: 90,
  opening_hours: null,
  blurb: 'Замок',
  fun_fact: 'Факт',
  fun_facts: [],
  links: [],
  ticket_price: null,
  photo: null,
  ...overrides,
});

describe('place-fields', () => {
  it('reads the coordinates into the form as text', () => {
    const fields = fieldsOf(place());
    expect(fields.lat).toBe('53.6768');
    expect(fields.lon).toBe('23.8223');
  });

  it('sends dragged coordinates as numbers', () => {
    const patch = toPlacePatch({
      ...fieldsOf(place()),
      lat: '53.7001',
      lon: '23.9002',
    });
    expect(patch.lat).toBe(53.7001);
    expect(patch.lon).toBe(23.9002);
  });

  it('omits a blank coordinate (means «do not move»), never 0°E/0°N', () => {
    const patch = toPlacePatch({ ...fieldsOf(place()), lat: '  ', lon: '' });
    expect('lat' in patch).toBe(false);
    expect('lon' in patch).toBe(false);
    expect(patch.lat).toBeUndefined();
  });

  it('omits unparseable text rather than sending NaN', () => {
    const patch = toPlacePatch({
      ...fieldsOf(place()),
      lat: 'abc',
      lon: '53,6',
    });
    expect('lat' in patch).toBe(false);
    expect('lon' in patch).toBe(false);
  });

  it('keeps 0 a real coordinate, not a blank', () => {
    const patch = toPlacePatch({ ...fieldsOf(place()), lat: '0', lon: '0' });
    expect(patch.lat).toBe(0);
    expect(patch.lon).toBe(0);
  });

  it('formats a dragged coordinate without float noise', () => {
    expect(formatCoord(53.676812345678)).toBe('53.6768123');
  });
});
