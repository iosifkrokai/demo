"""Overpass transport, district resolution and the pipe-delimited CSV writer.
Holds the endpoints, retry/failover, query templates and the reverse geocoder.
"""

from __future__ import annotations

import contextlib
import csv
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

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


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two lat/lon points."""
    earth_radius_m = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return earth_radius_m * 2 * math.asin(math.sqrt(a))


def nominatim_tile(lat: float, lon: float) -> tuple[int, int]:
    """z8 tile coordinates for Nominatim tile cache."""
    x = int((lon + 180) / 360 * 256)
    y = int(
        (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi)
        / 2
        * 256
    )
    return x, y


def nominatim_cache_path(lat: float, lon: float) -> Path:
    x, y = nominatim_tile(lat, lon)
    return CACHE_DIR / f"tile_z8_{x}_{y}.json"


def nominatim_reverse(
    lat: float, lon: float, cache: dict[tuple[float, float], str | None]
) -> str | None:
    """Reverse geocode via disk cache + Nominatim API (1 rps)."""
    key = (round(lat, 4), round(lon, 4))
    if key in cache:
        return cache[key]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tile_path = nominatim_cache_path(lat, lon)
    tile_cache: dict[str, str | None] = {}
    if tile_path.exists():
        with contextlib.suppress(Exception):
            tile_cache = json.loads(tile_path.read_text(encoding="utf-8"))

    if str(key) in tile_cache:
        cache[key] = tile_cache[str(key)]
        return cache[key]

    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "json", "accept-language": "ru"}
    headers = {"User-Agent": "GrodnoRegionPipeline/1.0 (codex-ai)"}
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        sys.stderr.write(f"[nominatim] {exc}\n")
        return None

    district = None
    addr = data.get("address", {})
    district = addr.get("county") or addr.get("district") or addr.get("region")
    if district:
        district = district.replace(" район", "").replace(" District", "").strip()

    cache[key] = district
    tile_cache[str(key)] = district
    with contextlib.suppress(Exception):
        tile_path.write_text(json.dumps(tile_cache, ensure_ascii=False), encoding="utf-8")

    time.sleep(1.1)
    return district


def resolve_district(
    lat: float,
    lon: float,
    tags: dict[str, str],
    cache: dict[tuple[float, float], str | None] | None = None,
    use_nominatim: bool = True,
) -> str:
    """District: OSM addr:district → Nominatim reverse → nearest raion centre.
    ``use_nominatim=False`` skips Nominatim; the value is cosmetic for routing.
    """
    if cache is None:
        cache = {}

    d = tags.get("addr:district", "")
    if d:
        return d.replace(" район", "").strip()

    if use_nominatim:
        district = nominatim_reverse(lat, lon, cache)
        if district:
            return district

    nearest = min(RAION_CENTRES, key=lambda r: haversine_m(lat, lon, *RAION_CENTRES[r]))
    return f"{nearest} район"


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
    for attempt in range(4):
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
                msg = f"[overpass] attempt {attempt+1}/{endpoint}: {exc}"
                sys.stderr.write(msg + "\n")
                if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                                    httpx.ConnectTimeout, httpx.RemoteProtocolError)):
                    dead.add(endpoint)
                if attempt == 3 and endpoint not in dead:
                    failed_endpoints.append(f"{endpoint} ({exc})")

        if all(ep in dead for ep in OVERPASS_ENDPOINTS):
            break
        if attempt < 3:
            wait = (2 ** attempt) * 5
            sys.stderr.write(f"[overpass] retry in {wait}s …\n")
            time.sleep(wait)

    for ep in failed_endpoints:
        sys.stderr.write(f"[overpass] FAILED: {ep}\n")
    sys.stderr.write(
        f"[overpass] all {len(OVERPASS_ENDPOINTS)} endpoints failed after 4 attempts"
        " — exiting\n"
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
