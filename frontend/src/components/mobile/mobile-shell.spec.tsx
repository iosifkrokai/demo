import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  cleanup,
  fireEvent,
  act,
} from '@testing-library/react';

import { SheetTitle } from '@/components/ui/sheet';
import { SheetDragHandle } from '@/components/parts/sheet-snap';
import type { SheetHandleProps } from '@/components/parts/sheet-snap';

import { MobileShell } from './mobile-shell';

/** The panel state the shell reads; individual tests flip it. */
const mockCommon = vi.hoisted(() => ({
  guiding: false,
  directionsPanelOpen: true,
}));

const mockSetOpen = vi.hoisted(() => vi.fn());
const mockNavigate = vi.hoisted(() => vi.fn());

// Only the hook is faked: the real module also exports schemas the rest of the
// app reads at import time, and dropping them breaks the whole graph.
vi.mock('@/stores/common-store', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/stores/common-store')>();
  return {
    ...actual,
    useCommonStore: (selector: (s: Record<string, unknown>) => unknown) =>
      selector({
        guiding: mockCommon.guiding,
        directionsPanelOpen: mockCommon.directionsPanelOpen,
        setDirectionsPanelOpen: mockSetOpen,
      }),
  };
});

// Partial: the real module also builds the route tree the store imports, so
// only `useNavigate` is faked.
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@tanstack/react-router')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

/**
 * The panel's contents; a stub keeps this spec about the shell's geometry. It
 * takes the drag handlers as the real panel does, so these tests can grab the
 * sheet's actual grab bar rather than a stand-in for it.
 */
const stubPanel = (handle: SheetHandleProps) => (
  <>
    <SheetTitle>Панель</SheetTitle>
    <p>содержимое</p>
    <SheetDragHandle snap="peek" handleProps={handle} />
  </>
);

