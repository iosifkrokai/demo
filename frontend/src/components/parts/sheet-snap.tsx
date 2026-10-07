import { useCallback, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
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
 * carrying only the grab handle and «выход» (a drag still opens it fully). The
 * turn and the next stop live in the portaled HUD, not here.
 *
 * Measured on 390x844: at the old 26dvh (219px) the sheet started at y=625 and
 * the navigator showed 23 % of the map. Its own body was 113px of empty space —
 * `GuidePanel` renders nothing but the portal in moving mode — so the strip is
 * now sized to the grab handle only; the guide sheet is transparent while
 * collapsed so the map remains visible to the bottom edge.
 */
export const GUIDE_SHEET_CLASS = 'h-14 md:hidden';

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
 *
 * The target is now the **full width** of the sheet, not a 96px pill in the
 * middle. A 44px-tall, 96px-wide target is legal and still awkward: a thumb
 * resting slightly off centre misses it entirely, and the miss costs the whole
 * gesture. The visible line stays exactly as it was — a wide, invisible, easy
 * target around it is what a phone sheet wants. Nothing else in the sheet is
 * inside this row, so the extra width costs no other control.
 */
export const SheetDragHandle = ({
  snap,
  handleProps,
}: SheetDragHandleProps) => {
  const { t } = useTranslation();
  const Chevron = snap === 'full' ? ChevronDown : ChevronUp;
  return (
    <div className="relative flex justify-center pt-1 md:hidden">
      <button
        type="button"
        data-testid="sheet-handle"
        aria-expanded={snap === 'full'}
        aria-label={snap === 'full' ? t('panel.collapse') : t('panel.expand')}
        title={t('panel.dragHint')}
        className={cn(
          'flex h-11 w-full touch-none items-center justify-center rounded-full transition-colors',
          'hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50'
        )}
        {...handleProps}
      >
        <span className="h-1 w-9 rounded-full bg-border" />
        <Chevron
          className="ml-2 size-4 text-muted-foreground"
          aria-hidden="true"
        />
      </button>
    </div>
  );
};
