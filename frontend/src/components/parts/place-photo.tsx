import { useTranslation } from 'react-i18next';

import type { Photo } from '@/api/types';

interface PlacePhotoProps {
  photo: Photo | null | undefined;
  /** The point's name — the `alt` text, since the picture shows that place. */
  name: string;
  className?: string;
}

/**
 * A point's picture, with the credit its licence requires.
 *
 * Renders **nothing** when there is no photo. That is the common case — most
 * points have none — and a grey placeholder would read as "still loading" on
 * every one of them.
 *
 * The credit is not optional chrome: for a CC BY-SA file the author and the
 * licence name must be shown, and the link goes to the file page so anyone can
 * check it. The data layer never hands over a photo without both (see
 * `parse_photo` on the backend), so this only has to print what it is given.
 */
export function PlacePhoto({ photo, name, className }: PlacePhotoProps) {
  const { t } = useTranslation();

  if (!photo) return null;

  const credit = t('photo.credit', { author: photo.author, license: photo.license });

  return (
    <figure className={className}>
      <img
        src={photo.url}
        alt={name}
        loading="lazy"
        decoding="async"
        className="aspect-[4/3] w-full rounded-lg border border-border object-cover"
      />
      <figcaption className="mt-1 text-meta text-muted-foreground">
        {photo.source ? (
          <a
            href={photo.source}
            target="_blank"
            rel="noreferrer noopener"
            className="transition-colors hover:text-foreground hover:underline"
          >
            {credit}
          </a>
        ) : (
          credit
        )}
      </figcaption>
    </figure>
  );
}
