# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`valhalla-web` (package name) is the ReactJS demo app that runs on https://valhalla.openstreetmap.de. It is a SPA frontend over the [Valhalla](https://github.com/valhalla/valhalla/CLAUDE.md) routing engine — it builds Valhalla `/route`, `/isochrone`, `/locate`, and `/height` requests and renders results on a MapLibre map. There is no backend in this repo.

## Commands

```bash
npm run dev            # Vite dev server on http://localhost:3000 (alias: npm start)
npm run build          # Vite production build → ./build (NOT ./dist)
npm run preview        # Serve the production build

npm test               # Vitest (watch mode); single test: npx vitest run path/to/file.spec.ts
npm run test:coverage  # Vitest with v8 coverage
npm run test:e2e       # Playwright (chromium + firefox); auto-starts dev server if not running
npm run test:e2e -- --project=chromium   # Single browser
npm run test:e2e:ui    # Playwright Test UI
npm run test:e2e:headed -- --project=firefox

npm run lint           # ESLint
npm run typecheck      # tsc --noEmit
npm run prettier       # Format
npm run check          # prettier:check && lint  (run before opening a PR)
npm run check:deps     # taze: list outdated deps interactively
```

Husky `pre-commit` runs `npm run typecheck && npx lint-staged` (eslint --fix on `*.{js,jsx,ts,tsx}`, prettier on `*.{json,md,scss,yaml,yml}`). CI (`.github/workflows/playwright.yml`) runs typecheck → lint → vitest → playwright (chromium only) on every PR.

## Tech stack

