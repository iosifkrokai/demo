FROM node:24-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . /app
ENV NODE_ENV=production

ARG VITE_AGENT_URL
ARG VITE_VALHALLA_URL
ARG VITE_NOMINATIM_URL
ARG VITE_CENTER_COORDS
ARG VITE_DEFAULT_COSTING_MODEL
ENV VITE_AGENT_URL=$VITE_AGENT_URL \
    VITE_VALHALLA_URL=$VITE_VALHALLA_URL \
    VITE_NOMINATIM_URL=$VITE_NOMINATIM_URL \
    VITE_CENTER_COORDS=$VITE_CENTER_COORDS \
    VITE_DEFAULT_COSTING_MODEL=$VITE_DEFAULT_COSTING_MODEL
RUN npm run build

# --- brotli -------------------------------------------------------------------
# `nginx:1.29-alpine` has no ngx_brotli, so the module is compiled here and
# copied into the runtime image. It is built from the *same* nginx version the
# base tag ships (read from the image itself, so a patch bump cannot desync the
# two) and with `--with-compat`, which the official image also builds with and
# which is what lets a dynamically loaded .so bind to its binary. The upstream
# revisions are pinned; the brotli submodule is the one ngx_brotli records.
FROM nginx:1.29-alpine AS nginx-brotli

ARG NGX_BROTLI_REV=a71f9312c2deb28875acc7bacfdd5695a111aa53
ARG BROTLI_REV=ed738e842d2fbdf2d6459e39267a633c4a9b2f5d

RUN set -eux; \
    apk add --no-cache --virtual .brotli-build-deps \
        build-base cmake linux-headers git pcre2-dev zlib-dev; \
    NGINX_VERSION="$(nginx -v 2>&1 | sed 's#.*/##')"; \
    cd /tmp; \
    curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o nginx.tar.gz; \
    tar -xzf nginx.tar.gz; \
    git clone --quiet https://github.com/google/ngx_brotli.git; \
    cd ngx_brotli; \
    git checkout --quiet "${NGX_BROTLI_REV}"; \
    git submodule update --init --recursive --quiet; \
    cd deps/brotli; \
    git checkout --quiet "${BROTLI_REV}"; \
    cmake -B out -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF . >/dev/null; \
    cmake --build out --config Release -j"$(nproc)" >/dev/null; \
    cd "/tmp/nginx-${NGINX_VERSION}"; \
    ./configure --with-compat --add-dynamic-module=/tmp/ngx_brotli >/dev/null; \
    make -j"$(nproc)" modules >/dev/null; \
    mkdir -p /tmp/brotli-out; \
    cp objs/ngx_http_brotli_filter_module.so \
       objs/ngx_http_brotli_static_module.so /tmp/brotli-out/

FROM nginx:1.29-alpine

# Load the module in the main context (a `load_module` line is not allowed inside
# the `http{}` block where the site config lives), then enable `brotli` in the
# server config copied below.
COPY --from=nginx-brotli /tmp/brotli-out/*.so /usr/lib/nginx/modules/
RUN printf '%s\n' \
        'load_module /usr/lib/nginx/modules/ngx_http_brotli_filter_module.so;' \
        'load_module /usr/lib/nginx/modules/ngx_http_brotli_static_module.so;' \
        > /etc/nginx/modules/brotli.conf \
    && sed -i '1i include /etc/nginx/modules/*.conf;' /etc/nginx/nginx.conf

COPY --from=builder /app/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/build /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
