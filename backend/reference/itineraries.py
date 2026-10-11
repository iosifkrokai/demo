"""The curated itineraries file, and the one place that reads it.

It is a reference dataset like the taxonomy and the area registry: authored,
versioned, read at runtime. Attaching stored facts to its stops is a read of
`places`, which is why that half stays in `db/store/itineraries.py`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.errors import ItinerariesUnavailable
from core.paths import ITINERARIES_JSON

log = logging.getLogger(__name__)

ITINERARIES_PATH = ITINERARIES_JSON


def load_itineraries(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the curated file.

    A broken file is a deployment bug, raised loudly, never swallowed silently.
    """
    target = path or ITINERARIES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ItinerariesUnavailable(f"{target}: {exc}") from exc
    items = raw.get("itineraries")
    if not isinstance(items, list) or not items:
        raise ItinerariesUnavailable(f"{target}: no itineraries")
    return items
