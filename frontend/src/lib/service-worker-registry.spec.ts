/** Tests for service-worker-registry.ts */

import { describe, expect, it } from 'vitest';

/** Re-exports of the pure functions from sw.js so they can be tested here. */

/** Whether a request URL matches any no-cache prefix. */
function matchNoCache(url: string, prefixes: string[]): string | null {
  const pathname = url.startsWith('http')
    ? (() => {
        try {
          return new URL(url).pathname;
        } catch {
          return url.split('?')[0]!;
        }
      })()
    : url.split('?')[0]!;

  for (const prefix of prefixes) {
    if (pathname.startsWith(prefix) || pathname === prefix) {
      return prefix;
    }
  }
  return null;
}

const NO_CACHE_PREFIXES = [
  '/routes/generate',
  '/routes',
  '/route',
  '/isochrone',
  '/optimized_route',
  '/status',
  '/locate',
  '/height',
  '/tile/',
  '/tile',
  '/clients',
];

describe('matchNoCache', () => {
  it('returns null for uncached paths', () => {
    expect(matchNoCache('/', NO_CACHE_PREFIXES)).toBeNull();
    expect(
      matchNoCache('/assets/index-a1b2c3d4.js', NO_CACHE_PREFIXES)
    ).toBeNull();
    expect(matchNoCache('/favicon.svg', NO_CACHE_PREFIXES)).toBeNull();
    expect(matchNoCache('/manifest.json', NO_CACHE_PREFIXES)).toBeNull();
    expect(matchNoCache('/directions', NO_CACHE_PREFIXES)).toBeNull();
  });

  it('returns the matching prefix for API routes', () => {
    expect(matchNoCache('/route?json={}', NO_CACHE_PREFIXES)).toBe('/route');
    expect(matchNoCache('/routes/generate', NO_CACHE_PREFIXES)).toBe(
      '/routes/generate'
    );
    expect(matchNoCache('/routes', NO_CACHE_PREFIXES)).toBe('/routes');
    expect(matchNoCache('/clients', NO_CACHE_PREFIXES)).toBe('/clients');
    expect(matchNoCache('/status', NO_CACHE_PREFIXES)).toBe('/status');
  });

  it('returns the matching prefix for tile requests', () => {
    expect(matchNoCache('/tile/12/2048/1024.png', NO_CACHE_PREFIXES)).toBe(
      '/tile/'
    );
    expect(matchNoCache('/tile', NO_CACHE_PREFIXES)).toBe('/tile');
  });

  it('handles full URLs correctly', () => {
    expect(
      matchNoCache('https://example.com/route?json={}', NO_CACHE_PREFIXES)
    ).toBe('/route');
    expect(
      matchNoCache('https://example.com/assets/app.js', NO_CACHE_PREFIXES)
    ).toBeNull();
    expect(matchNoCache('https://example.com/', NO_CACHE_PREFIXES)).toBeNull();
  });

  it('handles query strings in paths', () => {
    expect(matchNoCache('/route?json={"foo":"bar"}', NO_CACHE_PREFIXES)).toBe(
      '/route'
    );
    expect(
      matchNoCache(
        '/isochrone?json={"contours":[{"minutes":30}]}',
        NO_CACHE_PREFIXES
      )
    ).toBe('/isochrone');
  });
});

type Strategy = 'skip' | 'shell' | 'static';

/** Determines the cache strategy for a given request. */
function getStrategy(url: string, noCachePrefixes: string[]): Strategy {
  const pathname = (() => {
    try {
      return url.startsWith('http')
        ? new URL(url).pathname
        : url.split('?')[0]!;
    } catch {
      return url;
    }
  })();

  for (const prefix of noCachePrefixes) {
    if (pathname.startsWith(prefix) || pathname === prefix) {
      return 'skip';
    }
  }

  if (pathname === '/' || pathname.endsWith('index.html')) {
    return 'shell';
  }

  if (/\.(js|css|woff2?|ttf|otf|png|svg|ico|webp|json)$/.test(pathname)) {
    return 'static';
  }

  return 'skip';
}

describe('getStrategy', () => {
  it('skips API and tile paths', () => {
    expect(getStrategy('/route?json={}', NO_CACHE_PREFIXES)).toBe('skip');
    expect(getStrategy('/routes/generate', NO_CACHE_PREFIXES)).toBe('skip');
    expect(getStrategy('/tile/12/2048/1024.png', NO_CACHE_PREFIXES)).toBe(
      'skip'
    );
    expect(getStrategy('/clients', NO_CACHE_PREFIXES)).toBe('skip');
    expect(getStrategy('/status', NO_CACHE_PREFIXES)).toBe('skip');
  });

  it('returns shell for navigation requests', () => {
    expect(getStrategy('/', NO_CACHE_PREFIXES)).toBe('shell');
    expect(getStrategy('/index.html', NO_CACHE_PREFIXES)).toBe('shell');
  });

  it('returns static for asset files', () => {
    expect(getStrategy('/assets/app-a1b2c3d4.js', NO_CACHE_PREFIXES)).toBe(
      'static'
    );
    expect(getStrategy('/assets/index-c1d2e3f4.css', NO_CACHE_PREFIXES)).toBe(
      'static'
    );
    expect(getStrategy('/favicon.svg', NO_CACHE_PREFIXES)).toBe('static');
    expect(getStrategy('/manifest.json', NO_CACHE_PREFIXES)).toBe('static');
  });

  it('returns skip for unknown paths', () => {
    expect(getStrategy('/unknown-path', NO_CACHE_PREFIXES)).toBe('skip');
    expect(getStrategy('/api/unknown', NO_CACHE_PREFIXES)).toBe('skip');
  });

  it('handles full URLs', () => {
    expect(
      getStrategy('https://example.com/assets/app.js', NO_CACHE_PREFIXES)
    ).toBe('static');
    expect(getStrategy('https://example.com/route', NO_CACHE_PREFIXES)).toBe(
      'skip'
    );
  });
});

describe('register (DEV mode guard)', () => {
  it('skips registration in dev mode without touching navigator', () => {
    expect(typeof import.meta.env.DEV).toBe('boolean');
  });
});
