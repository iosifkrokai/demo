import { useEffect, useState } from 'react';

/**
 * Track a CSS media query from React.
 *
 * Needed where a control's presence depends on the viewport, not just on its
 * style: the planner entry button sits over the map at the top-left, which is
 * exactly where the docked panel lives on wide screens — so it must not be
 * rendered at all while that panel is open, instead of being painted on top of
 * it. jsdom answers `matchMedia` with `matches: false` for every query, which
 * keeps tests on the small-viewport branch (the button exists) deterministically.
 */
export const useMediaQuery = (query: string): boolean => {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false
  );

  useEffect(() => {
    if (
      typeof window === 'undefined' ||
      typeof window.matchMedia !== 'function'
    )
      return;
    const list = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    const sync = () => setMatches(list.matches);
    sync();
    list.addEventListener('change', onChange);
    return () => list.removeEventListener('change', onChange);
  }, [query]);

  return matches;
};
