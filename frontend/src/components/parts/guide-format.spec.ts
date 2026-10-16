import { describe, it, expect, afterEach } from 'vitest';

import i18n from '@/i18n';
import { fmtMin, fmtDist, agentErrorMessage } from './guide-format';

afterEach(async () => {
  await i18n.changeLanguage('ru');
});

describe('fmtMin', () => {
  it('keeps the abbreviated minute under an hour', () => {
    expect(fmtMin(40)).toBe('40 мин');
    expect(fmtMin(59)).toBe('59 мин');
  });

  it('splits hours and minutes', () => {
    expect(fmtMin(70)).toBe('1 ч 10 мин');
    expect(fmtMin(95)).toBe('1 ч 35 мин');
  });

  it('drops the minutes when they are zero', () => {
    expect(fmtMin(120)).toBe('2 ч');
  });

  it('uses English units and the English hour plural in English', async () => {
    await i18n.changeLanguage('en');

    expect(fmtMin(40)).toBe('40 min');
    expect(fmtMin(70)).toBe('1 hr 10 min');
    expect(fmtMin(120)).toBe('2 hrs');
  });
});

describe('fmtDist', () => {
  it('uses metres and a comma decimal, never a dot', () => {
    expect(fmtDist(240)).toBe('240 м');
    expect(fmtDist(1300)).toBe('1,3 км');
    expect(fmtDist(1500)).toBe('1,5 км');
    expect(fmtDist(4500)).toBe('4,5 км');
  });

  it('switches to English units and a dot decimal in English', async () => {
    await i18n.changeLanguage('en');

    expect(fmtDist(240)).toBe('240 m');
    expect(fmtDist(1300)).toBe('1.3 km');
  });
});

describe('agentErrorMessage', () => {
  it('tells the truth about a 404 — a mispointed app, not an empty answer', () => {
    const message = agentErrorMessage(404);

    expect(message).toContain('404');
    expect(message).toContain('настройки');
    expect(message).not.toContain('не найдено место');
    expect(message).toMatch(/найдено/);
  });

  it('keeps the plain status wording for a server error', () => {
    expect(agentErrorMessage(503)).toBe(
      'агент ответил ошибкой 503 — попробуйте ещё раз'
    );
  });

  it('names an unconfigured planner instead of a bare 503', () => {
    const message = agentErrorMessage(503, 'llm_not_configured');

    expect(message).toContain('ключ');
    expect(message).not.toContain('503');
  });

  it('names an access problem and a bad request for what they are', () => {
    expect(agentErrorMessage(403)).toContain('403');
    expect(agentErrorMessage(422)).toContain('422');
  });

  it('answers in English when the interface is English', async () => {
    await i18n.changeLanguage('en');

    expect(agentErrorMessage(404)).toContain('404');
    expect(agentErrorMessage(503)).toBe(
      'the agent answered with error 503 — try again'
    );
    expect(agentErrorMessage(403)).toContain('403');
  });
});
