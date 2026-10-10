import { useState } from 'react';
import {
  ChevronDown,
  ExternalLink,
  Lightbulb,
  Clock,
  MapPin,
  Ticket,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { PlaceDetails } from '@/stores/directions-store';
import { PlacePhoto } from '@/components/parts/place-photo';
import { VisitedToggle } from '@/components/visited/visited-toggle';
import { fmtMin } from '@/components/parts/guide-format';

/** What a point has to say, laid out for the surface it is read on. */
export interface PlaceCardBodyProps {
  details: PlaceDetails;
  onClose: () => void;
  mobile?: boolean;
  /** The DB `places.id`, so the card can offer the «посещено» toggle. */
  placeId?: number | null;
}

export function PlaceCardBody({
  details,
  onClose,
  mobile = false,
  placeId = null,
}: PlaceCardBodyProps) {
  const { t } = useTranslation();
  const [moreOpen, setMoreOpen] = useState(false);

  const hasVisitorInfo = Boolean(
    details.openingHours ||
    details.ticketPrice ||
    details.town ||
    details.district
  );
  const hasMore = Boolean(
    (details.funFacts?.length ?? 0) > 0 || (details.links?.length ?? 0) > 0
  );

  return (
    <div
      className={
        mobile
          ? 'relative flex min-w-0 flex-col gap-2.5 px-3 py-3'
          : 'relative flex min-w-[260px] max-w-[340px] flex-col gap-2.5 px-3 py-3'
      }
      data-testid="place-card-body"
      data-variant={mobile ? 'mobile' : 'popup'}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <Button
        variant="ghost"
        size={mobile ? 'icon-lg' : 'icon-xs'}
        onClick={onClose}
        className={
          mobile
            ? 'absolute right-1.5 top-1.5 text-muted-foreground'
            : 'absolute right-1 top-1'
        }
        aria-label={t('map.placeClose')}
      >
        <X className={mobile ? 'size-4' : 'size-3.5'} />
      </Button>

      <div
        className={
          mobile
            ? 'pr-10 text-title font-semibold leading-tight'
            : 'pr-6 text-body font-semibold leading-tight'
        }
      >
        {details.name || t('sidebar.waypoints.unnamed')}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {details.category && (
          <Badge variant="secondary" className="capitalize">
            {details.category}
          </Badge>
        )}
        {details.visitMinutes != null && (
          <Badge variant="outline" className="gap-1">
            <Clock className={mobile ? 'size-3.5' : 'size-3'} />~
            {fmtMin(details.visitMinutes)}
          </Badge>
        )}
      </div>

      <VisitedToggle placeId={placeId} />

      {details.photo && (
        <PlacePhoto
          photo={details.photo}
          name={details.name}
          className="mt-0.5"
        />
      )}

      {hasVisitorInfo && (
        <div
          className={
            mobile
              ? 'flex flex-col gap-1 text-label text-muted-foreground'
              : 'flex flex-col gap-1 text-meta text-muted-foreground'
          }
        >
          {details.openingHours && (
            <div className="flex items-center gap-1.5">
              <Clock
                className={mobile ? 'size-3.5 shrink-0' : 'size-3 shrink-0'}
              />
              {details.openingHours}
            </div>
          )}
          {details.ticketPrice && (
            <div className="flex items-center gap-1.5">
              <Ticket
                className={mobile ? 'size-3.5 shrink-0' : 'size-3 shrink-0'}
              />
              {details.ticketPrice}
            </div>
          )}
          {(details.town || details.district) && (
            <div className="flex items-center gap-1.5">
              <MapPin
                className={mobile ? 'size-3.5 shrink-0' : 'size-3 shrink-0'}
              />
              {[details.town, details.district].filter(Boolean).join(', ')}
            </div>
          )}
        </div>
      )}

      {details.blurb && (
        <p
          className={
            mobile
              ? 'text-label leading-snug text-muted-foreground'
              : 'text-meta leading-snug text-muted-foreground'
          }
        >
          {details.blurb}
        </p>
      )}

      {details.funFact && (
        <div
          className={
            mobile
              ? 'flex gap-2 rounded-xl bg-primary/5 p-2.5'
              : 'flex gap-1.5 rounded-lg bg-primary/5 p-2'
          }
        >
          <Lightbulb
            className={
              mobile
                ? 'mt-0.5 size-4 shrink-0 text-primary'
                : 'mt-0.5 size-3.5 shrink-0 text-primary'
            }
          />
          <p
            className={
              mobile ? 'text-label leading-snug' : 'text-meta leading-snug'
            }
          >
            {details.funFact}
          </p>
        </div>
      )}

      {mobile ? (
        hasMore && (
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              data-testid="place-card-more"
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen((open) => !open)}
              className="h-11 justify-start self-start gap-1.5 px-2 text-label text-muted-foreground"
            >
              <ChevronDown
                className={
                  moreOpen
                    ? 'size-4 rotate-180 transition-transform'
                    : 'size-4 transition-transform'
                }
                aria-hidden="true"
              />
              {moreOpen ? t('map.placeLess') : t('map.placeMore')}
            </Button>

            {moreOpen && (
              <div className="flex flex-col gap-2.5">
                {details.funFacts && details.funFacts.length > 0 && (
                  <div className="flex flex-col gap-1.5 border-l-2 border-primary/20 pl-3">
                    <span className="text-badge uppercase tracking-wide text-muted-foreground">
                      {t('map.placeMoreFacts')}
                    </span>
                    {details.funFacts.slice(0, 3).map((fact, i) => (
                      <div
                        key={`fact-${i}-${fact.slice(0, 24)}`}
                        className="flex items-start gap-1.5"
                      >
                        <span className="mt-1 size-1.5 shrink-0 rounded-full bg-primary/40" />
                        <p className="text-label leading-snug text-muted-foreground">
                          {fact}
                        </p>
                      </div>
                    ))}
                  </div>
                )}

                {details.links && details.links.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <span className="text-badge uppercase tracking-wide text-muted-foreground">
                      {t('map.placeReadMore')}
                    </span>
                    <div className="flex flex-col gap-0.5">
                      {details.links.slice(0, 4).map((link, i) => (
                        <a
                          key={`link-${i}-${link.url}`}
                          href={link.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex min-h-11 items-center gap-1.5 text-label text-primary hover:underline"
                        >
                          <ExternalLink className="size-3.5 shrink-0" />
                          <span className="truncate">{link.title}</span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )
      ) : (
        <>
          {details.funFacts && details.funFacts.length > 0 && (
            <div className="flex flex-col gap-1.5 border-l-2 border-primary/20 pl-3">
              <span className="text-badge uppercase tracking-wide text-muted-foreground">
                {t('map.placeMoreFacts')}
              </span>
              {details.funFacts.slice(0, 3).map((fact, i) => (
                <div
                  key={`fact-${i}-${fact.slice(0, 24)}`}
                  className="flex items-start gap-1.5"
                >
                  <span className="mt-1 size-1.5 shrink-0 rounded-full bg-primary/40" />
                  <p className="text-meta leading-snug text-muted-foreground">
                    {fact}
                  </p>
                </div>
              ))}
            </div>
          )}

          {details.links && details.links.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-badge uppercase tracking-wide text-muted-foreground">
                {t('map.placeReadMore')}
              </span>
              <div className="flex flex-col gap-0.5">
                {details.links.slice(0, 4).map((link, i) => (
                  <a
                    key={`link-${i}-${link.url}`}
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
        </>
      )}
    </div>
  );
}
