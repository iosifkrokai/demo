"""Where the committed data lives, resolved from the backend root."""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BACKEND_DIR / "db" / "data"
GEO_DIR = DATA_DIR / "geo"
PLACES_DIR = DATA_DIR / "places"
PHOTOS_DIR = DATA_DIR / "photos"

TAXONOMY_CSV = DATA_DIR / "taxonomy.csv"
ITINERARIES_JSON = DATA_DIR / "itineraries.json"
EXCLUDED_NAMES_JSON = DATA_DIR / "excluded_names.json"
TOWN_NAMES_JSON = DATA_DIR / "town_names.json"
PLACE_ALIASES_JSON = DATA_DIR / "place_aliases.json"
PLACE_ALIASES_EN_JSON = DATA_DIR / "place_aliases_en.json"
PLACE_MERGES_JSON = DATA_DIR / "place_merges.json"
