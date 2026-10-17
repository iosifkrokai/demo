"""The place repository: every query that reads or writes a `places` row."""

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
from db.store.columns import (
    EMBEDDED_TEXT_COLUMNS,
    PLACE_COLUMNS,
    PLACE_SELECT,
    PLACE_WRITE_COLUMNS,
)
from db.store.errors import DuplicateSourceError, StorageUnavailableError
from db.store.mappers import place_from_row, place_payload
from db.store.services import (
    DEFAULT_PROFILE,
    MAX_SERVICES,
    MAX_STEPS_ON_ROUTE,
    STEP_CATEGORY,
    STEP_ON_LINE_M,
    empty_answer,
    item_of,
    named_codes,
    route_line,
    service_codes,
    threshold_for,
)

log = logging.getLogger(__name__)

MAX_PLACES = 5000

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

_ALIAS_SELECT = ", ".join(f"p.{column}" for column in PLACE_COLUMNS)


def _in_area(alias: str, area: str | None) -> tuple[str, list[str]]:
    """SQL and its parameter that keep a search inside one area's polygon, not its bounding
    box: ``grodno-district`` holds the city of Grodno as a hole.
    """
    if not area:
        return "", []
    clause = (
        f" AND EXISTS (SELECT 1 FROM areas a WHERE a.code = %s "
        f"AND ST_Intersects({alias}.geom, a.geom))"
    )
    return clause, [area]


