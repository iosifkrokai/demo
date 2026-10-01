import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { PointerEvent as ReactPointerEvent } from 'react';

import {
  MOBILE_GUIDE_HEIGHT,
  MOBILE_SHEET_HEIGHT,
  useMobileSheetSnap,
} from './use-mobile-sheet-snap';

const at = (clientY: number) => ({ clientY }) as ReactPointerEvent<HTMLElement>;

describe('useMobileSheetSnap', () => {
  it('starts at the peek height the plan fixes', () => {
    const { result } = renderHook(() => useMobileSheetSnap());
    expect(result.current.snap).toBe('peek');
    expect(result.current.height).toBe(MOBILE_SHEET_HEIGHT.peek);
  });

  it('steps through the positions on a tap and stops at the top', () => {
    const { result } = renderHook(() => useMobileSheetSnap('bar'));

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('peek');

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('full');

    // A second tap at the top must not wrap around to the strip.
    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('full');
  });

  it('picks the position the finger was released closest to', () => {
    const { result } = renderHook(() => useMobileSheetSnap('peek'));

    act(() => {
      result.current.handleProps.onPointerDown(at(800));
      result.current.handleProps.onPointerMove(at(700));
      result.current.handleProps.onPointerUp(at(700));
    });
    expect(result.current.snap).toBe('full');

    act(() => {
      result.current.handleProps.onPointerDown(at(300));
      result.current.handleProps.onPointerMove(at(420));
      result.current.handleProps.onPointerUp(at(420));
    });
    expect(result.current.snap).toBe('bar');
  });

  it('swallows the tap a drag would otherwise end with', () => {
    const { result } = renderHook(() => useMobileSheetSnap('peek'));

    act(() => {
      result.current.handleProps.onPointerDown(at(800));
      result.current.handleProps.onPointerMove(at(700));
      result.current.handleProps.onPointerUp(at(700));
      result.current.handleProps.onClick(); // the click that follows the drag
    });
    expect(result.current.snap).toBe('full');
  });

  it('keeps every height in one place, the guide strip included', () => {
    expect(MOBILE_SHEET_HEIGHT).toEqual({
      bar: '132px',
      peek: '45dvh',
      full: '90dvh',
    });
    expect(MOBILE_GUIDE_HEIGHT).toBe('26dvh');
  });
});
