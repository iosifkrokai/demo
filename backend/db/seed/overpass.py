"""Overpass transport, district resolution and the pipe-delimited CSV writer.
Holds the endpoints, retry/failover, query templates and the raion fallback.
"""

from __future__ import annotations

import csv
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from .districts import district_at

DEFAULT_BBOX: tuple[float, float, float, float] = (23.35, 52.75, 27.00, 54.80)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "nominatim"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

OVERPASS_HTTP_TIMEOUT_S = 360.0

SIGHT_QUERY = """
[out:json][timeout:300];
(
  // Castles, manors, monasteries, memorials, monuments, ruins
  nwr["historic"~"castle|manor|monastery|memorial|monument|ruins|archaeological_site"]({south},{west},{north},{east});
  // Museums and attractions
  nwr["tourism"~"museum|attraction"]({south},{west},{north},{east});
  // Places of worship
  nwr["amenity"="place_of_worship"]({south},{west},{north},{east});
  // Church / castle buildings
  nwr["building"~"church|castle"]({south},{west},{north},{east});
);
out center;
"""

SERVICE_QUERY = """
[out:json][timeout:300];
(
  // Coffee and quick food
  nwr["amenity"="cafe"]({south},{west},{north},{east});
  nwr["amenity"="fast_food"]({south},{west},{north},{east});
  // Sit-down restaurants
  nwr["amenity"="restaurant"]({south},{west},{north},{east});
  // Public toilets
  nwr["amenity"="toilets"]({south},{west},{north},{east});
  // Accommodation
  nwr["tourism"~"hotel|hostel|guest_house"]({south},{west},{north},{east});
  // Public-transport boarding points: the tourist walking a route should see
  // where to get on a bus, trolleybus or tram instead of walking the whole way.
  nwr["highway"="bus_stop"]({south},{west},{north},{east});
  nwr["public_transport"="platform"]({south},{west},{north},{east});
  nwr["railway"="tram_stop"]({south},{west},{north},{east});
);
out center;
"""

RAION_CENTRES: dict[str, tuple[float, float]] = {
    "Гродненский": (53.6688, 23.8380),
    "Берестовицкий": (53.4420, 24.0353),
    "Волковысский": (53.1635, 24.4514),
    "Свислочский": (52.9296, 24.0939),
    "Мостовский": (53.4134, 24.5365),
    "Щучинский": (53.6055, 24.7406),
    "Островецкий": (54.6045, 25.9589),
    "Ошмянский": (54.4120, 25.7946),
    "Сморгонский": (54.4784, 26.3967),
    "Ивьевский": (54.1639, 25.7845),
    "Лидский": (53.8886, 25.3028),
    "Кореличский": (53.6010, 26.3025),
    "Новогрудский": (53.5958, 25.8269),
    "Дятловский": (53.4522, 25.4618),
    "Зельвенский": (53.1480, 24.8190),
    "Вороновский": (54.2560, 25.3032),
    "Слонимский": (53.0936, 25.3203),
}


