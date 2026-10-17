"""Reading `db/data/itineraries.json` — the seed's view of the curated walks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.errors import ItinerariesUnavailableError
from core.paths import ITINERARIES_JSON


def load_itineraries(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the curated file."""
    target = path or ITINERARIES_JSON
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ItinerariesUnavailableError(f"{target}: {exc}") from exc
    items = raw.get("itineraries")
    if not isinstance(items, list) or not items:
        raise ItinerariesUnavailableError(f"{target}: no itineraries")
    return items
