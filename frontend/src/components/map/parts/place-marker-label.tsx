import type { PlaceDetails } from '@/stores/directions-store';

interface PlaceMarkerLabelProps {
  details: PlaceDetails;
  /** The point the tourist tapped: its label stays, the rest step aside. */
  active?: boolean;
}

/** The caption next to a route marker: the place name plus its one-line description. */
export function PlaceMarkerLabel({
  details,
  active = false,
}: PlaceMarkerLabelProps) {
  return (
    <div
      data-testid="place-marker-label"
      data-active={active ? 'true' : 'false'}
      className={[
        'pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 w-max -translate-x-1/2',
        'rounded-lg border border-border/60 bg-background/90 px-2 py-1 text-center shadow-sm backdrop-blur-sm',
        active
          ? 'max-md:max-w-[70vw]'
          : 'max-md:max-w-[40vw] max-md:animate-place-label-out',
        'max-w-[210px]',
      ].join(' ')}
    >
      <div className="truncate text-badge leading-tight font-semibold">
        {details.name}
      </div>
      {details.blurb && (
        <div className="truncate text-badge leading-tight text-muted-foreground max-md:hidden">
          {details.blurb}
        </div>
      )}
    </div>
  );
}
