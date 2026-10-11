import type { LocaleArea } from './guide';

/** The credit line under a photo. */
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
