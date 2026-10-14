"""The data version: a digest of the committed datasets the seed projects."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.paths import DATA_DIR

VERSIONED_FILES: tuple[str, ...] = (
    "places/places_grodno_city.csv",
    "places/places_region.csv",
    "places/places_osm_raw.csv",
    "places/places_poi.csv",
    "places/places_curated.csv",
    "taxonomy.csv",
    "itineraries.json",
    "geo/areas.json",
    "geo/grodno_border.json",
    "photos/place_photos.json",
    "place_aliases.json",
    "place_merges.json",
)

_ABSENT = b"<absent>"


def content_version(data_dir: Path | None = None) -> str:
    """A short digest of every versioned dataset's path and bytes."""
    root = data_dir if data_dir is not None else DATA_DIR
    digest = hashlib.sha256()
    for rel in VERSIONED_FILES:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        path = root / rel
        digest.update(path.read_bytes() if path.is_file() else _ABSENT)
        digest.update(b"\x00")
    return digest.hexdigest()[:16]
