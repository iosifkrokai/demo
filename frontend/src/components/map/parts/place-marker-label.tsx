import type { PlaceDetails } from '@/stores/directions-store';

interface PlaceMarkerLabelProps {
  details: PlaceDetails;
  /** The point the tourist tapped: its label stays, the rest step aside. */
  active?: boolean;
}

/**
 * The caption next to a route marker: the place name plus its one-line
 * description. The full card (with the fun fact) opens on click — see
 * PlaceCardPopup. Kept pointer-events-none so clicks/drags reach the marker.
 *
 * On a phone it is one line and it leaves by itself.
 *
 * Both halves of that were measured, and both were the reason the map read as
 * cluttered on 390×844:
 *
 * * **One line.** The blurb is dropped below `md` and the name is truncated to
 *   40vw. A two-line 210px plate on every pin of a 12-stop route overlaps its
 *   neighbours, and a name you cannot read is worse than no name — the card has
 *   the blurb anyway.
 * * **It goes away after 6s.** Labels for every stop at once answer «what is
 *   here» and then keep answering it, over the map, for as long as the route is
 *   open. The animation is CSS with a delay (see `--animate-place-label-out`),
 *   so it costs no state and no timer per marker; the tapped point's label
 *   opts out and stays, because that one is being read on purpose.
 */
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
        // A phone: one truncated line, and gone after 6s unless it was tapped.
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
