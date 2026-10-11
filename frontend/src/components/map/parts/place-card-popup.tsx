import { Popup } from 'react-map-gl/maplibre';
import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from './place-card-body';

interface PlaceCardPopupProps {
  lng: number;
  lat: number;
  details: PlaceDetails;
  /** The DB `places.id`, threaded through so the card can toggle «посещено». */
  placeId?: number | null;
  onClose: () => void;
}

/** Card shown next to a route marker on a wide screen. */
export function PlaceCardPopup({
  lng,
  lat,
  details,
  placeId = null,
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
      <PlaceCardBody details={details} onClose={onClose} placeId={placeId} />
    </Popup>
  );
}
