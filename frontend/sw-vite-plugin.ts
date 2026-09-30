/**
 * Vite plugin: configures the service worker at build time.
 *
 * 1. public/sw.js  → receives:
 *      __SW_BASE_URL__      → package.json homepage pathname
 *      __SW_CACHE_NAME__    → "sw-shell-v{timestamp}"
 *      __SW_PRECACHE_URLS__  → JSON array of hashed JS/CSS + public file URLs
 *    Written to build/sw.js, OVER the copy Vite makes of public/sw.js.
 *
 * 2. index.html    — nothing to inject: registration lives in the typed module
 *                    src/lib/service-worker-registry.ts, which also guards dev
 *                    and drives the update prompt. An inline copy in the HTML
 *                    would register a second time and, worse, would do it in dev
 *                    with the placeholders still in place.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Plugin } from 'vite';

// Minimal types for writeBundle bundle items (subset of Rollup's OutputChunk/OutputAsset)
interface BundleChunk {
  type: 'chunk';
  isEntry: boolean;
  name: string;
  fileName: string;
}
interface BundleAsset {
  type: 'asset';
  fileName: string;
}
type BundleItem = BundleChunk | BundleAsset;

function getBaseUrl(): string {
  const { homepage } = JSON.parse(readFileSync('package.json', 'utf-8')) as {
    homepage?: string;
  };
  if (!homepage) return '/';
  if (homepage.startsWith('http')) {
    const url = new URL(homepage);
    return url.pathname === '/' ? '/' : url.pathname.replace(/\/$/, '') + '/';
  }
  const base = homepage.startsWith('/') ? homepage : `/${homepage}`;
  return base.replace(/\/$/, '') + '/';
}

/**
 * Everything the shell needs to boot with no network: the built chunks (the entry
 * AND the lazily-loaded ones — the map library included), the CSS, and the
 * always-served public files.
 *
 * Read from the BUNDLE, not from a reconstructed manifest. On this hook the
 * items carry `fileName`; the first draft read `outputName`, which does not
 * exist here, so the JS bundle was silently left out of the precache list — a
 * worker that caches a shell which cannot start.
 */
function buildPrecacheUrls(
  baseUrl: string,
  bundle: Record<string, BundleItem>
): string[] {
  const urls = ['favicon.svg', 'favicon.png', 'manifest.json'].map(
    (file) => `${baseUrl}${file}`
  );

  for (const item of Object.values(bundle)) {
    if (item.type === 'chunk') {
      urls.push(`${baseUrl}${item.fileName}`);
    } else if (item.fileName.endsWith('.css')) {
      urls.push(`${baseUrl}${item.fileName}`);
    }
  }

  return [...new Set(urls)].sort();
}

function transformSw(
  source: string,
  opts: { baseUrl: string; cacheName: string; precacheUrls: string[] }
): string {
  return source
    .replace('__SW_BASE_URL__', JSON.stringify(opts.baseUrl))
    .replace('__SW_CACHE_NAME__', JSON.stringify(opts.cacheName))
    .replace('__SW_PRECACHE_URLS__', JSON.stringify(opts.precacheUrls));
}

export function swVitePlugin(): Plugin[] {
  const baseUrl = getBaseUrl();
  const ts = String(Date.now());
  const cacheName = `sw-shell-v${ts}`;

  return [
    // After build: write the TRANSFORMED sw.js over the copy Vite made of
    // public/sw.js. Vite copies public/ verbatim, so without this the file the
    // browser actually requests still contains the literal placeholders — a
    // worker that precaches paths named «__SW_BASE_URL__». The cache name
    // carries the timestamp, so the bytes change on every build and the
    // registry's reg.update() sees a new version; that is the cache-busting, and
    // it needs no «?v=» in the FILENAME (a file named «sw.js?v=1» is a file
    // nginx cannot serve under any URL).
    {
      name: 'sw-vite-plugin:write',
      apply: 'build',
      writeBundle(
        _options: { dir?: string },
        bundle: Record<string, BundleItem>
      ) {
        const outDir = (_options.dir ?? resolve('build')) as string;

        const swSource = transformSw(
          readFileSync(resolve('public/sw.js'), 'utf-8'),
          {
            baseUrl,
            cacheName,
            precacheUrls: buildPrecacheUrls(baseUrl, bundle),
          }
        );

        writeFileSync(resolve(outDir, 'sw.js'), swSource, 'utf-8');
      },
    } as unknown as Plugin,
  ];
}
