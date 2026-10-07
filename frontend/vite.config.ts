import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import svgr from 'vite-plugin-svgr';
import path from 'path';
import tailwindcss from '@tailwindcss/vite';
import wasm from 'vite-plugin-wasm';
import topLevelAwait from 'vite-plugin-top-level-await';
import { swVitePlugin } from './sw-vite-plugin';

function getBaseUrl() {
  const { homepage } = JSON.parse(readFileSync('package.json', 'utf-8')) as {
    homepage?: string;
  };
  if (!homepage) return '/';

  // If it's a full URL, extract just the pathname
  if (homepage.startsWith('http')) {
    const url = new URL(homepage);
    return url.pathname === '/' ? '/' : url.pathname.replace(/\/$/, '') + '/';
  }

  const base = homepage.startsWith('/') ? homepage : `/${homepage}`;
  return base.replace(/\/$/, '') + '/';
}

export default defineConfig({
  base: getBaseUrl(),
  // ONE env file for the whole project: the root .env, shared with the backend
  // and docker compose. Vite resolves envDir relative to this config's root
  // (frontend/), so '..' points at the repo root.
  envDir: '..',
  plugins: [
    react(),
    svgr({
      include: '**/*.svg',
      svgrOptions: { exportType: 'named', namedExport: 'ReactComponent' },
    }),
    tailwindcss(),
    // Ferrostar's navigation core ships as WASM — without these plugins the
    // ESM module fails to load in both dev and build.
    wasm(),
    topLevelAwait(),
    swVitePlugin(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    open: true,
    // Mirror the production nginx routes (see nginx.conf) so the dev server is
    // same-origin exactly like the deployed app: the UI calls /routes/* with no
    // VITE_AGENT_URL, and this proxy forwards to the locally running agent.
    // Without it every build attempt 404s against the Vite server.
    proxy: {
      '/routes': {
        target: process.env.VITE_DEV_AGENT_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
      // Anonymous client entity (spec 003): saved routes, preferences and
      // «удалить мои данные». Same proxy miss class as the Valhalla pattern
      // below — without this entry Vite answers 200 with index.html and the
      // client layer honestly reports a bad response instead of saving.
      '/clients': {
        target: process.env.VITE_DEV_AGENT_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
      // The full point catalogue (GET /places) — a top-level agent path, not
      // under /routes, so it needs its own entry to reach the agent in dev.
      '/places': {
        target: process.env.VITE_DEV_AGENT_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
      // Valhalla endpoints the map talks to directly (route, status, ...).
      // The trailing (?|\$) matters: these URLs carry a ?json=... query, and a
      // \$-anchored pattern silently misses them — Vite then answers with
      // index.html and the map loses its route line with no visible error.
      '^/(route|isochrone|optimized_route|status|locate|height|tile)(\\?|$)': {
        target: process.env.VITE_DEV_VALHALLA_TARGET || 'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'build',
  },
  test: {
    environment: 'jsdom',
    pool: 'vmForks',
  },
});
