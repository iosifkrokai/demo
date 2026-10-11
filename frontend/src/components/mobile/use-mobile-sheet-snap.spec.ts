import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { PointerEvent as ReactPointerEvent } from 'react';

import {
  MOBILE_GUIDE_HEIGHT,
  MOBILE_SHEET_HEIGHT,
  guidePx,
  snapPx,
  useMobileSheetSnap,
} from './use-mobile-sheet-snap';

const at = (clientY: number) => ({ clientY }) as ReactPointerEvent<HTMLElement>;

/** One drag, in `steps` moves over `ms`, ending with a release at the last position. */
const drag = (
  handleProps: ReturnType<typeof useMobileSheetSnap>['handleProps'],
  from: number,
  to: number,
  { steps = 4, ms = 40 }: { steps?: number; ms?: number } = {}
) => {
  act(() => {
    handleProps.onPointerDown(at(from));
    for (let i = 1; i <= steps; i++) {
      vi.advanceTimersByTime(ms / steps);
      handleProps.onPointerMove(at(from + ((to - from) * i) / steps));
    }
    vi.advanceTimersByTime(ms / steps);
    handleProps.onPointerUp(at(to));
  });
};

/** A drag that ends with the finger resting before it lifts. */
const dragThenHold = (
  handleProps: ReturnType<typeof useMobileSheetSnap>['handleProps'],
  from: number,
  to: number,
  holdMs: number
) => {
  act(() => {
    handleProps.onPointerDown(at(from));
    for (let i = 1; i <= 3; i++) {
      vi.advanceTimersByTime(10);
      handleProps.onPointerMove(at(from + ((to - from) * i) / 3));
    }
    vi.advanceTimersByTime(holdMs);
    handleProps.onPointerUp(at(to));
  });
};

