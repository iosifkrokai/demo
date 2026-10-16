import { describe, it, expect, beforeEach } from 'vitest';
import { getBaseUrl } from './base-url';

const STORAGE_KEY = 'valhalla_base_url';
const TEST_CUSTOM_URL = 'https://custom.valhalla.com';

describe('getBaseUrl', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns the app origin when localStorage is empty', () => {
    expect(getBaseUrl()).toBe(window.location.origin);
  });

  it('returns the stored URL when present in localStorage', () => {
    localStorage.setItem(STORAGE_KEY, TEST_CUSTOM_URL);
    expect(getBaseUrl()).toBe(TEST_CUSTOM_URL);
  });
});
