import { describe, it, expect, beforeEach } from 'vitest';
import {
  mapStyleSchema,
  getMapStyleUrl,
  getInitialMapStyle,
  getCustomStyle,
} from './utils';
import { MAP_STYLE_STORAGE_KEY, CUSTOM_STYLE_STORAGE_KEY } from './constants';

describe('mapStyleSchema', () => {
  it('should validate built-in style ids', () => {
    expect(mapStyleSchema.safeParse('shortbread').success).toBe(true);
    expect(mapStyleSchema.safeParse('carto').success).toBe(true);
    expect(mapStyleSchema.safeParse('alidade-smooth').success).toBe(true);
  });

  it('should validate custom style', () => {
    expect(mapStyleSchema.safeParse('custom').success).toBe(true);
  });

  it('should reject invalid style ids', () => {
    expect(mapStyleSchema.safeParse('invalid').success).toBe(false);
    expect(mapStyleSchema.safeParse('').success).toBe(false);
    expect(mapStyleSchema.safeParse(null).success).toBe(false);
    expect(mapStyleSchema.safeParse(undefined).success).toBe(false);
    expect(mapStyleSchema.safeParse(123).success).toBe(false);
  });
});

describe('getMapStyleUrl', () => {
  it('should return correct URL for shortbread style', () => {
    expect(getMapStyleUrl('shortbread')).toBe(
      '/styles/versatiles-colorful.json'
    );
  });

  it('should return correct URL for carto style', () => {
    expect(getMapStyleUrl('carto')).toBe('/styles/carto.json');
  });

  it('should return correct URL for alidade-smooth style', () => {
    expect(getMapStyleUrl('alidade-smooth')).toBe(
      'https://tiles.stadiamaps.com/styles/alidade_smooth.json'
    );
  });

  it('should return default style URL for custom style', () => {
    expect(getMapStyleUrl('custom')).toBe('/styles/versatiles-colorful.json');
  });
});

describe('getInitialMapStyle', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('should return urlValue if it is a valid built-in style', () => {
    expect(getInitialMapStyle('shortbread')).toBe('shortbread');
    expect(getInitialMapStyle('carto')).toBe('carto');
    expect(getInitialMapStyle('alidade-smooth')).toBe('alidade-smooth');
  });

  it('should return custom if urlValue is custom and custom style exists in localStorage', () => {
    localStorage.setItem(CUSTOM_STYLE_STORAGE_KEY, '{"version":8}');
    expect(getInitialMapStyle('custom')).toBe('custom');
  });

  it('should return default if urlValue is custom but no custom style in localStorage', () => {
    expect(getInitialMapStyle('custom')).toBe('shortbread');
  });

  it('should fallback to localStorage if urlValue is undefined', () => {
    localStorage.setItem(MAP_STYLE_STORAGE_KEY, 'carto');
    expect(getInitialMapStyle()).toBe('carto');
  });

  it('should return custom from localStorage if custom style exists', () => {
    localStorage.setItem(MAP_STYLE_STORAGE_KEY, 'custom');
    localStorage.setItem(CUSTOM_STYLE_STORAGE_KEY, '{"version":8}');
    expect(getInitialMapStyle()).toBe('custom');
  });

  it('should return default if localStorage has custom but no custom style data', () => {
    localStorage.setItem(MAP_STYLE_STORAGE_KEY, 'custom');
    expect(getInitialMapStyle()).toBe('shortbread');
  });

  it('should return default if localStorage has invalid value', () => {
    localStorage.setItem(MAP_STYLE_STORAGE_KEY, 'invalid-style');
    expect(getInitialMapStyle()).toBe('shortbread');
  });

  it('should return default if no urlValue and no localStorage', () => {
    expect(getInitialMapStyle()).toBe('shortbread');
  });

  it('should return default for invalid urlValue', () => {
    expect(getInitialMapStyle('invalid')).toBe('shortbread');
    expect(getInitialMapStyle('')).toBe('shortbread');
  });
});

describe('getCustomStyle', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('should return null if no custom style in localStorage', () => {
    expect(getCustomStyle()).toBeNull();
  });

  it('should return parsed style if valid JSON in localStorage', () => {
    const styleData = { version: 8, name: 'Test', sources: {}, layers: [] };
    localStorage.setItem(CUSTOM_STYLE_STORAGE_KEY, JSON.stringify(styleData));
    expect(getCustomStyle()).toEqual(styleData);
  });

  it('should return null if invalid JSON in localStorage', () => {
    localStorage.setItem(CUSTOM_STYLE_STORAGE_KEY, 'invalid-json{');
    expect(getCustomStyle()).toBeNull();
  });

  it('should return complex style object correctly', () => {
    const styleData = {
      version: 8,
      name: 'Complex Style',
      sources: {
        osm: { type: 'vector', url: 'https://example.com' },
      },
      layers: [
        {
          id: 'background',
          type: 'background',
          paint: { 'background-color': '#fff' },
        },
      ],
    };
    localStorage.setItem(CUSTOM_STYLE_STORAGE_KEY, JSON.stringify(styleData));
    expect(getCustomStyle()).toEqual(styleData);
  });
});
