import { ExternalLink, Lightbulb, Clock, MapPin, Ticket, X } from 'lucide-react';
import { Popup } from 'react-map-gl/maplibre';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { PlaceDetails } from '@/stores/directions-store';

interface PlaceCardPopupProps {
  lng: number;
  lat: number;
  details: PlaceDetails;
  onClose: () => void;
}

/**
 * Card shown next to a route marker.
 * Shows: name, category, visit time, blurb, multiple fun facts, and links.
 */
export function PlaceCardPopup({
  lng,
  lat,
  details,
  onClose,
}: PlaceCardPopupProps) {
  const hasExtraFacts = details.funFacts && details.funFacts.length > 0;
  const hasLinks = details.links && details.links.length > 0;

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
      {/* Clicks inside the card must not bubble to the map: the map's own click
          handler would treat them as a miss and close the card mid-read. */}
      <div
        className="relative flex min-w-[260px] max-w-[340px] flex-col gap-2.5 px-3 py-3"
        data-testid="place-card-popup"
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onClose}
          className="absolute right-1 top-1"
          aria-label="Закрыть"
        >
          <X className="size-3.5" />
        </Button>

        {/* Header: name + category */}
        <div className="pr-6 text-body font-semibold leading-tight">
          {details.name}
        </div>

        {/* Badges: category + visit time */}
        <div className="flex flex-wrap items-center gap-1.5">
          {details.category && (
            <Badge variant="secondary" className="capitalize">
              {details.category}
            </Badge>
          )}
          {details.visitMinutes != null && (
            <Badge variant="outline" className="gap-1">
              <Clock className="size-3" />~{details.visitMinutes} мин
            </Badge>
          )}
        </div>

        {/* Visitor info: opening hours, ticket price, location */}
        {(details.openingHours ||
          details.ticketPrice ||
          details.town ||
          details.district) && (
          <div className="flex flex-col gap-1 text-meta text-muted-foreground">
            {details.openingHours && (
              <div className="flex items-center gap-1.5">
                <Clock className="size-3 shrink-0" />
                {details.openingHours}
              </div>
            )}
            {details.ticketPrice && (
              <div className="flex items-center gap-1.5">
                <Ticket className="size-3 shrink-0" />
                {details.ticketPrice}
              </div>
            )}
            {(details.town || details.district) && (
              <div className="flex items-center gap-1.5">
                <MapPin className="size-3 shrink-0" />
                {[details.town, details.district]
                  .filter(Boolean)
                  .join(', ')}
              </div>
            )}
          </div>
        )}

        {/* Description */}
        {details.blurb && (
          <p className="text-meta leading-snug text-muted-foreground">
            {details.blurb}
          </p>
        )}

        {/* Primary fun fact */}
        {details.funFact && (
          <div className="flex gap-1.5 rounded-lg bg-primary/5 p-2">
            <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-primary" />
            <p className="text-meta leading-snug">{details.funFact}</p>
          </div>
        )}

        {/* Extra facts */}
        {hasExtraFacts && (
          <div className="flex flex-col gap-1.5 border-l-2 border-primary/20 pl-3">
            <span className="text-badge uppercase tracking-wide text-muted-foreground">
              Ещё факты
            </span>
            {details.funFacts!.slice(0, 3).map((fact, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span className="mt-1 size-1.5 shrink-0 rounded-full bg-primary/40" />
                <p className="text-meta leading-snug text-muted-foreground">
                  {fact}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Links */}
        {hasLinks && (
          <div className="flex flex-col gap-1">
            <span className="text-badge uppercase tracking-wide text-muted-foreground">
              Почитать
            </span>
            <div className="flex flex-col gap-0.5">
              {details.links!.slice(0, 4).map((link, i) => (
                <a
                  key={i}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-meta text-primary hover:underline"
                >
                  <ExternalLink className="size-3 shrink-0" />
                  <span className="truncate">{link.title}</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </Popup>
  );
}
