"""Stairways from the local OSM extract, seeded as services rather than stops."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.paths import PLACES_DIR

from . import osm_tags, taxonomy
from .datasets import BBOX, COLUMNS
from .geofence import inside_project_area

CATEGORY = "лестница"

SOURCE_PREFIX = "osm:"

FACT_TAGS = ("handrail", "ramp", "step_count")

GRODNO_CITY_BBOX = {"south": 53.62, "west": 23.74, "north": 53.74, "east": 23.94}

Row = dict[str, str | None]


def _osmium() -> Any:
    """Import pyosmium on demand — not a declared dependency, so applying the written CSV
    needs no extract reader.
    """
    try:
        return importlib.import_module("osmium")
    except ImportError as exc:
        raise RuntimeError(
            "стадии steps нужен pyosmium (osmium): он не объявлен в pyproject — "
            "установите его отдельно (uv pip install osmium) или используйте уже "
            "записанный places_steps.csv"
        ) from exc


def line_center(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """The bounding-box centre of a way, the point Overpass calls its ``center``."""
    lat_lon = list(points)
    if not lat_lon:
        return None
    lons = [lon for lon, _ in lat_lon]
    lats = [lat for _, lat in lat_lon]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


def facts_of(tags: dict[str, str]) -> list[str]:
    """The stairway's tagged facts, each with its source, or an empty list when none are tagged."""
    return [
        f"{key}={tags[key].strip()} (OpenStreetMap)"
        for key in FACT_TAGS
        if tags.get(key, "").strip()
    ]


def element_to_row(element: dict[str, Any]) -> Row | None:
    """One OSM element → a 14-column places row, or None when it is not a stairway here."""
    tags = element.get("tags") or {}
    if tags.get("highway") != "steps":
        return None

    center = element.get("center") or {}
    lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None
    if not inside_project_area(float(lat), float(lon)):
        return None
    if not (
        BBOX["south"] <= float(lat) <= BBOX["north"] and BBOX["west"] <= float(lon) <= BBOX["east"]
    ):
        return None

    osm_type = element.get("type") or "way"
    osm_id = element.get("id")
    facts = facts_of(tags)
    return {
        "name": osm_tags.service_name(tags, CATEGORY) or "",
        "category": CATEGORY,
        "district": "",
        "town": (tags.get("addr:city") or tags.get("addr:village") or "").strip(),
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "blurb": "",
        "fun_fact": "",
        "fun_facts": json.dumps(facts, ensure_ascii=False) if facts else "[]",
        "opening_hours": "",
        "ticket_price": "",
        "visit_minutes": str(taxonomy.visit_minutes(CATEGORY)),
        "links": "[]",
        "source_url": f"{SOURCE_PREFIX}{osm_type}/{osm_id}",
    }


def _osm_id(source_url: str) -> int:
    return int(str(source_url).rsplit("/", 1)[-1])


def collect_rows(pbf_path: Path) -> list[Row]:
    """One filtered pass over the extract, rows sorted by OSM id so the file does not depend
    on the handler's heap order.
    """
    osmium: Any = _osmium()
    base = osmium.SimpleHandler
    rows: list[Row] = []

    class Collector(base):
        """Turns each stairway way into the same element shape the Overpass reader uses."""

        def way(self, way: Any) -> None:
            tags = {tag.k: tag.v for tag in way.tags}
            if tags.get("highway") != "steps":
                return
            center = line_center((node.lon, node.lat) for node in way.nodes)
            if center is None:
                return
            row = element_to_row(
                {
                    "type": "way",
                    "id": way.id,
                    "center": {"lat": center[0], "lon": center[1]},
                    "tags": tags,
                }
            )
            if row is not None:
                rows.append(row)

    handler = Collector()
    handler.apply_file(
        str(pbf_path), locations=True, filters=[osmium.filter.KeyFilter("highway", "steps")]
    )
    rows.sort(key=lambda row: _osm_id(str(row["source_url"])))
    return rows


def in_grodno_city(row: Row) -> bool:
    """Whether a row's centre stands inside the Grodno city box, for the count reported."""
    lat, lon = float(row["lat"] or 0.0), float(row["lon"] or 0.0)
    return (
        GRODNO_CITY_BBOX["south"] <= lat <= GRODNO_CITY_BBOX["north"]
        and GRODNO_CITY_BBOX["west"] <= lon <= GRODNO_CITY_BBOX["east"]
    )


def write_csv(rows: list[Row], path: Path) -> None:
    """Write the rows in the pipe-separated format the other datasets use, header commented."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(COLUMNS)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# " + "|".join(fieldnames) + "\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="|", quoting=csv.QUOTE_MINIMAL)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m db.seed.steps")
    parser.add_argument("--pbf", type=Path, default=None, help="OSM extract (default $OSM_PBF).")
    parser.add_argument(
        "--out", type=Path, default=PLACES_DIR / "places_steps.csv", help="Where to write the CSV."
    )
    args = parser.parse_args(argv)

    pbf = args.pbf or (Path(os.environ["OSM_PBF"]) if os.environ.get("OSM_PBF") else None)
    if pbf is None:
        raise SystemExit("стадии steps нужен PBF: передайте --pbf или задайте OSM_PBF")

    rows = collect_rows(pbf)
    write_csv(rows, args.out)
    with_handrail = sum(1 for row in rows if "handrail=yes" in str(row["fun_facts"]))
    print(
        f"[seed] steps: {len(rows)} лестниц записано "
        f"(в Гродно {sum(1 for row in rows if in_grodno_city(row))}, "
        f"с перилами {with_handrail}) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
