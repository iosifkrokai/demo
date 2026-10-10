import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/** The run lives in module state, so each case re-imports a fresh copy. */
const load = () => import('./run-session');

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('guide run session', () => {
  it('keeps one id for the whole run', async () => {
    let calls = 0;
    vi.stubGlobal('crypto', {
      randomUUID: vi.fn(
        () => `00000000-0000-4000-8000-${String(++calls).padStart(12, '0')}`
      ),
    });
    const { currentRunSession, startRunSession } = await load();

    const first = startRunSession();

    expect(currentRunSession()).toBe(first);
    expect(currentRunSession()).toBe(first);
  });

  it('opens a new id when a new topic begins', async () => {
    const { currentRunSession, startRunSession } = await load();

    const first = startRunSession();
    const second = startRunSession();

    expect(second).not.toBe(first);
    expect(currentRunSession()).toBe(second);
  });

  it('mints one on first use when no run was started yet', async () => {
    const { currentRunSession } = await load();

    const first = currentRunSession();

    expect(first).toBeTruthy();
    expect(currentRunSession()).toBe(first);
  });

  it('falls back without crypto.randomUUID (plain http on a LAN)', async () => {
    vi.stubGlobal('crypto', {});
    const { currentRunSession } = await load();

    expect(currentRunSession()).toMatch(/^run-/);
  });
});
