import { describe, it, expect, afterEach } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

import { VISIT_MAX, VISIT_MIN } from '@/utils/visit-time';

import { useVisitOverrides } from './use-visit-overrides';

const STORAGE_KEY = 'grodno-guide-visit-minutes';

const readStored = () =>
  JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as {
    route?: string;
    overrides?: Record<string, number>;
  };

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe('useVisitOverrides', () => {
  it('persists the tourist’s own minutes and reloads them', () => {
    const first = renderHook(() => useVisitOverrides('route-a'));

    act(() => first.result.current.setVisitMinutes('1', 50));

    expect(first.result.current.overrides).toEqual({ '1': 50 });
    expect(readStored()).toEqual({ route: 'route-a', overrides: { '1': 50 } });

    // A fresh mount is a page reload: the number is read back from storage.
    first.unmount();
    const second = renderHook(() => useVisitOverrides('route-a'));

    expect(second.result.current.overrides).toEqual({ '1': 50 });
    expect(second.result.current.effectiveMinutesFor('1', 40)).toBe(50);
  });

  it('falls back to the estimate for a stop the tourist never touched', () => {
    const { result } = renderHook(() => useVisitOverrides('route-a'));

    expect(result.current.effectiveMinutesFor('9', 40)).toBe(40);
    expect(result.current.effectiveMinutesFor('9', null)).toBeNull();
  });

  it('reset drops the override so the estimate decides again', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ route: 'route-a', overrides: { '1': 50 } })
    );

    const { result } = renderHook(() => useVisitOverrides('route-a'));
    expect(result.current.effectiveMinutesFor('1', 40)).toBe(50);

    act(() => result.current.setVisitMinutes('1', null));

    expect(result.current.overrides).toEqual({});
    expect(result.current.effectiveMinutesFor('1', 40)).toBe(40);
    expect(readStored().overrides).toEqual({});
  });

  it('keeps each route’s times apart', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ route: 'route-a', overrides: { '1': 50 } })
    );

    const { result, rerender } = renderHook(
      ({ key }: { key: string }) => useVisitOverrides(key),
      { initialProps: { key: 'route-a' } }
    );
    expect(result.current.overrides).toEqual({ '1': 50 });

    rerender({ key: 'route-b' });
    expect(result.current.overrides).toEqual({});
  });

  it('clamps to the visit range', () => {
    const { result } = renderHook(() => useVisitOverrides('route-a'));

    act(() => result.current.setVisitMinutes('1', VISIT_MAX + 500));
    expect(result.current.overrides['1']).toBe(VISIT_MAX);

    act(() => result.current.setVisitMinutes('1', VISIT_MIN - 500));
    expect(result.current.overrides['1']).toBe(VISIT_MIN);
  });
});
