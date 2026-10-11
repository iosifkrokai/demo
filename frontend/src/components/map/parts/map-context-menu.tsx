import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { useDirectionsStore } from '@/stores/directions-store';

/** Touch-only long-press menu: every action needs a finger-sized target (≥44px). */
const TOUCH_BUTTON = 'pointer-coarse:min-h-11 max-md:min-h-11';

interface MapContextMenuProps {
  onAddWaypoint: (index: number) => void;
  popupLocation: { lng: number; lat: number };
}

export function MapContextMenu({
  onAddWaypoint,
  popupLocation,
}: MapContextMenuProps) {
  const { t } = useTranslation();
  const waypointCount = useDirectionsStore((state) => state.waypoints.length);
  const addWaypointAtIndex = useDirectionsStore(
    (state) => state.addWaypointAtIndex
  );

  return (
    <ButtonGroup
      orientation="vertical"
      data-testid="button-group-right-context"
    >
      <Button
        variant="outline"
        size="sm"
        className={TOUCH_BUTTON}
        onClick={() => onAddWaypoint(0)}
      >
        {t('map.menuFrom')}
      </Button>
      <Button
        variant="outline"
        size="sm"
        className={TOUCH_BUTTON}
        onClick={() => {
          addWaypointAtIndex({
            index: waypointCount - 1,
            placeholder: popupLocation,
          });
          onAddWaypoint(waypointCount - 1);
        }}
      >
        {t('map.menuVia')}
      </Button>
      <Button
        variant="outline"
        size="sm"
        className={TOUCH_BUTTON}
        onClick={() => onAddWaypoint(waypointCount - 1)}
      >
        {t('map.menuTo')}
      </Button>
    </ButtonGroup>
  );
}
