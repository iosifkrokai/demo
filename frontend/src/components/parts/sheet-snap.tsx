import { useCallback, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { cn } from '@/lib/utils';

export type SheetSnap = 'peek' | 'full';

/** DESIGN.md: the mobile sheet snaps to ~45% and ~90% of the viewport. From md up
 * the panel is a full-height column again (`md:h-auto` + `md:inset-0`), otherwise
 * the snap height would leave the desktop sidebar 45 % tall. */
export const SHEET_SNAP_CLASS: Record<SheetSnap, string> = {
  peek: 'h-[45dvh] md:h-auto',
  full: 'h-[90dvh] md:h-auto',
};

/**
 * The panel itself: a bottom sheet under 768px (the map stays visible above it)
 * and a 380px column from 768px up. Built on the shared `Sheet` primitive, so
 * these classes only re-shape it — `cn()` (tailwind-merge) lets them win over
 * the primitive's own `inset-y-0 h-full w-3/4` defaults.
 */
export const PANEL_SHEET_CLASS = [
  'flex flex-col gap-0 overflow-hidden p-0',
  // mobile: bottom sheet
  'inset-x-0 bottom-0 top-auto w-full sm:max-w-none',
  'rounded-t-3xl border-t border-border',
  'shadow-[0_-8px_28px_rgba(0,0,0,0.12)]',
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
  'data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom',
  // desktop: 360–400px column
  'md:inset-0 md:w-[380px] md:max-w-[400px]',
  'md:rounded-none md:border-t-0 md:border-r md:shadow-none',
  'md:transition-none',
  'md:data-[state=open]:slide-in-from-left md:data-[state=closed]:slide-out-to-left',
].join(' ');

/** A drag of this many px moves the sheet to the other snap point. */
const SNAP_DRAG_PX = 32;

export interface SheetHandleProps {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerCancel: () => void;
  onClick: () => void;
}

/**
 * The two snap points of the mobile sheet. A tap on the handle toggles; a real
 * drag picks the point the finger was released closest to — and swallows the tap
 * such a drag would otherwise end with.
 */
export const useSheetSnap = (initial: SheetSnap = 'peek') => {
  const [snap, setSnap] = useState<SheetSnap>(initial);
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
    setSnap(state.from - event.clientY > 0 ? 'full' : 'peek');
  }, []);

  const onPointerCancel = useCallback(() => {
    drag.current = null;
  }, []);

  const onClick = useCallback(() => {
    if (swallowClick.current) {
      swallowClick.current = false;
      return;
    }
    setSnap((prev) => (prev === 'peek' ? 'full' : 'peek'));
  }, []);

  return {
    snap,
    isFull: snap === 'full',
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
      onClick,
    } satisfies SheetHandleProps,
  };
};

interface SheetDragHandleProps {
  snap: SheetSnap;
  handleProps: SheetHandleProps;
}

/**
 * The grab bar of the bottom sheet. Hidden on desktop, where the panel is a
 * full-height column with nothing to snap.
 */
export const SheetDragHandle = ({
  snap,
  handleProps,
}: SheetDragHandleProps) => (
  <div className="flex justify-center pt-2 md:hidden">
    <button
      type="button"
      aria-expanded={snap === 'full'}
      aria-label={snap === 'full' ? 'свернуть панель' : 'развернуть панель'}
      title="потянуть, чтобы развернуть"
      className={cn(
        'flex h-4 w-14 touch-none items-center justify-center rounded-full transition-colors',
        'hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50'
      )}
      {...handleProps}
    >
      <span className="h-1 w-9 rounded-full bg-border" />
    </button>
  </div>
);
