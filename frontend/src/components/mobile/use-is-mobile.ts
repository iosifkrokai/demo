import { useEffect, useState } from 'react';

/** Tailwind's `md` breakpoint minus one — the same 768 the sheet classes use.
 * Exported so the mobile shell and its spec cannot drift apart. */
export const MOBILE_MAX_WIDTH = 767;

const mediaQuery = () => `(max-width: ${MOBILE_MAX_WIDTH}px)`;

/**
 * Is this a phone-sized viewport?
 *
 * The ONE place the app branches on viewport width. Everything else either
 * belongs to the mobile shell or to the desktop one; a second `matchMedia` (or
 * another `max-md:` in a desktop component) is how the two shells start to
 * disagree about which one is on screen.
 *
 * jsdom has no `matchMedia` — the initial `innerWidth` check keeps the hook
 * usable there instead of throwing, and the effect simply does not subscribe.
 */
export const useIsMobile = (): boolean => {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= MOBILE_MAX_WIDTH
  );

  useEffect(() => {
    const mq = window.matchMedia?.(mediaQuery());
    if (!mq) return;
    // The media query, not the resize event: it is the same predicate the CSS
    // uses, so a zoom or a scrollbar change cannot move one without the other.
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return isMobile;
};