class PostgresPlaceRepository(PostgresRepository):
    """Reads, searches and the writes the seed and the admin both go through."""

    def catalog(self) -> dict[str, Any]:
        """Every place with coordinates, in a stable browse order."""
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

    def keyword_search(self, query: str, limit: int = 20) -> list[Place]:
        """Exact + prefix keyword search on name, town and district."""
        words = _WORD.findall(query.lower())
        if not words:
            return []

        per_word = (
            "name ILIKE %s OR town ILIKE %s OR district ILIKE %s",
            "name %%> %s OR town %%> %s OR district %%> %s",
        )
        per_word_sql = " OR ".join(per_word)
        conditions = " OR ".join([per_word_sql] * len(words))
        word_sim = [
            (
                "GREATEST(similarity(name, %s), word_similarity(%s, name), "
                "similarity(town, %s), word_similarity(%s, town))"
            )
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

    def by_alias(
        self,
        query: str,
        limit: int = 5,
        area: str | None = None,
    ) -> list[tuple[Place, float]]:
        """Trigram search over the other names a place answers to, best first."""
        words = re.findall(r"[а-яёa-z]{2,}", query.lower())
        if not words:
            return []

        sim_expr = " + ".join(
            ["GREATEST(similarity(a.alias, %s), word_similarity(%s, a.alias))"] * len(words)
        )
        conditions = " OR ".join(["a.alias ILIKE %s OR a.alias %%> %s"] * len(words))
        area_clause, area_params = _in_area("p", area)

        sim_params = [word for word in words for _ in range(2)]
        cond_params = [value for word in words for value in (f"%{word}%", word)]
        params: list[Any] = [*sim_params, *cond_params, *area_params, limit]

        sql = f"""
            SELECT {_ALIAS_SELECT}, s.alias_sim
              FROM places p
              JOIN (
                  SELECT a.place_id, MAX(({sim_expr})) AS alias_sim
                    FROM place_aliases a
                   WHERE {conditions}
                   GROUP BY a.place_id
              ) s ON s.place_id = p.id{area_clause}
             ORDER BY s.alias_sim DESC
             LIMIT %s
        """
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [(place_from_row(row), float(row["alias_sim"])) for row in cur.fetchall()]

    def nearby(
        self,
        lat: float,
        lon: float,
        radius_km: float = 12.0,
        limit: int = 50,
        area: str | None = None,
    ) -> list[Place]:
        """Places within `radius_km` of a point, nearest first."""
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        area_clause, area_params = _in_area("places", area)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places "
                "WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                f"{area_clause} "
                "ORDER BY (lat - %s) * (lat - %s) + (lon - %s) * (lon - %s) "
                "LIMIT %s",
                (
                    lat - dlat,
                    lat + dlat,
                    lon - dlon,
                    lon + dlon,
                    *area_params,
                    lat,
                    lat,
                    lon,
                    lon,
                    limit,
                ),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def by_embedding(
        self,
        qvec: Sequence[float],
        limit: int = 50,
        area: str | None = None,
    ) -> list[tuple[Place, float]]:
        """Top-K by vector cosine distance, each with the distance it scored."""
        area_clause, area_params = _in_area("places", area)
        sql = f"""
            SELECT {PLACE_SELECT},
                   embedding <=> %s::vector AS cosine_dist
              FROM places
             WHERE embedding IS NOT NULL
            {area_clause}
             ORDER BY embedding <=> %s::vector
             LIMIT %s
        """
        params: list[Any] = [qvec, *area_params, qvec, limit]
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [(place_from_row(row), float(row["cosine_dist"])) for row in cur.fetchall()]

    def by_category(self, codes: Sequence[str], limit: int, area: str | None = None) -> list[Place]:
        """Places in any of these DB category values, in the pool's own order."""
        if not codes:
            return []
        area_clause, area_params = _in_area("places", area)
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {PLACE_SELECT} FROM places WHERE category = ANY(%s){area_clause} LIMIT %s",
                (list(codes), *area_params, limit),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def outside_area(self, ids: Sequence[int], code: str) -> list[int]:
        """The ids that do not stand inside the area's polygon — the harness's own check."""
        if not ids:
            return []
        with self._cursor() as cur:
            cur.execute(
                "SELECT p.id FROM places p JOIN areas a ON a.code = %s "
                " WHERE p.id = ANY(%s) AND (a.geom IS NULL OR NOT ST_Intersects(p.geom, a.geom))",
                (code, list(ids)),
            )
            return [int(row["id"]) for row in cur.fetchall()]

    def with_category(self, codes: Iterable[str], *, lat_lon_only: bool = False) -> list[Place]:
        """Places in these categories — the quality layer's reference set."""
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

    def places_along(
        self,
        shape: dict[str, Any],
        *,
        categories: Sequence[str] | None = None,
        profile: str = DEFAULT_PROFILE,
        max_off_line_m: float | None = None,
        limit: int = MAX_SERVICES,
    ) -> dict[str, Any]:
        """Places of any named categories beside a route line, ordered along it; role is
        not consulted, and nothing here is a detour — only «beside the line».
        """
        return self._beside_line(
            shape,
            named_codes(categories),
            profile,
            max_off_line_m,
            limit,
            reason="no_category_codes",
        )

    def nearby_many(
        self,
        points: Sequence[tuple[float, float]],
        radius_km: float = 12.0,
        limit_per_stop: int = 50,
    ) -> list[Place]:
        """Places near any of the points, nearest first within each point's own search."""
        if not points:
            return []
        lats = [float(point[0]) for point in points]
        lons = [float(point[1]) for point in points]
        qualified = ", ".join(f"places.{column}" for column in PLACE_COLUMNS)
        with self._cursor() as cur:
            cur.execute(
                f"""
                SELECT {qualified}
                  FROM unnest(%s::float8[], %s::float8[]) AS stops(lat, lon)
                  CROSS JOIN LATERAL (
                      SELECT * FROM places
                       WHERE places.lat BETWEEN stops.lat - %s / 111.0
                                            AND stops.lat + %s / 111.0
                         AND places.lon BETWEEN stops.lon - %s
                                                / (111.0 * GREATEST(0.2, cos(radians(stops.lat))))
                                            AND stops.lon + %s
                                                / (111.0 * GREATEST(0.2, cos(radians(stops.lat))))
                       ORDER BY (places.lat - stops.lat) * (places.lat - stops.lat)
                              + (places.lon - stops.lon) * (places.lon - stops.lon)
                       LIMIT %s
                  ) AS places
                """,
                (lats, lons, radius_km, radius_km, radius_km, radius_km, limit_per_stop),
            )
            return [place_from_row(row) for row in cur.fetchall()]

    def steps_along(
        self,
        shape: dict[str, Any],
        *,
        profile: str = DEFAULT_PROFILE,
        max_off_line_m: float | None = None,
        limit: int = MAX_STEPS_ON_ROUTE,
    ) -> dict[str, Any]:
        """Stairways the drawn line runs over, re-read from the walk's own geometry: a
        normal walk keeps its stairways off the line, so a hit means the route climbs one.
        """
        gate = STEP_ON_LINE_M if max_off_line_m is None else max_off_line_m
        return self._beside_line(
            shape,
            [STEP_CATEGORY],
            profile,
            gate,
            limit,
            reason="no_stairways",
        )

    def services_along(
        self,
        shape: dict[str, Any],
        *,
        categories: Sequence[str] | None = None,
        service_category_codes: Sequence[str] = (),
        profile: str = DEFAULT_PROFILE,
        max_off_line_m: float | None = None,
        limit: int = MAX_SERVICES,
    ) -> dict[str, Any]:
        """Services beside a route line — the default the API and the planner ask for."""
        codes = service_codes(categories, service_category_codes)
        return self._beside_line(
            shape,
            codes,
            profile,
            max_off_line_m,
            limit,
            reason="no_service_categories",
        )

    def _beside_line(
        self,
        shape: dict[str, Any],
        codes: Sequence[str],
        profile: str,
        max_off_line_m: float | None,
        limit: int,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """One line query for both the service default and any named category."""
        line = route_line(shape)
        if not codes:
            return empty_answer(codes, profile, max_off_line_m, reason=reason)

        threshold = threshold_for(profile, max_off_line_m)
        cap = max(1, int(limit))
        with self._cursor() as cur:
            cur.execute(_BESIDE_LINE, (json.dumps(line), list(codes), threshold, cap))
            rows = [dict(row) for row in cur.fetchall()]

        line_m = float(rows[0]["line_m"]) if rows else 0.0
        items = [item_of(row, line_m) for row in rows]
        log.info(
            "places_along: %d of at most %d beside a %.0f m line (%.0f m off-line gate)",
            len(items),
            cap,
            line_m,
            threshold,
        )
        return {
            "items": items,
            "measured": "distance_to_line",
            "not_measured": "detour_walking_time",
            "detour_confirmed": False,
            "profile": profile,
            "categories": list(codes),
            "max_off_line_m": threshold,
            "line_m": round(line_m),
            "result_cap": cap,
            "capped": len(items) >= cap,
        }

    def ping(self) -> bool:
        """True when the database answers. Never raises."""
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception as exc:
            log.warning("health.db err=%s", exc)
            return False

    def rows_missing_embedding(self) -> list[tuple[int, str, str | None, str | None]]:
        """Every row with no vector, oldest first — the backfill's work list."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, name, category, town FROM places WHERE embedding IS NULL ORDER BY id",
            )
            return [
                (row["id"], row["name"], row["category"], row["town"]) for row in cur.fetchall()
            ]

    def clear_embeddings(self) -> int:
        """Forget every stored vector: the recipe changed, so all of them describe another text."""
        with self._cursor() as cur:
            cur.execute("UPDATE places SET embedding = NULL WHERE embedding IS NOT NULL")
            return cur.rowcount

    def upsert_embedding(self, place_id: int, vector: Sequence[float]) -> None:
        """Store one vector. The `::vector` cast is what pgvector needs."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE places SET embedding = %s::vector WHERE id = %s",
                (str(list(vector)), place_id),
            )

    def list_places_paged(
        self, *, q: str = "", category: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[Place], int]:
        """Admin browse: filtered by free text and category, name order."""
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
        """Insert a hand-curated point. Raises :class:`DuplicateSourceError` on a clash."""
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
                raise DuplicateSourceError(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        if row is None:
            raise StorageUnavailableError("insert returned no place row")
        return place_from_row(row)

    def update_place(self, place_id: int, fields: dict[str, Any]) -> Place | None:
        """Patch a point; ``None`` when the id is unknown."""
        cols = [c for c in PLACE_WRITE_COLUMNS if c in fields]
        if not cols:
            return self.get_by_id(place_id)
        sets = [f"{c} = %s" for c in cols]
        params: list[Any] = [fields[c] for c in cols]
        if "category" in cols:
            sets.append("category_source = 'curated'")
        if set(cols) & set(EMBEDDED_TEXT_COLUMNS):
            sets.append("embedding = NULL")
        params.append(place_id)
        with self._tx_cursor() as cur:
            if "category" in cols:
                cur.execute("SELECT set_config('grodno.allow_curated_category_change', 'on', true)")
            try:
                cur.execute(
                    f"UPDATE places SET {', '.join(sets)} WHERE id = %s RETURNING {PLACE_SELECT}",
                    tuple(params),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateSourceError(str(fields.get("source_url"))) from exc
            row = cur.fetchone()
        return place_from_row(row) if row is not None else None

    def delete_place(self, place_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM places WHERE id = %s", (place_id,))
            return cur.rowcount > 0
