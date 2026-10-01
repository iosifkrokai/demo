import { useCallback, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

/** The three positions of the mobile panel, from least to most screen given to it. */
export type MobileSnap = 'bar' | 'peek' | 'full';

/**
 * What the shell publishes as `--sheet-h`, so the map's own controls ride above it.
 *
 * `peek` is 45dvh, not the 38dvh the plan aims at, and this is measured, not
 * taste: with today's block sizes the panel's fixed parts (header 140 + ask 133
 * + footer 72 = 345px) do not fit a 321px sheet, so the main action was clipped
 * by 21px. Ф2 (density) brings those three blocks to ~256px — then the plan's
 * 38dvh (map 62%) fits with a real scroll window and this constant drops.
 */
export const MOBILE_SHEET_HEIGHT: Record<MobileSnap, string> = {
  bar: '132px',
  peek: '45dvh',
  full: '90dvh',
};

/** While the guide runs the map IS the navigator: the panel is a strip that
 * still carries the next turn and the next stop. Same value the desktop sheet
 * uses (`GUIDE_SHEET_CLASS`) — see the measurement note there. */
export const MOBILE_GUIDE_HEIGHT = '26dvh';

/** Drag further than this and the nearest position wins. */
const SNAP_DRAG_PX = 32;

const ORDER: MobileSnap[] = ['bar', 'peek', 'full'];

/** Where a release at `clientY` belongs, given the viewport and the current top edge. */
const nearestSnap = (from: number, to: number): MobileSnap => {
  // Dragging up (clientY decreases) opens the panel: bar -> peek -> full.
  if (from - to > SNAP_DRAG_PX * 3) return 'full';
  if (from - to > SNAP_DRAG_PX) return 'peek';
  if (to - from > SNAP_DRAG_PX) return 'bar';
  return 'peek';
};

export interface MobileSheetSnap {
  snap: MobileSnap;
  height: string;
  setSnap: (next: MobileSnap) => void;
  handleProps: {
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerCancel: () => void;
    onClick: () => void;
  };
}

/**
 * The mobile panel's position, owned by the shell rather than by the panel's
 * contents: the same content is shown at all three heights, only the geometry
 * changes. A tap on the handle steps to the next position (as in a phone map
 * app), a real drag picks the one the finger was released closest to, and the
 * drag swallows the click it would otherwise end with.
 */
export const useMobileSheetSnap = (
  initial: MobileSnap = 'peek'
): MobileSheetSnap => {
  const [snap, setSnap] = useState<MobileSnap>(initial);
  const drag = useRef<{ from: number; moved: boolean } | null>(null);
  const swallowClick = useRef(false);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    drag.current = { from: event.clientY, moved: false };
  }, []);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const state = drag.current;
    if (!state) return;
    if (Math.abs(state.from - event.clientY) >= SNAP_DRAG_PX)
      state.moved = true;
  }, []);

  const onPointerUp = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const state = drag.current;
    drag.current = null;
    if (!state?.moved) return; // a plain tap — the click handler owns it
    swallowClick.current = true;
    setSnap(nearestSnap(state.from, event.clientY));
  }, []);

  const onPointerCancel = useCallback(() => {
    drag.current = null;
  }, []);

  const onClick = useCallback(() => {
    if (swallowClick.current) {
      swallowClick.current = false;
      return;
    }
    setSnap(
      (prev) =>
        ORDER[Math.min(ORDER.indexOf(prev) + 1, ORDER.length - 1)] ?? 'full'
    );
  }, []);

  return {
    snap,
    height: MOBILE_SHEET_HEIGHT[snap],
    setSnap,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
      onClick,
    },
  };
};
