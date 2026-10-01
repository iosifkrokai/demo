import { describe, it, expect, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { MOBILE_MAX_WIDTH, useIsMobile } from './use-is-mobile';

const setWidth = (width: number) =>
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width,
  });

/** A stand-in for the media query, behaving like the CSS predicate. */
const stubMatchMedia = () => {
  const state = { matches: true };
  let listeners: Array<() => void> = [];
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      get matches() {
        return state.matches;
      },
      addEventListener: (_: string, cb: () => void) => {
        listeners.push(cb);
      },
      removeEventListener: (_: string, cb: () => void) => {
        listeners = listeners.filter((l) => l !== cb);
      },
    }))
  );
  return {
    state,
    fire: () => listeners.forEach((l) => l()),
  };
};

describe('useIsMobile', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setWidth(1024);
  });

  it('reads the phone branch from the real viewport', () => {
    setWidth(390);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it('puts the breakpoint exactly at 767/768, like Tailwind md', () => {
    setWidth(MOBILE_MAX_WIDTH);
    expect(renderHook(() => useIsMobile()).result.current).toBe(true);
    setWidth(MOBILE_MAX_WIDTH + 1);
    expect(renderHook(() => useIsMobile()).result.current).toBe(false);
  });

  it('follows the media query when the viewport changes', () => {
    const media = stubMatchMedia();
    media.state.matches = true;
    setWidth(390);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);

    act(() => {
      media.state.matches = false;
      media.fire();
    });
    expect(result.current).toBe(false);
  });

  it('answers where matchMedia is missing (jsdom), instead of throwing', () => {
    setWidth(1440);
    expect(() => renderHook(() => useIsMobile())).not.toThrow();
    expect(renderHook(() => useIsMobile()).result.current).toBe(false);
  });
});