describe('MobileShell', () => {
  beforeEach(() => {
    mockCommon.guiding = false;
    mockCommon.directionsPanelOpen = true;
    mockSetOpen.mockClear();
    mockNavigate.mockClear();
    document.documentElement.style.removeProperty('--sheet-h');
  });

  // The hook reads `performance.now()` for the release speed, so these tests
  // own the clock: with the real one every gesture inside jsdom lands in a
  // single millisecond and every release reads as a flick.
  beforeEach(() => vi.useFakeTimers());
  // `cleanup()` explicitly, and not because this spec needs it: vitest here runs
  // without `globals: true`, so Testing Library's automatic afterEach never
  // registers, and this file's sheet is portalled into the shared document —
  // where it would answer the next spec's queries.
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows the panel it is given, at the peek height', () => {
    render(<MobileShell panel={stubPanel} />);

    expect(screen.getByText('содержимое')).toBeInTheDocument();
    const sheet = screen.getByTestId('mobile-sheet');
    expect(sheet).toHaveAttribute('data-snap', 'peek');
    expect(sheet).toHaveStyle({ height: '39dvh' });
  });

  it('publishes its own height, so the map controls ride above it', () => {
    // The map's floating controls are siblings of the sheet: the custom
    // property on the document is the only channel they share (the desktop
    // panel publishes it too — the shell takes over that job on a phone).
    render(<MobileShell panel={stubPanel} />);
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '39dvh'
    );
  });

  it('drops to the guide strip while walking', () => {
    mockCommon.guiding = true;
    render(<MobileShell panel={stubPanel} />);
    expect(screen.getByTestId('mobile-sheet')).toHaveStyle({
      height: '26dvh',
    });
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '26dvh'
    );
  });

  it('folds down to the strip when the panel is closed', () => {
    mockCommon.directionsPanelOpen = false;
    render(<MobileShell panel={stubPanel} />);
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '132px'
    );
  });

  it('opens a test straight into the position it is asked for', () => {
    render(<MobileShell panel={stubPanel} initialSnap="full" />);
    const sheet = screen.getByTestId('mobile-sheet');
    expect(sheet).toHaveAttribute('data-snap', 'full');
    expect(sheet).toHaveStyle({ height: '90dvh' });
  });

  it('clears the published height when it goes away', () => {
    render(<MobileShell panel={stubPanel} />);
    cleanup();
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '0px'
    );
  });

  // ── The way in and out on a phone ─────────────────────────────────────────

  it('открывается кнопкой на карте, а не шевроном у края панели', () => {
    // The desktop chevron stands on the panel's own vertical edge. On a phone
    // the panel is a sheet across the bottom, so there is no such edge and the
    // chevron ended up floating in the middle of the map. The way in here is a
    // button on the map, as in a phone map app.
    mockCommon.directionsPanelOpen = false;
    render(<MobileShell panel={stubPanel} />);

    const open = screen.getByTestId('mobile-panel-open');
    expect(open).toHaveTextContent('Планировать маршрут');
    // Above the sheet, by the same variable the map's own controls ride on, so
    // it can never end up underneath it.
    expect(screen.getByTestId('mobile-sheet-opener').className).toContain(
      '--sheet-h'
    );
    // And phone-only: the desktop column keeps the chevron.
    expect(screen.getByTestId('mobile-sheet-opener').className).toContain(
      'md:hidden'
    );
  });

  it('кнопка открывает панель и приводит план в виду', () => {
    mockCommon.directionsPanelOpen = false;
    render(<MobileShell panel={stubPanel} />);

    fireEvent.click(screen.getByTestId('mobile-panel-open'));

    expect(mockSetOpen).toHaveBeenCalledWith(true);
    expect(mockNavigate).toHaveBeenCalledWith({
      params: { activeTab: 'directions' },
    });
  });

  it('кнопки нет, пока панель уже на экране', () => {
    render(<MobileShell panel={stubPanel} />);
    expect(screen.queryByTestId('mobile-panel-open')).not.toBeInTheDocument();
  });

  it('флик вниз с полоски закрывает панель — кнопку искать не надо', () => {
    // Sheet gestures own open/close on a phone. `setDirectionsPanelOpen(false)`
    // rather than a toggle: a dismiss has to close, whatever else closed it
    // first.
    // Open, resting on the strip — the only place a downward flick can close.
    render(<MobileShell panel={stubPanel} initialSnap="bar" />);

    const grip = screen.getByTestId('sheet-handle');
    act(() => {
      fireEvent.pointerDown(grip, { clientY: 300, pointerId: 1 });
      for (let i = 1; i <= 3; i++) {
        vi.advanceTimersByTime(10);
        fireEvent.pointerMove(grip, { clientY: 300 + i * 40, pointerId: 1 });
      }
      vi.advanceTimersByTime(10);
      fireEvent.pointerUp(grip, { clientY: 420, pointerId: 1 });
    });

    expect(mockSetOpen).toHaveBeenCalledWith(false);
  });

  it('высота следует за пальцем, и переход при этом выключен', () => {
    // An animated height on top of a per-move rewrite is what made the panel
    // feel like it was being dragged through something: the sheet trailed the
    // glass by a fifth of a second.
    render(<MobileShell panel={stubPanel} />);
    const grip = screen.getByTestId('sheet-handle');

    act(() => {
      fireEvent.pointerDown(grip, { clientY: 800, pointerId: 1 });
      vi.advanceTimersByTime(10);
      fireEvent.pointerMove(grip, { clientY: 700, pointerId: 1 });
    });

    const sheet = screen.getByTestId('mobile-sheet');
    expect(sheet).toHaveAttribute('data-dragging', 'true');
    // A px height, not one of the three resting values: this is the finger's.
    expect(sheet.style.height).toMatch(/^\d+px$/);
    expect(sheet.style.height).not.toBe('39dvh');
    expect(sheet.className).toContain('data-[dragging=true]:transition-none');

    act(() => {
      vi.advanceTimersByTime(10);
      fireEvent.pointerUp(grip, { clientY: 700, pointerId: 1 });
    });
    // And it settles back onto a position afterwards.
    expect(sheet).toHaveAttribute('data-dragging', 'false');
  });

  it('показывает планирование, а не то, чем панель закрыли', () => {
    // Dismissing is a «put this away» gesture; coming back to the full height
    // the tourist just swiped away would read as the panel refusing to go.
    const { rerender } = render(
      <MobileShell panel={stubPanel} initialSnap="full" />
    );
    expect(screen.getByTestId('mobile-sheet')).toHaveAttribute(
      'data-snap',
      'full'
    );

    mockCommon.directionsPanelOpen = false;
    rerender(<MobileShell panel={stubPanel} initialSnap="full" />);
    mockCommon.directionsPanelOpen = true;
    rerender(<MobileShell panel={stubPanel} initialSnap="full" />);

    expect(screen.getByTestId('mobile-sheet')).toHaveAttribute(
      'data-snap',
      'peek'
    );
  });
});