- **React 18** + **TypeScript** (strict, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`) + **Vite 7**
- **TanStack Router** (code-based, not file-based — see `src/routes.tsx`)
- **TanStack Query** for all Valhalla/Nominatim fetches
- **Zustand** + `immer` + `devtools` middleware for client state (3 stores in `src/stores/`)
- **Tailwind CSS v4** via `@tailwindcss/vite` + **shadcn/ui** (style `new-york`, base `slate`, lucide icons; see `components.json`)
- **maplibre-gl** + **react-map-gl** + `@watergis/maplibre-gl-terradraw` for drawing exclude-polygons
- **react-day-picker** (used by the shadcn `Calendar` primitive that powers the date/time button)
- **date-fns** for formatting
- **zod** for env/search-param/URL validation
- Path alias: `@/*` → `src/*`

## Architecture

### Entry & routing

```
src/index.tsx                      Mounts <RouterProvider> wrapped in TanStackQuery <Provider>
└─ src/routes.tsx                  Defines the router (code-based)
   └─ rootRoute → RootComponent    Renders <Outlet/> + dev-only TanStack devtools
      ├─ '/'                       beforeLoad redirects to '/directions'
      └─ '/$activeTab'             component=<App/>; validateSearch=zodValidator(searchParamsSchema)
                                   activeTab ∈ {'directions','isochrones','tiles'}; invalid → redirect
```

`<App/>` (`src/app.tsx`) wraps everything in `MapProvider` and renders three siblings: `MapComponent`, `RoutePlanner`, `SettingsPanel`, plus a sonner `<Toaster/>`.

URL search params are the source of truth for `profile` (costing model) and `style` (map style); a `retainSearchParams` middleware keeps them across tab switches. Schema is in `src/utils/route-schemas.ts`.

The Vite `base` is derived from `package.json` `homepage` (see `vite.config.ts → getBaseUrl()`), and the router uses `import.meta.env.BASE_URL`. The PR-preview workflow rewrites `homepage` before building so the bundle is served from `/{PR_NUMBER}/`.

### State

Three Zustand stores, each with `immer` + `devtools`:

- `src/stores/common-store.ts` — settings panel/directions panel open state, costing settings, dateTime, map-ready flag. `Profile` enum and `profileEnum` zod schema live here.
- `src/stores/directions-store.ts` — waypoints (with geocode results), route results, highlighted maneuver, optimized-route flag, active-route index, plus `placeDetails`: curated info about agent-generated stops (`name`/`category`/`blurb`/`funFact`/`visitMinutes`) keyed by the backend `places.id`, set by the sidebar from the `/routes/generate` response and read by the map to decorate markers. Waypoints carry `placeId` to link back to it.
- `src/stores/isochrones-store.ts` — input/result, range/interval/denoise/generalize, color palette, opacity.

Server-state lives in TanStack Query. The global `QueryClient` (`src/lib/tanstack-query/root-provider.tsx`) sets `refetchOnWindowFocus: false`, `retry: 1`, `staleTime: 5min`, `gcTime: 10min`. Query hooks are in `src/hooks/use-*-queries.ts`. They read inputs directly from Zustand stores via `useStore.getState()` and from the router via `router.state.location.search` rather than parameters — keep that pattern when adding new queries.

### Components

- `src/components/map/` — MapLibre map. `index.tsx` is the orchestrator; `parts/` holds map sublayers (route lines, isochrone polygons, hover popups, draw controls, marker icons). `valhalla-layers.ts` defines internal Valhalla edge/node/shortcut/access-restriction MVT layer IDs.
- `src/components/directions/`, `src/components/isochrones/`, `src/components/tiles/` — the three tab panels.
- `src/components/quick-settings.tsx` — left-sidebar "General settings" collapsible panel. Hosts the most-used controls inline so they're visible without opening the advanced panel: ferry/highway/toll icon buttons (a state-decorated `IconEnumButton` for each), a `DateTimeButton`, an alternates slider, and the directions language picker. Used by both the directions and isochrones tabs (the latter passes `showAlternates={false} showLanguage={false}`). Renders the `SettingsButton` ("Advanced settings") at the bottom — that's the entry point to `SettingsPanel`.
- `src/components/settings-panel/` — full ("advanced") costing options panel. `settings-options.ts` holds `settingsInit`, `settingsInitTruckOverride`, the per-profile `profileSettings` / `generalSettings` lists, and the `languageOptions` / language storage helpers. `settings-panel.tsx` renders `Profile Settings` + `General Settings`, **filtering out** `use_highways`, `use_tolls`, `use_ferry`, `alternates` since those moved to QuickSettings (params still flow through `filter-profile-settings.ts` for API requests).
- `src/components/ui/` — shadcn/ui primitives (do not rename — they're tracked by `components.json`). `icon-enum-setting.tsx` (`IconEnumButton`), `date-time-button.tsx`, and `calendar.tsx` are the QuickSettings building blocks.
- `src/components/types.ts` — shared `PossibleSettings`, `ActiveWaypoint`, Valhalla response types.

### Backend integration

- **Valhalla base URL**: `getBaseUrl()` in `src/utils/base-url.ts` reads `localStorage['valhalla_base_url']` first, then falls back to `VITE_VALHALLA_URL`. The settings panel lets users override and `testConnection()` validates by hitting `/status` and checking `available_actions` includes `route` and `isochrone`.
- **Client ID header**: every Valhalla request sends `X-Client-Id: ${VITE_CLIENT_ID}`. `src/index.tsx` warns at startup if it's unset or `unknown-web-app`. Production CI sets it to `public-web-app`.
- **Nominatim**: `src/utils/nominatim.ts`, base URL from `VITE_NOMINATIM_URL`.

### Conventions

- **File and folder names are KEBAB_CASE**, enforced by `eslint-plugin-check-file`. Spec/test/`.d.ts`/config files are exempt. Test files are `*.spec.ts(x)` colocated next to source.
- Vitest uses `jsdom` + `pool: 'vmForks'`. Setup in `src/test-setup.ts` polyfills `ResizeObserver` and imports `@testing-library/jest-dom/vitest`.
- Don't edit `src/components/ui/*` to add app-specific behavior — wrap them. `lib/utils.ts` exports `cn()` (clsx + tailwind-merge).

## Environment variables

All build-time, prefixed `VITE_`. Defined in `.env`, typed in `src/vite-env.d.ts`:

| Var                          | Purpose                                                                          |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `VITE_VALHALLA_URL`          | Valhalla server base URL (overridable via UI/localStorage)                       |
| `VITE_NOMINATIM_URL`         | Nominatim server for geocoding                                                   |
| `VITE_TILE_SERVER_URL`       | Raster tile URL template `{z}/{x}/{y}.png`                                       |
| `VITE_CENTER_COORDS`         | Initial map center `"lat,lng"`                                                   |
| `VITE_DEFAULT_COSTING_MODEL` | Default profile (auto/bicycle/pedestrian/car/truck/bus/motor_scooter/motorcycle) |
| `VITE_CLIENT_ID`             | Sent as `X-Client-Id` on Valhalla requests                                       |
| `VITE_AGENT_URL`             | Grodno FastAPI agent (`/routes/generate`); falls back to `http://localhost:8080`  |

## Deployment

- **Production** (`.github/workflows/deploy.yml`): on push to `master`, builds with `VITE_CLIENT_ID=public-web-app` (written to `.env.production.local`) and rsyncs `./build/` to the host server over SSH.
- **PR previews**: `preview-build.yml` rewrites the `homepage` field in `package.json` to `https://valhalla-app-tests.gis-ops.com/<PR#>` before building; `preview-deploy.yml` consumes that artifact, generates an `.htaccess` for SPA rewrites, rsyncs to `<host>/<PR#>/`, posts a status check, and comments the URL. `preview-cleanup.yml` removes the directory when the PR closes.
- **Docker** (`Dockerfile` + `docker-compose.yml`): node:24-alpine builder → nginx:1.29-alpine serving `./build` on port 80. Build-args do not pass through to Vite, so `.env` values are baked at image build time.
- The `npm run deploy` script (`gh-pages`) is defined but **not** used by any workflow — production goes via rsync.

## Grodno AI Guide integration

The sidebar (`src/components/sidebar.tsx`) drives the Grodno FastAPI agent. The map (`src/components/map/index.tsx`) renders the route and place markers.

### POST /routes/generate

**Request body** — the sidebar sends these fields:

| Field | Type | When sent |
|---|---|---|
| `query` | `string` | Always |
| `time_budget_minutes` | `number` | Only when the user picked ≥ 15 min; absent or `0` = no limit |
| `profile` | `string` (Valhalla costing name) | Only when the user explicitly picked a transport; `car` maps to `auto` |
| `origin` | `{lat: number, lon: number}` | Browser geolocation coordinates, if available |

**Response fields the UI consumes:**

| Field | Used by |
|---|---|
| `points[]` | Converted to waypoints and rendered on the map with `placeId` linking each to its `PlaceDetails` |
| `budget{}` | Displayed in the route summary strip |
| `summary{length_km, time_seconds}` | Displayed in the route summary strip |
| `costing` | If the user did not pick a transport, the UI adopts this costing and mirrors it into the URL `?profile=` param so the webapp's own `/route` request draws the line with the same costing |

### "My location" waypoint

The waypoint `id = 'me'` (`ME_WAYPOINT_ID`, exported from `stores/directions-store.ts`) marks the tourist's own position as the route start. It is:

- inserted as the **first waypoint** (before any agent-generated stops)
- drawn on the map as a **blue unnumbered pin** (the map skips it when assigning route-stop numbers)
- **excluded from stop numbering** in both the waypoint list and the map marker layer
- **re-planned** when the user picks a different transport (the sidebar re-submits the last query with the new `profile`)

### Map click semantics

- A **plain map click** (on empty ground): clears the active place card only. No coordinate popup, no Valhalla JSON popup — those were developer tools removed from the tourist-facing UI.
- A **marker click**: opens `PlaceCardPopup` (blurb, fun facts, links). The handler sets `markerClickRef` before the delayed map-click handler fires; the ref is checked so the delayed handler does not close the card that was just opened.


### Waypoint chunking for long routes

Valhalla's `/route` endpoint rejects requests with more than 20 locations (error `150, "Exceeded max locations: 20"`). A region-wide agent plan can have 26–29 stops, so `useDirectionsQuery()` in `src/hooks/use-directions-queries.ts` calls `chunkWaypoints()` (`src/utils/valhalla.ts`, `VALHALLA_MAX_LOCATIONS`) to split the waypoints into chained groups of at most 20 that share their joint endpoint, fetches each chunk separately, and merges the legs, geometry, and summary into a single response. Without this split the stops render on the map with no connecting line.

### No MMR trim without a time budget

MMR diversity trimming in `backend/agent/planner/pipeline.py` runs **only when the user specified a time budget**. With no budget the pipeline passes every candidate that survived retrieval + rerank (today bounded by `RETRIEVAL_POOL_SIZE=50` and `RERANK_POOL_SIZE=30`) straight to optimization — producing an honestly long multi-day route rather than a silently trimmed dozen stops.

Those two ceilings are deliberate and should **not** be raised to "return absolutely everything": a literal "give me every church in the voblast" request is rare, and lifting the caps costs paid rerank tokens per candidate plus a time matrix that grows into hours (29 stops already take ~25 s). The pipeline follows what the user actually asked for — a budget trims, no budget does not. Handling a genuinely exhaustive list is a catalogue feature (list grouped by town, route inside the chosen one), not a bigger pool.

## Working with this team

