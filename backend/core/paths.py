"""Where the committed data lives, resolved from the backend root.

The datasets belong to the persistence side — `db/seed` is what reads them — so
they sit inside `db/`, beside the code that projects them into Postgres.
"""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BACKEND_DIR / "db" / "data"
GEO_DIR = DATA_DIR / "geo"
PLACES_DIR = DATA_DIR / "places"
PHOTOS_DIR = DATA_DIR / "photos"

TAXONOMY_CSV = DATA_DIR / "taxonomy.csv"
ITINERARIES_JSON = DATA_DIR / "itineraries.json"
