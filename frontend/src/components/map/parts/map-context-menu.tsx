import { Button } from '@/components/ui/button';
import { ButtonGroup } from '@/components/ui/button-group';
import { useDirectionsStore } from '@/stores/directions-store';

/** Touch-only long-press menu: every action needs a finger-sized target (≥44px). */
const TOUCH_BUTTON = 'pointer-coarse:min-h-11 max-md:min-h-11';

interface MapContextMenuProps {
  activeTab: string;
  onAddWaypoint: (index: number) => void;
  onAddIsoWaypoint: () => void;
  popupLocation: { lng: number; lat: number };
}

export function MapContextMenu({
  activeTab,
  onAddWaypoint,
  onAddIsoWaypoint,
  popupLocation,
}: MapContextMenuProps) {
  const waypointCount = useDirectionsStore((state) => state.waypoints.length);
  const addWaypointAtIndex = useDirectionsStore(
    (state) => state.addWaypointAtIndex
  );

  if (activeTab === 'directions') {
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
          Directions from here
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
          Add as via point
        </Button>
        <Button
          variant="outline"
          size="sm"
          className={TOUCH_BUTTON}
          onClick={() => onAddWaypoint(waypointCount - 1)}
        >
          Directions to here
        </Button>
      </ButtonGroup>
    );
  }

  return (
    <ButtonGroup orientation="vertical">
      <Button
        variant="outline"
        size="sm"
        className={TOUCH_BUTTON}
        onClick={onAddIsoWaypoint}
      >
        Set center here
      </Button>
    </ButtonGroup>
  );
}
