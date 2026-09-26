import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * DESIGN.md defines its own type scale (`--text-badge|meta|label|body|title|stat`
 * in index.css). tailwind-merge cannot tell those font sizes apart from text
 * colours, so it used to read `text-label` and `text-foreground` on one element
 * as a single conflicting group and silently drop the size — which is how chips
 * and labels ended up rendering at the inherited 15px instead of 13px.
 * Declaring the scale once here keeps every cn() call honest.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['badge', 'meta', 'label', 'body', 'title', 'stat'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
