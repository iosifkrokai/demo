"""The place repository: every query that reads or writes a `places` row.

The queries themselves are unchanged from the modules they came from
(`search.py`, `places.py`, `services.py`, and the pgvector write that used to sit
in `infra/embeddings.py`) — this file is their one home, not a rewrite. What is
new is the shape: methods on an object that owns the connection, returning
`db.models.place.Place` instead of a raw dict.

Callers in `planner/`, `agent/` and `api/` hold one of these, never a connection.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

import psycopg

from db.models.place import Place
from db.store.base import PostgresRepository
from db.store.columns import PLACE_SELECT, PLACE_WRITE_COLUMNS
from db.store.errors import DuplicateSource
from db.store.mappers import place_from_row, place_payload
from db.store.services import (
    DEFAULT_PROFILE,
    MAX_SERVICES,
    empty_answer,
    item_of,
    route_line,
    service_codes,
    threshold_for,
)

log = logging.getLogger(__name__)

MAX_PLACES = 5000

# The one query the service lookup runs. It lives beside the repository rather
# than in a class of its own now that the repository is where queries live.
_BESIDE_LINE = """
    WITH line AS (
        SELECT ST_GeomFromGeoJSON(%s) AS geom
    )
    SELECT p.id, p.source_url, p.name, p.category, p.town, p.lat, p.lon,
           p.opening_hours,
           ST_Distance(p.geom::geography, l.geom::geography) AS off_line_m,
           ST_LineLocatePoint(l.geom, p.geom) AS along_fraction,
           ST_Length(l.geom::geography) AS line_m
    FROM places p
    CROSS JOIN line l
    WHERE p.category = ANY(%s)
      AND p.geom IS NOT NULL
      AND ST_DWithin(p.geom::geography, l.geom::geography, %s)
    ORDER BY along_fraction ASC, off_line_m ASC
    LIMIT %s
