import type { PlaceDetails } from '@/stores/directions-store';

import { PlaceCardBody } from '@/components/map/parts/place-card-body';

/** About a point, on a phone. */
export interface MobilePlaceCardProps {
  details: PlaceDetails;
  /** The DB `places.id`, so the card can offer the «посещено» toggle. */
  placeId?: number | null;
  onClose: () => void;
}

export function MobilePlaceCard({
  details,
  placeId = null,
  onClose,
}: MobilePlaceCardProps) {
  return (
    <div
      data-testid="mobile-place-card"
      className="pointer-events-none absolute inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+var(--sheet-h,0px)+0.5rem)] z-20 flex justify-center px-1.5 md:hidden"
    >
      <div className="slim-scroll pointer-events-auto max-h-[min(52dvh,26rem)] w-full overflow-y-auto overscroll-contain rounded-2xl border border-border bg-card/97 shadow-float backdrop-blur-sm">
        <PlaceCardBody
          details={details}
          onClose={onClose}
          mobile
          placeId={placeId}
        />
      </div>
    </div>
  );
}
