import { Popup } from 'react-map-gl/maplibre';
import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from './place-card-body';

interface PlaceCardPopupProps {
  lng: number;
  lat: number;
  details: PlaceDetails;
  onClose: () => void;
}

/**
 * Card shown next to a route marker on a wide screen.
 * Shows: name, category, visit time, blurb, multiple fun facts, and links.
 *
 * A phone does not use this one: a 340px popup anchored to a pin, floating over
 * the map in the middle of a walk, is the hardest thing on the screen to read
 * and the easiest to lose. There it is a card standing on the bottom of the map
 * instead — see `mobile/mobile-place-card.tsx`. The content itself is shared
 * (PlaceCardBody), so the two cannot say different things about one point.
 */
export function PlaceCardPopup({
  lng,
  lat,
  details,
  onClose,
}: PlaceCardPopupProps) {
  return (
    <Popup
      longitude={lng}
      latitude={lat}
      anchor="bottom"
      offset={44}
      closeButton={false}
      closeOnClick={false}
      maxWidth="340px"
    >
      <PlaceCardBody details={details} onClose={onClose} />
    </Popup>
  );
}
