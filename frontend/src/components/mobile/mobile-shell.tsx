import { useEffect } from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { Sidebar } from '@/components/sidebar';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { useCommonStore } from '@/stores/common-store';

import {
  MOBILE_GUIDE_HEIGHT,
  MOBILE_SHEET_HEIGHT,
  useMobileSheetSnap,
} from './use-mobile-sheet-snap';
import type { MobileSnap } from './use-mobile-sheet-snap';

/**
 * The mobile panel host: it owns the geometry (three positions, the height the
 * map controls ride above, the strip used while walking) and renders the same
 * panel contents inside it. Contents do not know how tall they are — that is
 * what went wrong when the panel's own blocks grew and pushed the main action
 * out of the sheet (see docs/specs/004-mobile-first-frontend).
 *
 * Still a Radix sheet on purpose: the panel contains `SheetTitle`/`SheetDescription`,
 * which need the dialog context — and the dialog role/name is what screen
 * readers announce.
 */
const SHEET_CLASS = [
  'bg-background p-0 gap-0 overflow-hidden rounded-t-3xl border-t border-border shadow-sheet',
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
].join(' ');

export interface MobileShellProps {
  /** The panel's contents. A prop so the shell can be tested without the real panel. */
  panel?: ReactNode;
  /** Where the panel starts; a deep link or a test can open it fully. */
  initialSnap?: MobileSnap;
}

export const MobileShell = ({
  panel,
  initialSnap = 'peek',
}: MobileShellProps) => {
  const guiding = useCommonStore((s) => s.guiding);
  const panelOpen = useCommonStore((s) => s.directionsPanelOpen);
  const { snap, height, handleProps } = useMobileSheetSnap(initialSnap);

  // While walking, the map IS the navigator, so the sheet drops to a strip.
  const sheetHeight = guiding ? MOBILE_GUIDE_HEIGHT : height;

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty(
      '--sheet-h',
      panelOpen ? sheetHeight : MOBILE_SHEET_HEIGHT.bar
    );
  }, [sheetHeight, panelOpen]);

  useEffect(() => {
    const root = document.documentElement;
    return () => root.style.setProperty('--sheet-h', '0px');
  }, []);

  return (
    <Sheet open={panelOpen} modal={false}>
      <SheetContent
        side="bottom"
        data-testid="mobile-sheet"
        data-snap={snap}
        className={SHEET_CLASS}
        style={{ height: sheetHeight } as CSSProperties}
      >
        {panel ?? (
          <Sidebar
            bare
            // The three positions map onto the two the panel's own handle knows:
            // it only needs «expanded» to be true in the full one.
            snap={snap === 'full' ? 'full' : 'peek'}
            handleProps={handleProps}
            publishSheetHeight={false}
          />
        )}
      </SheetContent>
    </Sheet>
  );
};
