import { useTranslation } from 'react-i18next';

import type { Photo } from '@/api/types';

interface PlacePhotoProps {
  photo: Photo | null | undefined;
  /** The point's name — the `alt` text, since the picture shows that place. */
  name: string;
  className?: string;
}

/** A point's picture, with the credit its licence requires. */
export function PlacePhoto({ photo, name, className }: PlacePhotoProps) {
  const { t } = useTranslation();

  if (!photo) return null;

  const credit = t('photo.credit', {
    author: photo.author,
    license: photo.license,
  });

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
