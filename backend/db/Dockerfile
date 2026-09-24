FROM pgvector/pgvector:pg16

# pgvector ships with PostgreSQL + pgvector only, no PostGIS. Add PostGIS 3 from the
# official PGDG repo. base image is Debian; VERSION_CODENAME is read from /etc/os-release
# so this Dockerfile works across Debian versions the pgvector image might use.

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg lsb-release \
    && install -d /etc/apt/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*
