import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

/** The three positions of the mobile panel, from least to most screen given to it. */
export type MobileSnap = 'bar' | 'peek' | 'full';

/** What the shell publishes as `--sheet-h`, so the map's own controls ride above it. */
export const MOBILE_SHEET_HEIGHT: Record<MobileSnap, string> = {
  bar: '132px',
  peek: '39dvh',
  full: '90dvh',
};

/** The guide sheet is transparent while collapsed; only its grab handle remains. */
export const MOBILE_GUIDE_HEIGHT = '3.5rem';

const ORDER: MobileSnap[] = ['bar', 'peek', 'full'];

/** Below this the pointer is treated as a tap, so a tap never nudges the sheet. */
const TAP_SLOP_PX = 4;

/** Past this speed the release is a *flick*: it moves exactly one position however short it was. */
const FLICK_PX_PER_MS = 0.5;

/** How long the sheet is allowed to keep travelling after the finger leaves the glass. */
const PROJECTION_MS = 120;

/** Past the outermost position the sheet gives ground slowly instead of not at all. */
const RESISTANCE = 0.3;

/** Thrown this far below the strip and the release closes the panel — the way a sheet is dismissed everywhere else on a phone, where there is no separate close button to find. */
const DISMISS_RATIO = 0.6;

const viewportHeight = (): number =>
  typeof window === 'undefined' || !window.innerHeight
    ? 800
    : window.innerHeight;

const now = (): number =>
  typeof performance !== 'undefined' ? performance.now() : Date.now();

/** The positions in pixels. */
export const snapPx = (snap: MobileSnap): number => {
  const vh = viewportHeight();
  switch (snap) {
    case 'bar':
      return 132;
    case 'peek':
      return Math.round(vh * 0.39);
    case 'full':
      return Math.round(vh * 0.9);
  }
};

/** The guide strip in pixels — the height the drag starts from while walking. */
export const guidePx = (): number => 56;

/** The height a drag is allowed to reach, with the pull past the outermost position damped rather than refused: a sheet that stops dead under the finger is the "clumsy" feeling this whole function exists to remove. */
const withResistance = (raw: number): number => {
  const min = snapPx('bar');
  const max = snapPx('full');
  if (raw > max) return max + (raw - max) * RESISTANCE;
  if (raw < min) return min + (raw - min) * RESISTANCE;
  return raw;
};

interface Gesture {
  /** Where the finger went down. */
  originY: number;
  /** The height the sheet rested at, so the drag starts from where it looked. */
  originPx: number;
  /** The last `VELOCITY_WINDOW_MS` of pointer positions, for the release speed. */
  samples: Sample[];
  /** Past `TAP_SLOP_PX` this is a drag, not a tap. */
  moved: boolean;
}

/** How fast the finger is travelling, px per ms, positive when it is going up. */
const VELOCITY_WINDOW_MS = 100;

interface Sample {
  y: number;
  at: number;
}

const velocity = (samples: Sample[], endY: number, endAt: number): number => {
  const from = endAt - VELOCITY_WINDOW_MS;
  const recent = samples.filter((s) => s.at >= from);
  const first = recent[0];
  if (!first) return 0;
  const dt = Math.max(1, endAt - first.at);
  return (first.y - endY) / dt;
};

export interface MobileSheetSnap {
  snap: MobileSnap;
  /** The resting height of the current position, as CSS. */
  height: string;
  /** The height under the finger right now, in px; null while it is not being dragged. */
  dragHeight: number | null;
  dragging: boolean;
  setSnap: (next: MobileSnap) => void;
  handleProps: {
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
    onPointerCancel: () => void;
    onClick: () => void;
  };
}

export interface MobileSheetSnapOptions {
  /** The height the sheet is resting at right now, in px — the guide strip while walking, the position's own height otherwise. */
  restingPx?: number;
  /** A flick below the outermost position closes the panel. */
  onDismiss?: () => void;
}

