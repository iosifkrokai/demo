"""One-shot scraper: fills places table from planetabelarus.by sight pages.

NOT a service. NOT a cron. Run once to populate; safe to re-run (cache + ON CONFLICT).

Usage:
    python scripts/parse_places.py

Respects:
    - robots.txt via urllib.robotparser.RobotFileParser (raises on disallowed paths).
    - 1.5s delay between requests.
    - Disk cache under ./cache so re-runs during debugging do not re-hit the site.
    - Honest User-Agent with contact info.

Honors the user spec's "geocode manually before inserting" rule by:
    - Reading lat/lon directly from the page's inline JS `initialCenter = [LON, LAT]`
      (server-rendered, full precision), so we never trust a separate Nominatim lookup.
    - Applying a bbox filter (Grodno Oblast) as the manual sanity check.
"""

import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import psycopg
import requests
from bs4 import BeautifulSoup

BASE = "https://planetabelarus.by"
SITEMAP = f"{BASE}/sitemap-iblock-6.xml"
UA = "grodno-poc-collector/1.0 (research POC; contact: dev@example.com)"
HEADERS = {"User-Agent": UA}
CACHE = Path(os.environ.get("PARSER_CACHE", "./cache"))
DELAY_S = float(os.environ.get("PARSER_DELAY", "1.5"))

# Grodno Oblast bbox (OSM relation 59173, approximate).
# (min_lat, min_lon, max_lat, max_lon)
BBOX = (52.85, 23.50, 54.10, 26.55)

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")

# `const initialCenter = [23.82318289, 53.67719794];` — full precision, [lon, lat].
INITIAL_CENTER_RE = re.compile(
    r"const\s+initialCenter\s*=\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]"
)


def fetch(url: str, rp: RobotFileParser) -> str:
    if not rp.can_fetch("*", url):
        raise RuntimeError(f"disallowed by robots.txt: {url}")
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    p.write_text(r.text, encoding="utf-8")
    time.sleep(DELAY_S)
    return r.text


def parse_detail(url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.publications")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    desc = None
    meta_desc = soup.select_one("meta[name='description']")
    if meta_desc and meta_desc.get("content"):
        desc = meta_desc["content"].strip()
    else:
        cnt = soup.select_one(".place.sight-detail div.cnt")
        if cnt:
            first_p = cnt.find("p")
            if first_p:
                desc = first_p.get_text(" ", strip=True)

    photo_url = None
    og = soup.select_one("meta[property='og:image']")
    if og and og.get("content"):
        photo_url = urljoin(BASE, og["content"])

    m = INITIAL_CENTER_RE.search(html)
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))

    # bbox sanity filter — manual "verify the result before inserting" step
    if not (BBOX[0] <= lat <= BBOX[2] and BBOX[1] <= lon <= BBOX[3]):
        return None

    return {
        "name": name,
        "description": desc,
        "lat": lat,
        "lon": lon,
        "photo_url": photo_url,
        "source_url": url,
    }


def main() -> None:
    rp = RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    rp.read()

    print(f"fetching sitemap {SITEMAP}...")
    r = requests.get(SITEMAP, headers=HEADERS, timeout=30)
    r.raise_for_status()
    urls = re.findall(r"<loc>([^<]+)</loc>", r.text)
    urls = [u for u in urls if "/sights/" in u.rstrip("/") and not u.rstrip("/").endswith("/sights")]
    print(f"{len(urls)} sight URLs")

    inserted = updated = skipped = failed = 0
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for i, url in enumerate(urls, 1):
                try:
                    html = fetch(url, rp)
                    rec = parse_detail(url, html)
                except RuntimeError as e:
                    print(f"[{i}/{len(urls)}] skip {url}: {e}")
                    skipped += 1
                    continue
                except Exception as e:
                    print(f"[{i}/{len(urls)}] fail {url}: {e}")
                    failed += 1
                    continue
                if not rec:
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO places (name, description, lat, lon, photo_url, source_url)
                    VALUES (%(name)s, %(description)s, %(lat)s, %(lon)s, %(photo_url)s, %(source_url)s)
                    ON CONFLICT (source_url) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        photo_url = EXCLUDED.photo_url
                    RETURNING (xmax = 0) AS was_insert
                    """,
                    rec,
                )
                was_insert = cur.fetchone()[0]
                if was_insert:
                    inserted += 1
                else:
                    updated += 1
                if i % 50 == 0:
                    print(f"[{i}/{len(urls)}] inserted={inserted} updated={updated} skipped={skipped} failed={failed}")
        conn.commit()

    print(f"done. inserted={inserted} updated={updated} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
