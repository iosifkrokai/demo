import type { LocaleArea } from './guide';

/**
 * The credit line under a photo. Short by design: it sits under an image, not
 * in a paragraph, and it is a licence obligation rather than decoration — the
 * author and the licence name have to be legible next to the picture.
 */
export interface PhotoAreaShape {
  photo: Record<'credit', string>;
}

export const photoArea: LocaleArea<PhotoAreaShape> = {
  ru: {
    photo: {
      credit: 'фото: {{author}} · {{license}}',
    },
  },
  en: {
    photo: {
      credit: 'photo: {{author}} · {{license}}',
    },
  },
};
