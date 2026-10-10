/** Mobile density tokens — everything that makes a phone-sized screen fit in one file, instead of a `max-md:h-*` scattered through every caller (that sprinkle is what made the panel's own blocks grow until the main action fell out of the sheet). */
import { MOBILE_MAX_WIDTH } from './use-is-mobile';

/** Every token here applies below this width (Tailwind `md` minus one). */
export const DENSITY_MAX_WIDTH = MOBILE_MAX_WIDTH;

/** Smallest side a finger target may have on a phone. */
export const MIN_TAP_PX = 40;

/** A 44px side is comfortable; the plan keeps it for the controls that matter. */
export const COMFORT_TAP_PX = 44;

export const DENSITY = {
  /** Panel padding: 16 -> 12. */
  panelX: 'max-md:px-3',
  panelY: 'max-md:py-2',
  /** Tab strip (Plan/Routes/History) and its items. */
  segmented: 'max-md:p-0.5',
  segmentedItem: 'max-md:h-10 max-md:px-3 pointer-coarse:max-md:h-10',
  /** Language switcher: RU/EN was a 33x36 sliver — now 44x44. */
  languageItem:
    'max-md:h-11 max-md:min-w-11 max-md:px-0 pointer-coarse:max-md:h-11 pointer-coarse:max-md:min-w-11',
  /** Chips (time presets, interests): 44px -> 40px, and still wide. */
  chip: 'max-md:h-10 max-md:px-3 pointer-coarse:max-md:h-10',
  /** The ask field: the container's padding, not the textarea, is what bloated. */
  fieldBox: 'max-md:py-1.5',
  fieldInput: 'max-md:min-h-10',
  /** The pinned action bar. */
  footer: 'max-md:py-2',
  /** Gaps between chips/rows. */
  gap: 'max-md:gap-1.5',
  /** Radii: cards tighten on a phone. */
  card: 'max-md:rounded-xl',
} as const;