describe('useMobileSheetSnap', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('starts at the peek height the plan fixes', () => {
    const { result } = renderHook(() => useMobileSheetSnap());
    expect(result.current.snap).toBe('peek');
    expect(result.current.height).toBe(MOBILE_SHEET_HEIGHT.peek);
    expect(result.current.dragging).toBe(false);
    expect(result.current.dragHeight).toBeNull();
  });

  it('steps through the positions on a tap and stops at the top', () => {
    const { result } = renderHook(() => useMobileSheetSnap('bar'));

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('peek');

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('full');

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('full');
  });

  it('steps one position per flick, in the direction it went', () => {
    const up = renderHook(() => useMobileSheetSnap('peek'));
    drag(up.result.current.handleProps, 800, 700);
    expect(up.result.current.snap).toBe('full');

    const down = renderHook(() => useMobileSheetSnap('peek'));
    drag(down.result.current.handleProps, 300, 380);
    expect(down.result.current.snap).toBe('bar');
  });

  it('treats a short fast flick as a full step, and the same travel slowly as none', () => {
    const quick = renderHook(() => useMobileSheetSnap('bar'));
    drag(quick.result.current.handleProps, 800, 780, { ms: 20 });
    expect(quick.result.current.snap).toBe('peek');

    const slow = renderHook(() => useMobileSheetSnap('bar'));
    drag(slow.result.current.handleProps, 800, 780, { ms: 400 });
    expect(slow.result.current.snap).toBe('bar');
  });

  it('does not read a flick that ended in a pause as a flick', () => {
    const { result } = renderHook(() => useMobileSheetSnap('bar'));
    dragThenHold(result.current.handleProps, 800, 780, 400);
    expect(result.current.snap).toBe('bar');
  });

  it('lands a long careful drag on the nearest position', () => {
    const { result } = renderHook(() => useMobileSheetSnap('peek'));
    drag(result.current.handleProps, 300, 440, { ms: 600, steps: 8 });
    expect(result.current.snap).toBe('bar');
  });

  it('follows the finger while it is down, and settles on release', () => {
    const { result } = renderHook(() => useMobileSheetSnap('peek'));
    const peek = snapPx('peek');

    act(() => result.current.handleProps.onPointerDown(at(800)));
    act(() => result.current.handleProps.onPointerMove(at(700)));

    expect(result.current.dragging).toBe(true);
    expect(result.current.dragHeight).toBe(peek + 100);

    act(() => result.current.handleProps.onPointerUp(at(700)));
    expect(result.current.dragging).toBe(false);
    expect(result.current.dragHeight).toBeNull();
  });

  it('gives ground slowly past the top instead of stopping dead', () => {
    const { result } = renderHook(() => useMobileSheetSnap('full'));
    const full = snapPx('full');

    act(() => result.current.handleProps.onPointerDown(at(400)));
    act(() => result.current.handleProps.onPointerMove(at(200)));

    expect(result.current.dragHeight).toBe(full + 60);
  });

  it('starts a drag from the strip while walking, not from 39dvh', () => {
    const { result } = renderHook(() =>
      useMobileSheetSnap('peek', { restingPx: guidePx() })
    );

    act(() => result.current.handleProps.onPointerDown(at(800)));
    act(() => result.current.handleProps.onPointerMove(at(700)));
    expect(result.current.dragHeight).toBe(guidePx() + 100);
  });

  it('closes the panel on a flick down from the strip', () => {
    const onDismiss = vi.fn();
    const { result } = renderHook(() =>
      useMobileSheetSnap('bar', { onDismiss })
    );

    drag(result.current.handleProps, 300, 420);
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(result.current.dragHeight).toBeNull();
  });

  it('does not dismiss from the planning height', () => {
    const onDismiss = vi.fn();
    const { result } = renderHook(() =>
      useMobileSheetSnap('peek', { onDismiss })
    );

    drag(result.current.handleProps, 300, 420);
    expect(onDismiss).not.toHaveBeenCalled();
    expect(result.current.snap).toBe('bar');
  });

  it('drops the gesture on a cancelled drag instead of leaving it stuck', () => {
    const onDismiss = vi.fn();
    const { result } = renderHook(() =>
      useMobileSheetSnap('bar', { onDismiss })
    );

    act(() => result.current.handleProps.onPointerDown(at(300)));
    act(() => result.current.handleProps.onPointerMove(at(200)));
    act(() => result.current.handleProps.onPointerCancel());

    expect(result.current.dragHeight).toBeNull();
    expect(result.current.dragging).toBe(false);
    act(() => result.current.handleProps.onPointerUp(at(200)));
    expect(onDismiss).not.toHaveBeenCalled();
    expect(result.current.snap).toBe('bar');
  });

  it('swallows the tap a drag would otherwise end with', () => {
    const { result } = renderHook(() => useMobileSheetSnap('peek'));

    act(() => {
      result.current.handleProps.onPointerDown(at(800));
      result.current.handleProps.onPointerMove(at(700));
      result.current.handleProps.onPointerUp(at(700));
      result.current.handleProps.onClick();
    });
    expect(result.current.snap).toBe('full');
  });

  it('treats a finger that never moved as a tap, not as a drag', () => {
    const onDismiss = vi.fn();
    const { result } = renderHook(() =>
      useMobileSheetSnap('peek', { onDismiss })
    );

    act(() => {
      result.current.handleProps.onPointerDown(at(800));
      result.current.handleProps.onPointerMove(at(799));
      result.current.handleProps.onPointerUp(at(799));
    });

    expect(result.current.dragHeight).toBeNull();
    expect(onDismiss).not.toHaveBeenCalled();
    expect(result.current.snap).toBe('peek');

    act(() => result.current.handleProps.onClick());
    expect(result.current.snap).toBe('full');
  });

  it('keeps every height in one place, the guide strip included', () => {
    expect(MOBILE_SHEET_HEIGHT).toEqual({
      bar: '132px',
      peek: '39dvh',
      full: '90dvh',
    });
    expect(MOBILE_GUIDE_HEIGHT).toBe('3.5rem');
  });

  it('turns the CSS heights into the pixels the drag maths needs', () => {
    const vh = window.innerHeight;
    expect(snapPx('bar')).toBe(132);
    expect(snapPx('peek')).toBe(Math.round(vh * 0.39));
    expect(snapPx('full')).toBe(Math.round(vh * 0.9));
    expect(guidePx()).toBe(56);
    expect(snapPx('bar')).toBeLessThan(snapPx('peek'));
    expect(snapPx('peek')).toBeLessThan(snapPx('full'));
  });
});
