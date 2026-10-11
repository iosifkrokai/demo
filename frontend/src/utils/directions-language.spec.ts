import { describe, it, expect, vi, beforeEach } from 'vitest';

const i18nMock = vi.hoisted(() => ({ language: 'ru' as string }));
vi.mock('@/i18n', () => ({ default: i18nMock }));

import { getDirectionsLanguage } from './directions-language';

describe('directions-language', () => {
  beforeEach(() => {
    i18nMock.language = 'ru';
  });

  it('writes instructions in Russian for the Russian interface', () => {
    expect(getDirectionsLanguage()).toBe('ru-RU');
  });

  it('writes instructions in English for the English interface', () => {
    i18nMock.language = 'en';
    expect(getDirectionsLanguage()).toBe('en-US');
  });

  it('treats a region-tagged English locale as English', () => {
    i18nMock.language = 'en-GB';
    expect(getDirectionsLanguage()).toBe('en-US');
  });
});
