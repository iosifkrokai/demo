import { useEffect, useState } from 'react';

/**
 * The value, held steady until it has stopped changing for `delayMs`.
 *
 * The admin search boxes are typed into character by character, and the raw
 * keystroke is not what the list should be fetched by: the query key is the
 * value, so without this every letter was a new key with no cached data and the
 * list blanked to «загружаю…» in between. The delay is short enough to feel
 * immediate and long enough to swallow a burst of typing.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);

  return debounced;
}
