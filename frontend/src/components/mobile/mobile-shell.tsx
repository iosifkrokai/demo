import { useEffect, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { Sidebar } from '@/components/sidebar';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { useCommonStore } from '@/stores/common-store';
import { cn } from '@/lib/utils';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Route as RouteIcon } from 'lucide-react';

import {
  MOBILE_GUIDE_HEIGHT,
  MOBILE_SHEET_HEIGHT,
  guidePx,
  useMobileSheetSnap,
} from './use-mobile-sheet-snap';
import type { MobileSnap } from './use-mobile-sheet-snap';
import type { SheetHandleProps } from '@/components/parts/sheet-snap';

/** The mobile panel host: it owns the geometry (three positions, the height the map controls ride above, the strip used while walking) and renders the same panel contents inside it. */
const SHEET_CLASS = [
  'bg-background p-0 gap-0 overflow-hidden rounded-t-3xl border-t border-border shadow-sheet',
  'pb-[env(safe-area-inset-bottom)]',
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
  'data-[dragging=true]:transition-none',
].join(' ');

export interface MobileShellProps {
  /** The panel's contents. */
  panel?: ReactNode | ((handle: SheetHandleProps) => ReactNode);
  /** Where the panel starts; a deep link or a test can open it fully. */
  initialSnap?: MobileSnap;
}

export const MobileShell = ({
  panel,
  initialSnap = 'peek',
}: MobileShellProps) => {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: '/$activeTab' });
  const guiding = useCommonStore((s) => s.guiding);
  const panelOpen = useCommonStore((s) => s.directionsPanelOpen);
  const setDirectionsPanelOpen = useCommonStore(
    (s) => s.setDirectionsPanelOpen
  );

  const { snap, height, dragHeight, dragging, handleProps, setSnap } =
    useMobileSheetSnap(initialSnap, {
      restingPx: guiding ? guidePx() : undefined,
      onDismiss: () => setDirectionsPanelOpen(false),
    });

  const guideCollapsed = guiding && snap === 'peek';
  const restingHeight = guideCollapsed ? MOBILE_GUIDE_HEIGHT : null;

  const settled = restingHeight ?? height;
  const sheetHeight =
    dragHeight !== null ? `${Math.round(dragHeight)}px` : settled;

  const wasOpen = useRef(panelOpen);
  useEffect(() => {
    if (panelOpen && !wasOpen.current) setSnap('peek');
    wasOpen.current = panelOpen;
  }, [panelOpen, setSnap]);

  const wasGuiding = useRef(guiding);
  useEffect(() => {
    if (guiding && !wasGuiding.current) setSnap('peek');
    wasGuiding.current = guiding;
  }, [guiding, setSnap]);

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
    <>
      {!panelOpen && (
        <div
          data-testid="mobile-sheet-opener"
          className="absolute inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+var(--sheet-h,0px)+0.75rem)] z-10 flex justify-center px-3 md:hidden"
        >
          <Button
            type="button"
            data-testid="mobile-panel-open"
            className="h-12 min-w-[11rem] gap-2 rounded-full px-5 text-label font-semibold shadow-card"
            onClick={() => {
              setDirectionsPanelOpen(true);
              navigate({ params: { activeTab: 'directions' } });
            }}
          >
            <RouteIcon className="h-4 w-4" aria-hidden="true" />
            {t('map.planRoute')}
          </Button>
        </div>
      )}

      <Sheet open={panelOpen} modal={false}>
        <SheetContent
          side="bottom"
          data-testid="mobile-sheet"
          data-snap={snap}
          data-dragging={dragging}
          className={cn(
            SHEET_CLASS,
            guideCollapsed && 'bg-transparent border-t-transparent shadow-none'
          )}
          style={{ height: sheetHeight } as CSSProperties}
        >
          {panel ? (
            typeof panel === 'function' ? (
              panel(handleProps)
            ) : (
              panel
            )
          ) : (
            <Sidebar
              bare
              snap={snap === 'full' ? 'full' : 'peek'}
              handleProps={handleProps}
              publishSheetHeight={false}
            />
          )}
        </SheetContent>
      </Sheet>
    </>
  );
};
