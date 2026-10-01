import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { SheetTitle } from '@/components/ui/sheet';

import { MobileShell } from './mobile-shell';

/** The panel state the shell reads; individual tests flip it. */
const mockCommon = vi.hoisted(() => ({
  guiding: false,
  directionsPanelOpen: true,
}));

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
      }),
  };
});

/** The panel's contents; a stub keeps this spec about the shell's geometry. */
const stubPanel = (
  <>
    <SheetTitle>Панель</SheetTitle>
    <p>содержимое</p>
  </>
);

describe('MobileShell', () => {
  beforeEach(() => {
    mockCommon.guiding = false;
    mockCommon.directionsPanelOpen = true;
    document.documentElement.style.removeProperty('--sheet-h');
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
});
