import type { PlaceDetails } from '@/stores/directions-store';

interface PlaceMarkerLabelProps {
  details: PlaceDetails;
}

/**
 * Always-visible caption rendered next to a route marker: the place name plus
 * its one-line description. The full card (with the fun fact) opens on click —
 * see PlaceCardPopup. Kept pointer-events-none so clicks/drags reach the marker.
 */
export function PlaceMarkerLabel({ details }: PlaceMarkerLabelProps) {
  return (
    <div
      className="pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 w-max max-w-[210px] -translate-x-1/2 rounded-md border border-border/60 bg-background/90 px-2 py-1 text-center shadow-sm backdrop-blur-sm"
      data-testid="place-marker-label"
    >
      <div className="truncate text-[11px] leading-tight font-semibold">
        {details.name}
      </div>
      {details.blurb && (
        <div className="truncate text-[10px] leading-tight text-muted-foreground">
          {details.blurb}
        </div>
      )}
    </div>
  );
}
