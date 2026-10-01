import { useCallback, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export type SheetSnap = 'peek' | 'full';

/** DESIGN.md: the mobile sheet snaps to ~50% and ~90% of the viewport. From md up
 * the panel is a full-height column again (`md:h-auto` + `md:inset-0`), otherwise
 * the snap height would leave the desktop sidebar 45 % tall.
 *
 * Measured at 390x844 with the old 45dvh peek: the form's scroll window came out
 * 36px high — the budget presets were cut off mid-row — while the map above kept
 * 55% of the screen for a preview nobody reads during planning. Half and half is
 * what a phone map app does, and it buys the panel ~80px of room. */
export const SHEET_SNAP_CLASS: Record<SheetSnap, string> = {
  peek: 'h-[50dvh] md:h-auto',
  full: 'h-[90dvh] md:h-auto',
};

/**
 * While the guide runs, the map IS the navigator, so the sheet drops to a strip
 * that still shows the turn and the next stop (a drag still opens it fully).
 *
 * Measured on 390x844: at the peek height the sheet started at y=464 and covered
 * every map control — the guide's own compass and follow buttons sat at y=640
 * and y=704, i.e. behind it, and the compass overlapped the services button by
 * 32px on top of that. A navigator whose map is a quarter of the screen and
 * whose controls are unreachable is not a navigator.
 */
export const GUIDE_SHEET_CLASS = 'h-[26dvh] md:h-auto';

/**
 * The panel itself: a bottom sheet under 768px (the map stays visible above it)
 * and a resizable column (420px by default) from 768px up. Built on the shared `Sheet` primitive, so
 * these classes only re-shape it — `cn()` (tailwind-merge) lets them win over
 * the primitive's own `inset-y-0 h-full w-3/4` defaults.
 */
export const PANEL_SHEET_CLASS = [
  'flex flex-col gap-0 overflow-hidden p-0',
  // mobile: bottom sheet
  'inset-x-0 bottom-0 top-auto w-full sm:max-w-none',
  'rounded-t-3xl border-t border-border',
  'shadow-sheet',
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
  'data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom',
  // desktop: a column whose width the tourist sets (`--panel-width`, see
  // panel-resize.tsx). The variable only applies from md up, so the mobile
  // sheet keeps the full viewport width.
  'md:inset-0 md:w-[var(--panel-width,420px)] md:max-w-[var(--panel-width,420px)]',
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
 *
 * Measured on 390x844 before this: the bar was 56x16 — a 16px-tall target for
 * the only control that changes the sheet's height — and the way out of the open
 * panel was the 24px chevron on the map's far edge, i.e. the tourist had to
 * leave the panel to find the control that closes it. Now the bar is a 44px
 * target (the line itself stays thin) and the sheet carries its own ✕.
 */
export const SheetDragHandle = ({
  snap,
  handleProps,
}: SheetDragHandleProps) => {
  const { t } = useTranslation();
  return (
    <div className="relative flex justify-center pt-1 md:hidden">
      <button
        type="button"
        data-testid="sheet-handle"
        aria-expanded={snap === 'full'}
        aria-label={snap === 'full' ? t('panel.collapse') : t('panel.expand')}
        title={t('panel.dragHint')}
        className={cn(
          'flex h-11 w-24 touch-none items-center justify-center rounded-full transition-colors',
          'hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50'
        )}
        {...handleProps}
      >
        <span className="h-1 w-9 rounded-full bg-border" />
      </button>
    </div>
  );
};