_ATTEMPTS = 4


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two lat/lon points."""
    earth_radius_m = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return earth_radius_m * 2 * math.asin(math.sqrt(a))


def fill_town_names(
    rows: list[dict[str, Any]],
    resolve: Callable[[str], str | None],
) -> dict[str, int]:
    """Replace a Belarusian ``town`` with the Russian one, when the mapping knows it.

    ``addr:city`` in OSM is Belarusian («Гродна», «Слонім») while the product speaks
    Russian, so the card would show the wrong language. The mapping is a committed file
    (`db/data/town_names.json`), not a geocoder: reverse geocoding answers the same
    question but needs a network pass per value, and a village translated by guesswork is
    a wrong fact on a card.

    ``resolve`` is injected so this is testable offline. A value the mapping does not
    know is left exactly as it was — and counted, so the gap stays visible.
    """
    translated = kept = 0
    for row in rows:
        town = str(row.get("town") or "").strip()
        if not town:
            continue
        russian = resolve(town)
        if russian and russian != town:
            row["town"] = russian
            translated += 1
        else:
            kept += 1
    return {"translated": translated, "kept": kept}


def resolve_district(lat: float, lon: float, tags: dict[str, str]) -> str:
    """District: OSM ``addr:district`` when tagged, then the committed boundary, then the
    nearest raion centre — the last one only for a point outside every boundary we hold.
    """
    d = tags.get("addr:district", "")
    if d:
        return d.replace(" район", "").strip()

    inside = district_at(lat, lon)
    if inside:
        return inside

    nearest = min(RAION_CENTRES, key=lambda r: haversine_m(lat, lon, *RAION_CENTRES[r]))
    return f"{nearest} район"


def fill_districts(
    rows: list[dict[str, Any]], previous: Mapping[str, str] | None = None
) -> dict[str, int]:
    """Give every row a district, offline.

    The Overpass rows carry no ``addr:district`` on these objects, so the committed
    boundary answers first: it is the only source that knows where a border runs, and a
    snapshot carried from an older fetch may hold the nearest-centre answer that boundary
    has since corrected. Outside every boundary the previous snapshot is the best evidence
    left — an exact match on ``source_url`` keeps the district it already had — and a
    genuinely new point falls back to the raion whose centre is nearest.

    A re-fetch that skipped this would write ``district: ""`` over every sight, which is
    why it runs before the file is written. Returns the counts for the fetch report.
    """
    known = previous or {}
    carried = derived = 0
    for row in rows:
        if str(row.get("district") or "").strip():
            continue
        lat, lon = float(row["lat"]), float(row["lon"])
        district = district_at(lat, lon)
        if district:
            derived += 1
        elif district := known.get(str(row.get("source_url"))):
            carried += 1
        else:
            district = resolve_district(lat, lon, {})
            derived += 1
        row["district"] = district
    return {"carried": carried, "derived": derived}


def build_overpass_query(template: str, bbox: tuple[float, float, float, float]) -> str:
    """Render an Overpass `template` for `bbox`, which is (west, south, east, north).
    Remaps to Overpass QL's (south, west, north, east); a malformed box raises.
    """
    west, south, east, north = bbox
    if not west < east:
        raise ValueError(f"bbox west {west} must be < east {east} — order is (W, S, E, N)")
    if not south < north:
        raise ValueError(f"bbox south {south} must be < north {north} — order is (W, S, E, N)")
    return template.format(south=south, west=west, north=north, east=east)


def fetch_overpass(
    query: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    mock: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Query Overpass with retry+backoff, or return mock data. None on total failure.
    Endpoints rotate; a timed-out endpoint is dropped, an HTTP error stays in rotation.
    """
    if dry_run:
        elements: list[dict[str, Any]] = (mock or {}).get("elements", [])
        return elements[:limit] if limit else elements

    request_timeout = httpx.Timeout(OVERPASS_HTTP_TIMEOUT_S, connect=30.0)

    failed_endpoints: list[str] = []
    dead: set[str] = set()
    for attempt in range(_ATTEMPTS):
        for endpoint in OVERPASS_ENDPOINTS:
            if endpoint in dead:
                continue
            try:
                resp = httpx.post(
                    endpoint,
                    data={"data": query},
                    timeout=request_timeout,
                    headers={"User-Agent": "GrodnoRegionPipeline/1.0 (codex-ai)"},
                )
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                return elements[:limit] if limit else elements
            except Exception as exc:
                msg = f"[overpass] attempt {attempt + 1}/{endpoint}: {exc}"
                sys.stderr.write(msg + "\n")
                if isinstance(
                    exc,
                    (
                        httpx.ConnectError,
                        httpx.ReadTimeout,
                        httpx.WriteTimeout,
                        httpx.ConnectTimeout,
                        httpx.RemoteProtocolError,
                    ),
                ):
                    dead.add(endpoint)
                if attempt == _ATTEMPTS - 1 and endpoint not in dead:
                    failed_endpoints.append(f"{endpoint} ({exc})")

        if all(ep in dead for ep in OVERPASS_ENDPOINTS):
            break
        if attempt < _ATTEMPTS - 1:
            wait = (2**attempt) * 5
            sys.stderr.write(f"[overpass] retry in {wait}s …\n")
            time.sleep(wait)

    for ep in failed_endpoints:
        sys.stderr.write(f"[overpass] FAILED: {ep}\n")
    sys.stderr.write(
        f"[overpass] all {len(OVERPASS_ENDPOINTS)} endpoints failed "
        f"after {_ATTEMPTS} attempts — exiting\n"
    )
    return None


def write_pipe_csv(rows: list[dict[str, Any]], path: Path, columns: Sequence[str]) -> None:
    """Write rows in pipe-separated CSV format, byte-stable across runs.
    The header is written as a ``#`` comment, matching the other dataset readers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(columns)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# " + "|".join(fieldnames) + "\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="|", quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            writer.writerow(row)
