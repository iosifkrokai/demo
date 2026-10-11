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
import { MOBILE_GUIDE_HEIGHT } from './use-mobile-sheet-snap';

/** The panel state the shell reads; individual tests flip it. */
const mockCommon = vi.hoisted(() => ({
  guiding: false,
  directionsPanelOpen: true,
}));

const mockSetOpen = vi.hoisted(() => vi.fn());
const mockNavigate = vi.hoisted(() => vi.fn());

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

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@tanstack/react-router')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

/** The panel's contents; a stub keeps this spec about the shell's geometry. */
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

  beforeEach(() => vi.useFakeTimers());
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
    render(<MobileShell panel={stubPanel} />);
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '39dvh'
    );
  });

  it('drops to the guide strip while walking', () => {
    mockCommon.guiding = true;
    render(<MobileShell panel={stubPanel} />);
    expect(screen.getByTestId('mobile-sheet')).toHaveStyle({
      height: MOBILE_GUIDE_HEIGHT,
    });
    expect(screen.getByTestId('mobile-sheet')).toHaveClass(
      'bg-transparent',
      'border-t-transparent',
      'shadow-none'
    );
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      MOBILE_GUIDE_HEIGHT
    );
  });

  it('expands the navigator sheet to a full route overview', () => {
    mockCommon.guiding = true;
    render(<MobileShell panel={stubPanel} />);

    fireEvent.click(screen.getByTestId('sheet-handle'));

    expect(screen.getByTestId('mobile-sheet')).toHaveAttribute(
      'data-snap',
      'full'
    );
    expect(screen.getByTestId('mobile-sheet')).toHaveStyle({ height: '90dvh' });
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      '90dvh'
    );
  });

  it('сложится в полоску, если в проводник войти с развёрнутого листа', () => {
    const { rerender } = render(<MobileShell panel={stubPanel} />);

    fireEvent.click(screen.getByTestId('sheet-handle'));
    expect(screen.getByTestId('mobile-sheet')).toHaveAttribute(
      'data-snap',
      'full'
    );

    mockCommon.guiding = true;
    rerender(<MobileShell panel={stubPanel} />);

    const sheet = screen.getByTestId('mobile-sheet');
    expect(sheet).toHaveStyle({ height: MOBILE_GUIDE_HEIGHT });
    expect(document.documentElement.style.getPropertyValue('--sheet-h')).toBe(
      MOBILE_GUIDE_HEIGHT
    );
  });

  it('разворот в обзор маршрута во время ведения не сбрасывается', () => {
    mockCommon.guiding = true;
    render(<MobileShell panel={stubPanel} />);

    fireEvent.click(screen.getByTestId('sheet-handle'));

    expect(screen.getByTestId('mobile-sheet')).toHaveAttribute(
      'data-snap',
      'full'
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

  it('открывается кнопкой на карте, а не шевроном у края панели', () => {
    mockCommon.directionsPanelOpen = false;
    render(<MobileShell panel={stubPanel} />);

    const open = screen.getByTestId('mobile-panel-open');
    expect(open).toHaveTextContent('Планировать маршрут');
    expect(screen.getByTestId('mobile-sheet-opener').className).toContain(
      '--sheet-h'
    );
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
    render(<MobileShell panel={stubPanel} />);
    const grip = screen.getByTestId('sheet-handle');

    act(() => {
      fireEvent.pointerDown(grip, { clientY: 800, pointerId: 1 });
      vi.advanceTimersByTime(10);
      fireEvent.pointerMove(grip, { clientY: 700, pointerId: 1 });
    });

    const sheet = screen.getByTestId('mobile-sheet');
    expect(sheet).toHaveAttribute('data-dragging', 'true');
    expect(sheet.style.height).toMatch(/^\d+px$/);
    expect(sheet.style.height).not.toBe('39dvh');
    expect(sheet.className).toContain('data-[dragging=true]:transition-none');

    act(() => {
      vi.advanceTimersByTime(10);
      fireEvent.pointerUp(grip, { clientY: 700, pointerId: 1 });
    });
    expect(sheet).toHaveAttribute('data-dragging', 'false');
  });

  it('показывает планирование, а не то, чем панель закрыли', () => {
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
