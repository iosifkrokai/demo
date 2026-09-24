# ─────────────────────────────────────────────────────────────────────────────
# Valhalla web-app (React/Vite frontend).
#
# Layer caching strategy:
#   1. Copy only package.json + package-lock.json first
#   2. npm ci — installs into node_modules (cached until deps change)
#   3. Copy source code last (invalidated on any .ts/.tsx change)
#
# The original Dockerfile did `COPY . /app` BEFORE `npm i`, which meant
# ANY change to ANY source file invalidated the npm install layer —
# adding 30-90s to every rebuild.
# ─────────────────────────────────────────────────────────────────────────────

FROM node:24-alpine AS builder
WORKDIR /app

# ── Layer 1: dependencies (cache-friendly) ──
# Copy ONLY the manifest. Any change to package.json/package-lock.json
# invalidates this layer; changes to .ts/.tsx files do NOT.
COPY package.json package-lock.json* ./
RUN npm ci

# ── Layer 2: source code + build ──
COPY . /app
ENV NODE_ENV=production
RUN npm run build

# ── Production: nginx serving the built static files ──
FROM nginx:1.29-alpine

# Drop the default nginx config and use the one we ship.
COPY --from=builder /app/nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder /app/build /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
