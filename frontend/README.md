# Grodno Guide — frontend

Vite 7 + React 18 + TypeScript webapp for the Grodno walking-route POC.
Built on top of the [valhalla/web-app](https://github.com/valhalla/valhalla/tree/master/web-app)
upstream; our additions live alongside the upstream code without forking its primitives.

Visual design tokens and component patterns are documented in `DESIGN.md`.

## What this app does

The user describes a walk (in Russian or English), picks a time budget and transport
mode, and the app calls the FastAPI agent (`/routes/generate`) to build a pedestrian
route over real Valhalla road data. The UI then shows:

- **Sidebar / Planning panel** — free-text query input, time and transport chips,
  quick-query hints, stop list with categories and visit times, route summary stats.
- **Guide panel** — step-by-step walker mode: next stop card, progress counter,
  manual "done" button, "open in maps" link.
- **Map** — MapLibre GL, numbered stop markers, route polyline from Valhalla.

nginx in the frontend container proxies:
- `POST /routes/*` → agent :8080
- `/route`, `/status`, `/isochrone`, `/locate`, `/height`, `/tile` → Valhalla :8002

## Commands

```bash
# Install dependencies
npm install

# Dev server with hot reload (Vite, http://localhost:5173)
npm run dev

# Type-check
npm run typecheck

# Lint
npm run lint

# Unit tests (Vitest)
npm test

# Production build
npm run build
```

## Environment

Copy the example before building or running `docker compose build`:

```bash
cp .env.example .env
```

`VITE_AGENT_URL` and `VITE_VALHALLA_URL` default to empty string (the app talks to its
own nginx origin). Set them only when running the dev server against a remote stack.

`VITE_*` vars are baked into the JS bundle at build time by Vite — changing `.env`
after a build has no effect until the next build.

## Docker

The container is built and started from the repo root:

```bash
# Build and start (requires .env to exist — see above)
docker compose up -d --build frontend
```

`nginx.conf` is mounted as a template; at container start `envsubst` renders
`AGENT_UPSTREAM` and `VALHALLA_UPSTREAM` into it. Default values point to
`host.docker.internal:8080` / `host.docker.internal:8002` (the host, so the
agent can run outside compose). See `docker-compose.yml` for the full
environment and `extra_hosts` setup.

## Key source files

| Path | Purpose |
|---|---|
| `src/components/sidebar.tsx` | Planning panel: query input, filters, stop list |
| `src/components/guide-panel.tsx` | Walker mode: next stop, progress, manual advance |
| `src/components/parts/segmented.tsx` | Segmented control (Planning / Guide) |
| `src/components/parts/guide-*.tsx` | Guide sub-components |
| `src/components/map/index.tsx` | MapLibre map + stop markers + route line |
| `src/index.css` | Design tokens (colours, radius, shadows) |
| `DESIGN.md` | Visual spec: tokens, component patterns, motion |

## Testing

```bash
# Unit tests
npm test

# Type-check only
npm run typecheck

# End-to-end (requires a running stack)
npx playwright install chromium
npm run test:e2e
```

Tests live alongside source files as `*.spec.tsx` (Vitest convention, see
`frontend/CLAUDE.md`).

## Plan of record

Active workstreams that affect this frontend:

- **W5** — extended sidebar filters (group composition, hard/soft services, interests)
- **W6** — pedestrian Guide panel: turn-by-turn manoeuvres, off-route handling
- **W7** — RU/EN i18n layer (i18next/react-i18next)

See `docs/specs/002-grodno-guide-rebuild/plan.md` and `tasks.md` for the current state.
Design constraints: `docs/specs/constitution.md`.
