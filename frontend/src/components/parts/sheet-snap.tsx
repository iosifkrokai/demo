import { useCallback, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export type SheetSnap = 'peek' | 'full';

/** DESIGN.md: the mobile sheet snaps to ~50% and ~90% of the viewport. */
export const SHEET_SNAP_CLASS: Record<SheetSnap, string> = {
  peek: 'h-[50dvh] md:h-auto',
  full: 'h-[90dvh] md:h-auto',
};

/** While the guide runs, the sheet drops to a strip with just the handle and «выход». */
export const GUIDE_SHEET_CLASS = 'h-14 md:hidden';

/** Panel layout: bottom sheet below 768px, resizable 420px column at and above. */
export const PANEL_SHEET_CLASS = [
  'flex flex-col gap-0 overflow-hidden p-0',
  'inset-x-0 bottom-0 top-auto w-full sm:max-w-none',
  'rounded-t-3xl border-t border-border',
  'shadow-sheet',
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
  'data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom',
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

/** The two snap points of the mobile sheet. */
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
    if (!state?.moved) return;
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

/** The grab bar of the bottom sheet. */
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
