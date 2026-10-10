import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  CLIENT_ID_STORAGE_KEY,
  clearClientId,
  getClientId,
  isClientId,
  peekClientId,
} from './client-id';

const STORED_ID = '11111111-1111-4111-8111-111111111111';

beforeEach(() => {
  clearClientId();
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('client id', () => {
  it('creates the id once and reuses it on every later call', () => {
    let calls = 0;
    const randomUUID = vi.fn(
      () => `00000000-0000-4000-8000-${String(++calls).padStart(12, '0')}`
    );
    vi.stubGlobal('crypto', { randomUUID });

    const first = getClientId();
    const second = getClientId();

    expect(first).toBe(second);
    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(CLIENT_ID_STORAGE_KEY)).toBe(first);
    expect(isClientId(first)).toBe(true);
  });

  it('reuses the id already in storage without minting a new one', () => {
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, STORED_ID);
    const randomUUID = vi.fn(() => 'should-not-be-called');
    vi.stubGlobal('crypto', { randomUUID });

    expect(getClientId()).toBe(STORED_ID);
    expect(getClientId()).toBe(STORED_ID);
    expect(randomUUID).not.toHaveBeenCalled();
  });

  it('replaces a malformed stored value, then keeps the replacement', () => {
    localStorage.setItem(CLIENT_ID_STORAGE_KEY, 'not-a-uuid');

    const id = getClientId();

    expect(isClientId(id)).toBe(true);
    expect(getClientId()).toBe(id);
  });

  it('peek does not create an id', () => {
    expect(peekClientId()).toBeNull();
    expect(localStorage.getItem(CLIENT_ID_STORAGE_KEY)).toBeNull();
  });

  it('clearClientId forgets the browser copy', () => {
    const first = getClientId();
    clearClientId();

    expect(peekClientId()).toBeNull();
    expect(localStorage.getItem(CLIENT_ID_STORAGE_KEY)).toBeNull();

    const second = getClientId();
    expect(isClientId(second)).toBe(true);
    expect(localStorage.getItem(CLIENT_ID_STORAGE_KEY)).toBe(second);
    expect(second).not.toBe(first);
  });
});
