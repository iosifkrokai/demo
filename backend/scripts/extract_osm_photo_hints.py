"""Collect the photo hints OSM states for the points we actually show.

The full extract of Belarus carries ~50k objects with some photo-ish tag; the
guide has 3.7k points, of which ~450 line up with such an object. Committing the
whole scan would put a 3 MB file in the repo for nothing, so this script keeps
only the objects whose `<type>/<id>` appears in `places.source_url`.

What counts as a hint is exactly what OSM itself says, nothing inferred:

  * `wikidata`          — Q-id, to be resolved through Wikidata P18
  * `wikimedia_commons` — a Commons file or category, usable as is
  * `wikipedia`         — an article that usually carries a lead image
  * `image`             — a direct URL, kept verbatim and validated later, because
                          in the real data this tag also holds share links

Output: `data/osm_photo_hints.json`, keyed `<type>/<id>`, sorted, so a re-run on
the same PBF produces a byte-identical file.

Run from `backend/`:
    ./.venv/bin/python scripts/extract_osm_photo_hints.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from collections import Counter

import osmium
import psycopg

BACKEND = pathlib.Path(__file__).resolve().parent.parent
OUT = BACKEND / "data" / "osm_photo_hints.json"
PBF = os.environ.get("OSM_PBF", "/workspaces/demo/valhalla-data/belarus-latest.osm.pbf")
DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

WANTED = ("wikidata", "wikipedia", "webpage", "wikimedia_commons", "image")

# `osm:node/306067583`, `osm_poi:way/123` — the prefix is a seeding detail, the
# `type/id` tail is the OSM identity.
_OSM_REF = re.compile(r"^osm(?:_poi)?:([a-z]+/\d+)$")


def wanted_ids() -> set[str]:
    """The OSM identities our points claim to be — read from the DB, not typed in."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_url FROM places")
        rows = [r[0] for r in cur.fetchall()]

    ids: set[str] = set()
    for url in rows:
        m = _OSM_REF.match(url or "")
        if m:
            ids.add(m.group(1))
    return ids


class Collector(osmium.SimpleHandler):
    def __init__(self, wanted: set[str]) -> None:
        super().__init__()
        self.wanted = wanted
        self.found: dict[str, dict[str, str]] = {}

    def _keep(self, kind: str, obj) -> None:
        key = f"{kind}/{obj.id}"
        if key not in self.wanted:
            return
        tags = {t.k: t.v for t in obj.tags}
        hit = {k: tags[k] for k in WANTED if k in tags}
        if hit:
            self.found[key] = hit

    def node(self, n) -> None:
        self._keep("node", n)

    def way(self, w) -> None:
        self._keep("way", w)

    def relation(self, r) -> None:
        self._keep("relation", r)


def main() -> int:
    ids = wanted_ids()
    print(f"точек с OSM-ссылкой в source_url: {len(ids)}")
    collector = Collector(ids)
    collector.apply_file(PBF)

    found = {k: collector.found[k] for k in sorted(collector.found)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        json.dump(found, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    tags = Counter(t for v in found.values() for t in v)
    print(f"объектов с подсказкой среди наших точек: {len(found)}  {dict(tags)}")
    print(f"без ответа (в OSM подсказки нет): {len(ids) - len(found)}")
    print(f"-> {OUT.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