/** The mobile panel's position, owned by the shell rather than by the panel's contents: the same content is shown at all three heights, only the geometry changes. */
export const useMobileSheetSnap = (
  initial: MobileSnap = 'peek',
  { restingPx, onDismiss }: MobileSheetSnapOptions = {}
): MobileSheetSnap => {
  const [snap, setSnapState] = useState<MobileSnap>(initial);
  const [dragHeight, setDragHeight] = useState<number | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const swallowClick = useRef(false);

  const latest = useRef({ snap, restingPx, onDismiss });
  useEffect(() => {
    latest.current = { snap, restingPx, onDismiss };
  }, [snap, restingPx, onDismiss]);

  const snapRef = useRef<MobileSnap>(snap);
  const setSnap = useCallback((next: MobileSnap) => {
    snapRef.current = next;
    setSnapState(next);
  }, []);

  /** The element this gesture captured the pointer on, so the capture can be given back. */
  const captured = useRef<{ target: HTMLElement; pointerId: number } | null>(
    null
  );

  const releaseCapture = useCallback(() => {
    const held = captured.current;
    captured.current = null;
    if (held?.target.hasPointerCapture?.(held.pointerId)) {
      held.target.releasePointerCapture(held.pointerId);
    }
  }, []);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const target = event.currentTarget;
    if (target?.setPointerCapture) {
      target.setPointerCapture(event.pointerId);
      captured.current = { target, pointerId: event.pointerId };
    }
    gesture.current = {
      originY: event.clientY,
      originPx:
        snapRef.current === 'peek'
          ? (latest.current.restingPx ?? snapPx(snapRef.current))
          : snapPx(snapRef.current),
      samples: [{ y: event.clientY, at: now() }],
      moved: false,
    };
  }, []);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g) return;
    g.samples.push({ y: event.clientY, at: now() });

    const travelled = g.originY - event.clientY;
    if (!g.moved && Math.abs(travelled) < TAP_SLOP_PX) return;
    g.moved = true;
    setDragHeight(withResistance(g.originPx + travelled));
  }, []);

  const release = useCallback(
    (event: ReactPointerEvent<HTMLElement> | null) => {
      const g = gesture.current;
      gesture.current = null;
      releaseCapture();
      if (!g) return;

      if (!g.moved) {
        setDragHeight(null);
        return;
      }
      swallowClick.current = true;

      const endY = event?.clientY ?? g.samples[g.samples.length - 1]?.y ?? 0;
      const releaseVelocity = velocity(g.samples, endY, now());

      const index = ORDER.indexOf(snapRef.current);
      const height = g.originPx + (g.originY - endY);
      const projected =
        withResistance(height) + releaseVelocity * PROJECTION_MS;
      const min = snapPx('bar');

      if (
        index === 0 &&
        (releaseVelocity <= -FLICK_PX_PER_MS || projected < min * DISMISS_RATIO)
      ) {
        setDragHeight(null);
        latest.current.onDismiss?.();
        return;
      }

      let next: MobileSnap;
      if (releaseVelocity >= FLICK_PX_PER_MS) {
        next = ORDER[Math.min(index + 1, ORDER.length - 1)] ?? 'full';
      } else if (releaseVelocity <= -FLICK_PX_PER_MS) {
        next = ORDER[Math.max(index - 1, 0)] ?? 'bar';
      } else {
        next = ORDER.reduce(
          (best, candidate) =>
            Math.abs(snapPx(candidate) - projected) <
            Math.abs(snapPx(best) - projected)
              ? candidate
              : best,
          ORDER[ORDER.length - 1] ?? 'full'
        );
      }

      setDragHeight(null);
      setSnap(next);
    },
    [setSnap, releaseCapture]
  );

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => release(event),
    [release]
  );

  const onPointerCancel = useCallback(() => {
    releaseCapture();
    gesture.current = null;
    setDragHeight(null);
  }, [releaseCapture]);

  const onClick = useCallback(() => {
    if (swallowClick.current) {
      swallowClick.current = false;
      return;
    }
    setSnap(
      ORDER[Math.min(ORDER.indexOf(snapRef.current) + 1, ORDER.length - 1)] ??
        'full'
    );
  }, [setSnap]);

  return {
    snap,
    height: MOBILE_SHEET_HEIGHT[snap],
    dragHeight,
    dragging: dragHeight !== null,
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
