import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from '@/components/map/parts/place-card-body';

/**
 * About a point, on a phone.
 *
 * The desktop answer to «что это за место» is a 340px popup anchored to the pin,
 * floating over the middle of the map. On a 390px screen that popup is 87 % of
 * the width, sits wherever the pin happens to be — often behind the map's own
 * controls or half off the top — and has to be read with a thumb covering the
 * bottom of it. Every phone map app solves this the same way: the place card is
 * a **card standing on the bottom of the map**, in reach of the thumb, with the
 * map still visible above it. So that is what this is.
 *
 * Two decisions worth stating:
 *
 * * **It is docked, not anchored to the pin.** A pin-anchored card inherits the
 *   pin's screen position, which means it moves when the map is panned, can
 *   land under the map's own controls, and has to be caught by the pan handler
 *   to stay put. A card that owns the bottom of the screen has none of that
 *   problem and behaves the same at every zoom level.
 * * **It rides `--sheet-h`.** The same custom property the map's controls use, so
 *   the card and the planner sheet stack instead of overlapping, at whatever
 *   height the sheet currently is.
 *
 * The height is bounded and the body scrolls: a point with three extra facts and
 * four links must not be able to swallow the map it is describing.
 */
export interface MobilePlaceCardProps {
  details: PlaceDetails;
  onClose: () => void;
}

export function MobilePlaceCard({ details, onClose }: MobilePlaceCardProps) {
  return (
    <div
      data-testid="mobile-place-card"
      className="pointer-events-none absolute inset-x-0 bottom-[calc(var(--sheet-h,0px)+0.5rem)] z-20 flex justify-center px-1.5 md:hidden"
    >
      <div className="slim-scroll pointer-events-auto max-h-[min(52dvh,26rem)] w-full overflow-y-auto overscroll-contain rounded-2xl border border-border bg-card/97 shadow-float backdrop-blur-sm">
        <PlaceCardBody details={details} onClose={onClose} mobile />
      </div>
    </div>
  );
}
