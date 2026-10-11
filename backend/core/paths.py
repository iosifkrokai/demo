"""Where the committed data lives, resolved from the backend root."""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BACKEND_DIR / "data"
GEO_DIR = DATA_DIR / "geo"
PLACES_DIR = DATA_DIR / "places"
PHOTOS_DIR = DATA_DIR / "photos"

TAXONOMY_CSV = DATA_DIR / "taxonomy.csv"
ITINERARIES_JSON = DATA_DIR / "itineraries.json"
