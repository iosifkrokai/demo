import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

/** The three positions of the mobile panel, from least to most screen given to it. */
export type MobileSnap = 'bar' | 'peek' | 'full';

/**
 * What the shell publishes as `--sheet-h`, so the map's own controls ride above it.
 *
 * `peek` was 50dvh, then 45dvh while the panel was still dense; it is 39dvh now
 * that Ф2 (density) shrank the fixed blocks, and every step was measured:
 *   - before density: header 140 + ask 133 + footer 72 = 345px fixed, and at a
 *     321px sheet the main action was clipped by 21px;
 *   - after density (chips 40, field 40, paddings 12, RU/EN 44x44, tighter
 *     header/ask/footer): 287px fixed, i.e. a 42px scroll window at 39dvh —
 *     map 61%, the plan's >= 60%, with the action still inside the sheet.
 */
export const MOBILE_SHEET_HEIGHT: Record<MobileSnap, string> = {
  bar: '132px',
  peek: '39dvh',
  full: '90dvh',
};

/** While the guide runs the map IS the navigator: the panel is a strip that
 * still carries the next turn and the next stop. Same value the desktop sheet
 * uses (`GUIDE_SHEET_CLASS`) — see the measurement note there. */
export const MOBILE_GUIDE_HEIGHT = '26dvh';

const ORDER: MobileSnap[] = ['bar', 'peek', 'full'];

// ── Gesture tuning ─────────────────────────────────────────────────────────
// Four numbers, each with a reason. They are the difference between a sheet
// that feels bolted to the finger and one that jumps between fixed heights a
// quarter-second after the finger has already stopped.

/** Below this the pointer is treated as a tap, so a tap never nudges the sheet. */
const TAP_SLOP_PX = 4;

/**
 * Past this speed the release is a *flick*: it moves exactly one position
 * however short it was. Below it the position is chosen by distance, so a slow
 * careful drag still lands where it was taken.
 */
const FLICK_PX_PER_MS = 0.5;

/**
 * How long the sheet is allowed to keep travelling after the finger leaves the
 * glass. This is what makes a short fast flick reach a position 300px away, and
 * it is why the position no longer has to be dragged all the way.
 */
const PROJECTION_MS = 120;

/** Past the outermost position the sheet gives ground slowly instead of not at all. */
const RESISTANCE = 0.3;

/**
 * Thrown this far below the strip and the release closes the panel — the way a
 * sheet is dismissed everywhere else on a phone, where there is no separate
 * close button to find.
 */
const DISMISS_RATIO = 0.6;

const viewportHeight = (): number =>
  typeof window === 'undefined' || !window.innerHeight
    ? 800
    : window.innerHeight;

const now = (): number =>
  typeof performance !== 'undefined' ? performance.now() : Date.now();

/**
 * The positions in pixels. Dragging is arithmetic on distances, so it cannot be
 * done on the CSS strings above: `39dvh` has to become a number once, in one
 * place, or the rubber band and the flick projection would each guess.
 */
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
export const guidePx = (): number => Math.round(viewportHeight() * 0.26);

/**
 * The height a drag is allowed to reach, with the pull past the outermost
 * position damped rather than refused: a sheet that stops dead under the finger
 * is the "clumsy" feeling this whole function exists to remove.
 */
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

/**
 * How fast the finger is travelling, px per ms, positive when it is going up.
 *
 * Measured over a short trailing window of samples rather than over the whole
 * drag, or over the last two samples alone. Both of those were tried and both
 * are wrong in a way a finger notices:
 *
 * * Whole drag: a long careful drag that ends in a rush averages out to
 *   "slow", and a 20px flick averages out to "slow" too — so the gesture the
 *   sheet is built for is the one it ignores.
 * * Last two samples: browsers deliver a `pointermove` a millisecond or two
 *   before `pointerup`, often after the finger has already stopped. A 1px step
 *   in 1ms reads as 1 px/ms and every release becomes a flick.
 *
 * A window gets both right: the samples that fell out of it are the ones from
 * before the finger settled, and a finger that held still long enough has no
 * samples left at all, so its speed is genuinely zero.
 *
 * The interval is floored at 1ms: two samples in the same millisecond would
 * divide by zero and hand the release an infinite velocity.
 */
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
  /**
   * The height the sheet is resting at right now, in px — the guide strip while
   * walking, the position's own height otherwise. Read when the gesture starts,
   * so a drag begun from the strip does not first jump to 39dvh.
   */
  restingPx?: number;
  /** A flick below the outermost position closes the panel. */
  onDismiss?: () => void;
}

