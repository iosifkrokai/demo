"""The data version: a digest of the committed datasets the seed projects.

Data is code. The CSVs under `places/`, plus `taxonomy.csv`, `itineraries.json`,
the `geo/` geometry and the committed photos, are the source; the database is a
projection rebuilt from them by ``python -m db.seed``. The version is the *content*
of those files — not a hand-bumped constant and not an mtime: re-running the seed
over unchanged files reproduces the same digest, while a change to any projected
input moves it. That is what lets a re-seed invalidate anything measured against
the old rows.

Pure and offline: the standard library and `core.paths` only, so the seed's
``--dry-run`` (and the store that reads the stamped version back) need no driver.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.paths import DATA_DIR

# The committed inputs the projection is built from, relative to `db/data` and in
# a fixed order. Every file the seed reads is here; a non-dataset file that lives
# beside them (a README) is not, so editing prose does not move the version. A
# missing optional file is hashed as absent, so creating it later moves it too.
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
)

_ABSENT = b"<absent>"


def content_version(data_dir: Path | None = None) -> str:
    """A short digest of every versioned dataset's path and bytes.

    Read once per seed (and per test); nothing on the request path calls it. Two
    runs over the same files give the same string; any change to a file's content
    (or to whether it exists) gives another.
    """
    root = data_dir if data_dir is not None else DATA_DIR
    digest = hashlib.sha256()
    for rel in VERSIONED_FILES:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        path = root / rel
        digest.update(path.read_bytes() if path.is_file() else _ABSENT)
        digest.update(b"\x00")
    return digest.hexdigest()[:16]
