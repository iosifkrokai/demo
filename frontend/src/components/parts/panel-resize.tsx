import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';

import { cn } from '@/lib/utils';

/** The docked panel's width, the tourist's to choose. */
export const PANEL_WIDTH_STORAGE_KEY = 'grodno-panel-width';

/** Wider than the 380px it was fixed at — long stop names were wrapping. */
export const PANEL_WIDTH_DEFAULT = 420;
export const PANEL_WIDTH_MIN = 340;
export const PANEL_WIDTH_MAX = 720;

/** Arrow key nudge; holding Shift moves faster. */
const KEYBOARD_STEP = 16;
const KEYBOARD_STEP_LARGE = 64;

/** Never let the panel eat the map: at most this share of the viewport. */
const MAX_VIEWPORT_SHARE = 0.6;

/** Keep a requested width usable. */
export const clampPanelWidth = (
  width: number,
  viewportWidth: number = typeof window === 'undefined'
    ? PANEL_WIDTH_MAX
    : window.innerWidth
): number => {
  const ceiling = Math.max(
    PANEL_WIDTH_MIN,
    Math.min(PANEL_WIDTH_MAX, Math.round(viewportWidth * MAX_VIEWPORT_SHARE))
  );
  if (!Number.isFinite(width)) return PANEL_WIDTH_DEFAULT;
  return Math.round(Math.min(Math.max(width, PANEL_WIDTH_MIN), ceiling));
};

const readStoredWidth = (): number | null => {
  try {
    const raw = localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

const storeWidth = (width: number) => {
  try {
    localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(width));
  } catch {}
};

export interface PanelWidthHandleProps {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void;
  onDoubleClick: () => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void;
}

export interface PanelWidth {
  width: number;
  /** True while the edge is being dragged — used to drop the width transition. */
  resizing: boolean;
  setWidth: (width: number) => void;
  reset: () => void;
  handleProps: PanelWidthHandleProps;
}

/** Resizable panel width with pointer and keyboard control. */
export const usePanelWidth = (): PanelWidth => {
  const [width, setWidthState] = useState<number>(() =>
    clampPanelWidth(readStoredWidth() ?? PANEL_WIDTH_DEFAULT)
  );
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  const setWidth = useCallback((next: number) => {
    const clamped = clampPanelWidth(next);
    setWidthState(clamped);
    storeWidth(clamped);
  }, []);

  const reset = useCallback(() => setWidth(PANEL_WIDTH_DEFAULT), [setWidth]);

  useEffect(() => {
    const onResize = () => setWidthState((current) => clampPanelWidth(current));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      event.currentTarget.setPointerCapture?.(event.pointerId);
      drag.current = { startX: event.clientX, startWidth: width };
      setResizing(true);
    },
    [width]
  );

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (!drag.current) return;
      setWidth(drag.current.startWidth + (event.clientX - drag.current.startX));
    },
    [setWidth]
  );

  const endDrag = useCallback(() => {
    drag.current = null;
    setResizing(false);
  }, []);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
      if (event.key === 'ArrowLeft') {
        setWidth(width - step);
      } else if (event.key === 'ArrowRight') {
        setWidth(width + step);
      } else if (event.key === 'Home') {
        setWidth(PANEL_WIDTH_MIN);
      } else if (event.key === 'End') {
        setWidth(PANEL_WIDTH_MAX);
      } else {
        return;
      }
      event.preventDefault();
    },
    [setWidth, width]
  );

  return {
    width,
    resizing,
    setWidth,
    reset,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onDoubleClick: reset,
      onKeyDown,
    },
  };
};

/** The drag strip on the panel's right edge. */
export const PanelResizeHandle = ({
  width,
  resizing,
  props,
  label,
  className,
}: {
  width: number;
  resizing: boolean;
  props: PanelWidthHandleProps;
  label: string;
  className?: string;
}) => (
  <div
    role="separator"
    aria-orientation="vertical"
    aria-label={label}
    aria-valuenow={width}
    aria-valuemin={PANEL_WIDTH_MIN}
    aria-valuemax={PANEL_WIDTH_MAX}
    tabIndex={0}
    data-testid="panel-resize-handle"
    data-resizing={resizing ? 'true' : 'false'}
    {...props}
    className={cn(
      'absolute right-0 top-0 z-10 hidden h-full w-2 cursor-col-resize touch-none items-center justify-center md:flex',
      'group focus-visible:outline-none',
      className
    )}
  >
    <span
      aria-hidden="true"
      className={cn(
        'h-10 w-1 rounded-full bg-border transition-colors',
        'group-hover:bg-muted-foreground/60 group-focus-visible:bg-primary',
        resizing && 'bg-primary'
      )}
    />
  </div>
);