/**
 * The mobile panel's position, owned by the shell rather than by the panel's
 * contents: the same content is shown at all three heights, only the geometry
 * changes.
 *
 * How it feels is the point of this hook, so the rules are these:
 *
 * * **The sheet follows the finger.** Every move sets the height from the
 *   pointer, and the shell drops its CSS transition while `dragging` is true —
 *   an animated height would lag behind the glass and read as lag, not motion.
 * * **The pointer is captured**, so a drag that runs off the 44px grab bar
 *   keeps being tracked instead of stopping dead at its edge.
 * * **Pulling past the end gives ground slowly**, which is the difference
 *   between "it is full" and "it is stuck".
 * * **A flick is a flick.** Fast, it moves one position however short it was,
 *   projected forward by `PROJECTION_MS`; slow, it lands nearest where the
 *   finger let go. Before this a move from the strip to the full height had to
 *   be dragged 96px, which is why moving the panel felt like work.
 * * **A flick down from the strip closes it**, so no separate close button has
 *   to be found on a phone.
 * * **A tap cycles the positions** and the drag swallows the tap it ends with.
 */
export const useMobileSheetSnap = (
  initial: MobileSnap = 'peek',
  { restingPx, onDismiss }: MobileSheetSnapOptions = {}
): MobileSheetSnap => {
  const [snap, setSnapState] = useState<MobileSnap>(initial);
  const [dragHeight, setDragHeight] = useState<number | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const swallowClick = useRef(false);

  // Latest values for the gesture, which starts long after the render that
  // created the handlers: `snap` decides which position a release steps from,
  // the resting height is the guide strip while walking, and the dismiss
  // callback may be a fresh closure each render.
  //
  // Synced in an effect rather than during render: these are read only inside
  // pointer handlers, which always run after the commit, so an effect is late
  // enough — and writing a ref during render is what makes React drop a render
  // whose only output was that write.
  const latest = useRef({ snap, restingPx, onDismiss });
  useEffect(() => {
    latest.current = { snap, restingPx, onDismiss };
  });

  const snapRef = useRef<MobileSnap>(snap);
  const setSnap = useCallback((next: MobileSnap) => {
    snapRef.current = next;
    setSnapState(next);
  }, []);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    // Capture the pointer: without it a drag that outruns the grab bar stops
    // being tracked the moment the finger crosses its edge, which is most of
    // the movement in a sheet drag.
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    gesture.current = {
      originY: event.clientY,
      originPx: latest.current.restingPx ?? snapPx(snapRef.current),
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
      if (!g) return;

      if (!g.moved) {
        setDragHeight(null);
        return; // a plain tap — the click handler owns it
      }
      swallowClick.current = true;

      const endY = event?.clientY ?? g.samples[g.samples.length - 1]?.y ?? 0;
      // The speed that counts is the speed the finger had when it *left* the
      // glass, so it is sampled here rather than reused from the last move: a
      // sheet that was flicked and then held for a moment was not flicked.
      const releaseVelocity = velocity(g.samples, endY, now());

      const index = ORDER.indexOf(snapRef.current);
      const height = g.originPx + (g.originY - endY);
      const projected =
        withResistance(height) + releaseVelocity * PROJECTION_MS;
      const min = snapPx('bar');

      // A flick down at the lowest position, or a pull most of the way off it,
      // dismisses. Above that it is just the next position down.
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
        next = ORDER.reduce((best, candidate) =>
          Math.abs(snapPx(candidate) - projected) <
          Math.abs(snapPx(best) - projected)
            ? candidate
            : best
        );
      }

      setDragHeight(null);
      setSnap(next);
    },
    [setSnap]
  );

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => release(event),
    [release]
  );

  const onPointerCancel = useCallback(() => {
    gesture.current = null;
    setDragHeight(null);
  }, []);

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