"""

_WORD = re.compile(r"[а-яёa-z]{3,}\d*")


class PostgresPlaceRepository(PostgresRepository):
    """Reads, searches and the writes the seed and the admin both go through."""

    # --- browse -------------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        """Every place with coordinates, in a stable browse order.

        Ordered by category then town then name, so a re-seed does not reorder it.
        """
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places "
                "WHERE lat IS NOT NULL AND lon IS NOT NULL "
                "ORDER BY category, town, name LIMIT %s",
                (MAX_PLACES,),
            )
            places = [place_from_row(row) for row in cur.fetchall()]

        items = [place_payload(place) for place in places]
        log.info("places: %d points in the catalogue", len(items))
        return {
            "items": items,
            "total": len(items),
            "capped": len(items) >= MAX_PLACES,
        }

    # --- by identity --------------------------------------------------------

    def get_by_id(self, place_id: int) -> Place | None:
        """One place by id, or None."""
        places = self.get_by_ids([place_id])
        return places[0] if places else None

    def get_by_ids(self, ids: Sequence[int]) -> list[Place]:
        """The places with these ids, in the order asked for. Missing ids drop out."""
        if not ids:
            return []
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places WHERE id = ANY(%s)",
                (list(ids),),
            )
            by_id = {row["id"]: place_from_row(row) for row in cur.fetchall()}
        return [by_id[i] for i in ids if i in by_id]

    def get_by_source_urls(self, keys: Sequence[str]) -> tuple[dict[str, Place], list[str]]:
        """Fetch places by their `source_url`. Returns (places by key, missing keys)."""
        if not keys:
            return {}, []
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places WHERE source_url = ANY(%s)",
                (list(keys),),
            )
            found = {row["source_url"]: place_from_row(row) for row in cur.fetchall()}
        return found, [key for key in keys if key not in found]

    # --- search -------------------------------------------------------------

    def keyword_search(self, query: str, limit: int = 20) -> list[Place]:
        """Exact + prefix keyword search on name, town and district.

        The fallback when embeddings are unavailable; ILIKE + pg_trgm carries the
        inflection.
        """
        words = [w for w in _WORD.findall(query.lower()) if len(w) >= 3]
        if not words:
            return []

        per_word = (
            "name ILIKE %s OR town ILIKE %s OR district ILIKE %s",
            "name %%> %s OR town %%> %s OR district %%> %s",
        )
        per_word_sql = " OR ".join(per_word)
        conditions = " OR ".join([per_word_sql] * len(words))
        word_sim = [
            "GREATEST(similarity(name, %s), word_similarity(%s, name), "
            "similarity(town, %s), word_similarity(%s, town))"
        ] * len(words)
        order_expr = "(" + ") + (".join(word_sim) + ")"

        params: list[Any] = []
        for word in words:
            params.extend([f"%{word}%"] * 3)
            params.extend([word] * 3)
        params.extend(word for word in words for _ in range(4))
        params.append(limit)

        sql = f"""
            SELECT {PLACE_SELECT}
              FROM places
             WHERE {conditions}
             ORDER BY {order_expr} DESC
             LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [place_from_row(row) for row in cur.fetchall()]

    def name_match(self, query: str, limit: int = 5) -> list[tuple[Place, float]]:
        """Name-only trigram search, best first, with the similarity it scored."""
        words = [w for w in re.findall(r"[а-яёa-z]{2,}", query.lower()) if len(w) >= 2]
        if not words:
            return []

        conditions = " OR ".join(["name ILIKE %s OR name %%> %s"] * len(words))
        word_sims = [
            "GREATEST(similarity(name, %s), word_similarity(%s, name))"
        ] * len(words)
        order_expr = "(" + ") + (".join(word_sims) + ")"

        params: list[Any] = []
        for word in words:
            params.extend([f"%{word}%", word])
        for word in words:
            params.extend([word, word])
        params.append(limit)

        sql = f"""
            SELECT {PLACE_SELECT}, ({order_expr}) AS _name_sim
              FROM places
             WHERE {conditions}
             ORDER BY _name_sim DESC
             LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [(place_from_row(row), float(row["_name_sim"])) for row in cur.fetchall()]

    def nearby(
        self, lat: float, lon: float, radius_km: float = 12.0, limit: int = 50
    ) -> list[Place]:
        """Places within `radius_km` of a point, nearest first.

        Guarantees the pool holds something around a known point or named town.
        """
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places "
                "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                "ORDER BY (lat - %s) * (lat - %s) + (lon - %s) * (lon - %s) "
                "LIMIT %s",
                (
                    lat - dlat, lat + dlat, lon - dlon, lon + dlon,
                    lat, lat, lon, lon, limit,
                ),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def by_embedding(
        self,
        qvec: Sequence[float],
        limit: int = 50,
        region_bbox: Sequence[float] | None = None,
    ) -> list[tuple[Place, float]]:
        """Top-K by vector cosine distance, each with the distance it scored.

        Category filtering is left to the caller as a soft score boost, not a WHERE.
        """
        bbox_clause = ""
        bbox_params: list[Any] = []
        if region_bbox and len(region_bbox) == 4:
            south, west, north, east = region_bbox
            bbox_clause = "   AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
            bbox_params = [west, south, east, north]

        sql = f"""
            SELECT {PLACE_SELECT},
                   embedding <=> %s::vector AS cosine_dist
              FROM places
             WHERE embedding IS NOT NULL
            {bbox_clause}
             ORDER BY embedding <=> %s::vector
             LIMIT %s
        """
        params: list[Any] = [qvec, *bbox_params, qvec, limit]
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [
                (place_from_row(row), float(row["cosine_dist"])) for row in cur.fetchall()
            ]

    def by_category(self, codes: Sequence[str], limit: int) -> list[Place]:
        """Places in any of these DB category values, in the pool's own order."""
        if not codes:
            return []
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places "
                "WHERE category = ANY(%s) LIMIT %s",
                (list(codes), limit),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def category_of(self, ids: Sequence[int]) -> dict[int, str]:
        """`{place_id: category}` for these ids — the avoid-filter reads it."""
        if not ids:
            return {}
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, category FROM places WHERE id = ANY(%s)", (list(ids),)
            )
            return {row["id"]: row["category"] for row in cur.fetchall()}

    def with_category(
        self, codes: Iterable[str], *, lat_lon_only: bool = False
    ) -> list[Place]:
        """Places in these categories — the quality layer's reference set.

        `lat_lon_only` keeps the rows the haversine cross-check needs.
        """
        wanted = list(codes)
        if not wanted:
            return []
        clause = " AND lat IS NOT NULL AND lon IS NOT NULL" if lat_lon_only else ""
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places WHERE category = ANY(%s){clause}",
                (wanted,),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def embeddings_by_ids(self, ids: Sequence[int]) -> dict[int, list[float]]:
        """Vectors for the MMR diversity step; ids with no vector are skipped."""
        if not ids:
            return {}
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, embedding::text FROM places "
                "WHERE id = ANY(%s) AND embedding IS NOT NULL",
                (list(ids),),
            )
            out: dict[int, list[float]] = {}
            for place_id, raw in cur.fetchall():
                if isinstance(raw, str):
                    try:
                        out[place_id] = [
                            float(x) for x in raw.strip("[]").split(",") if x.strip()
                        ]
                    except ValueError:
                        continue
                else:
                    arr = raw.tolist() if hasattr(raw, "tolist") else raw
                    out[place_id] = list(arr)
            return out

    # --- services beside a line ---------------------------------------------

    def services_along(
        self,
        shape: dict,
        *,
        categories: Sequence[str] | None = None,
        profile: str = DEFAULT_PROFILE,
        max_off_line_m: float | None = None,
        limit: int = MAX_SERVICES,
    ) -> dict[str, Any]:
        """Services lying beside a route line, ordered along it.

        Never mixes a service into the route's stops; the caller shows it.
        """
        line = route_line(shape)
        codes = service_codes(categories)
        if not codes:
            return empty_answer(
                codes, profile, max_off_line_m, reason="no_service_categories"
            )

        threshold = threshold_for(profile, max_off_line_m)
        cap = max(1, int(limit))
        with self._cursor() as cur:
            cur.execute(_BESIDE_LINE, (json.dumps(line), codes, threshold, cap))
            rows = [dict(row) for row in cur.fetchall()]

        line_m = float(rows[0]["line_m"]) if rows else 0.0
        items = [item_of(row, line_m) for row in rows]
        log.info(
            "services_along: %d of at most %d beside a %.0f m line (%.0f m off-line gate)",
            len(items), cap, line_m, threshold,
        )
        return {
            "items": items,
            "measured": "distance_to_line",
            "not_measured": "detour_walking_time",
            "detour_confirmed": False,
            "profile": profile,
            "categories": codes,
            "max_off_line_m": threshold,
            "line_m": round(line_m),
            "result_cap": cap,
            "capped": len(items) >= cap,
        }

    # --- health and the embedding backfill ----------------------------------

    def ping(self) -> bool:
        """True when the database answers. Never raises."""
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception as exc:
            log.warning("health.db err=%s", exc)
            return False

    def rows_missing_embedding(self) -> list[tuple[int, str, str | None]]:
        """Every row with no vector, oldest first — the backfill's work list."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, name, blurb FROM places "
                "WHERE embedding IS NULL ORDER BY id",
            )
            return [(row["id"], row["name"], row["blurb"]) for row in cur.fetchall()]

    def upsert_embedding(self, place_id: int, vector: Sequence[float]) -> None:
        """Store one vector. The `::vector` cast is what pgvector needs."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE places SET embedding = %s::vector WHERE id = %s",
                (str(list(vector)), place_id),
            )

    # --- the admin's reads and writes ---------------------------------------

    def list_places_paged(
        self, *, q: str = "", category: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[Place], int]:
        """Admin browse: filtered by free text and category, name order.

        Returns the page and the total that matched, so the panel can paginate
        without a second query of its own.
        """
        pattern = f"%{q}%"
        where = (
            "(%s = '' OR name ILIKE %s OR town ILIKE %s OR district ILIKE %s) "
            "AND (%s = '' OR category = %s)"
        )
        args = (q, pattern, pattern, pattern, category, category)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places WHERE {where} "
                "ORDER BY name, id LIMIT %s OFFSET %s",
                (*args, limit, offset),
            )
            places = [place_from_row(row) for row in cur.fetchall()]
            cur.execute(f"SELECT count(*) AS n FROM places WHERE {where}", args)
            total = int(cur.fetchone()["n"])
        return places, total

    def create_place(self, fields: dict[str, Any]) -> Place:
        """Insert a hand-curated point. Raises :class:`DuplicateSource` on a clash.

        A category supplied by an admin is a curated one by definition, so the
        row is stamped `curated` and the trigger protects it from the seed.
        """
        cols = [c for c in PLACE_WRITE_COLUMNS if c in fields]
        extra_cols = ["category_source"] if "category" in cols else []
        values_sql = ", ".join(["%s"] * (len(cols) + len(extra_cols)))
        params = [fields[c] for c in cols]
        if extra_cols:
            params.append("curated")
        with self._cursor() as cur:
            try:
                cur.execute(
                    f"INSERT INTO places ({', '.join([*cols, *extra_cols])}) "
                    f"VALUES ({values_sql}) RETURNING {PLACE_SELECT}",
                    tuple(params),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateSource(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        assert row is not None
        return place_from_row(row)

    def update_place(
        self, place_id: int, fields: dict[str, Any]
    ) -> Place | None:
        """Patch a point; ``None`` when the id is unknown.

        A category change is an admin decision, so it runs with the
        curated-category guard explicitly lowered — and re-stamps `curated`.
        """
        cols = [c for c in PLACE_WRITE_COLUMNS if c in fields]
        if not cols:
            return self.get_by_id(place_id)
        sets = [f"{c} = %s" for c in cols]
        params: list[Any] = [fields[c] for c in cols]
        if "category" in cols:
            sets.append("category_source = 'curated'")
        params.append(place_id)
        with self._tx_cursor() as cur:
            if "category" in cols:
                cur.execute(
                    "SELECT set_config('grodno.allow_curated_category_change', "
                    "'on', true)"
                )
            try:
                cur.execute(
                    f"UPDATE places SET {', '.join(sets)} WHERE id = %s "
                    f"RETURNING {PLACE_SELECT}",
                    tuple(params),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateSource(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        return place_from_row(row) if row is not None else None

    def delete_place(self, place_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM places WHERE id = %s", (place_id,))
            return cur.rowcount > 0
