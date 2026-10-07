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
  // The height is animated only when it is *not* being dragged. During a drag the
  // height is rewritten on every pointer move to follow the finger, and a 200ms
  // transition on top of that is what made the panel feel like it was being
  // dragged through something: the sheet visibly trailed the glass by a fifth
  // of a second. The spring back to a position, and the slide in and out, are
  // still transitions.
  'transition-[height] duration-200 ease-out motion-reduce:transition-none',
  'data-[dragging=true]:transition-none',
].join(' ');

export interface MobileShellProps {
  /**
   * The panel's contents. A prop so the shell can be tested without the real
   * panel.
   *
   * It may be a function of the drag handlers, because the sheet's grab bar is
   * what carries them — the bar lives in the panel's own header, which the
   * shell does not render, so it hands them down rather than inventing a second
   * handle of its own. The real panel takes the same handlers as props.
   */
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

  // Keep the compact navigator strip at peek, but let the same handle expand it
  // to a full route overview when needed.
  const guideCollapsed = guiding && snap === 'peek';
  const restingHeight = guideCollapsed ? MOBILE_GUIDE_HEIGHT : null;

  // The position's own height, except for the compact guide strip — and, while
  // a finger is down, exactly what the finger is asking for.
  const settled = restingHeight ?? height;
  const sheetHeight =
    dragHeight !== null ? `${Math.round(dragHeight)}px` : settled;

  // A reopened sheet shows the planning height, not whatever it was left at:
  // dismissing is a "put this away" gesture, and coming back to a full-screen
  // panel the tourist had just swiped away would read as it refusing to go.
  // Only on a *re*open: a sheet mounted straight into `initialSnap="full"`
  // (a deep link, a test) must keep the position it was given.
  const wasOpen = useRef(panelOpen);
  useEffect(() => {
    if (panelOpen && !wasOpen.current) setSnap('peek');
    wasOpen.current = panelOpen;
  }, [panelOpen, setSnap]);

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
      {/* The way *in*, and on a phone there is no other one: the desktop panel
          is opened by a chevron standing on its own edge, which on a phone
          sits in the middle of the map with nothing to be the edge of. A phone
          opens its sheet the way its own map apps do — a button on the map. It
          rides above the sheet by the same `--sheet-h` every other floating
          control uses, so it never ends up underneath it. */}
      {!panelOpen && (
        <div
          data-testid="mobile-sheet-opener"
          className="absolute inset-x-0 bottom-[calc(var(--sheet-h,0px)+0.75rem)] z-10 flex justify-center px-3 md:hidden"
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
              // The three positions map onto the two the panel's own handle knows:
              // it only needs «expanded» to be true in the full one.
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
