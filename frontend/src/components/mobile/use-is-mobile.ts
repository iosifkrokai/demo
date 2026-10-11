import { useEffect, useState } from 'react';

/** Tailwind's `md` breakpoint minus one — the same 768 the sheet classes use. */
export const MOBILE_MAX_WIDTH = 767;

const mediaQuery = () => `(max-width: ${MOBILE_MAX_WIDTH}px)`;

/** Is this a phone-sized viewport? */
export const useIsMobile = (): boolean => {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= MOBILE_MAX_WIDTH
  );

  useEffect(() => {
    const mq = window.matchMedia?.(mediaQuery());
    if (!mq) return;
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return isMobile;
};
