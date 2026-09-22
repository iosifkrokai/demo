"""One-shot scraper: fills places table from planetabelarus.by sight pages.

NOT a service. NOT a cron. Run once to populate; safe to re-run (cache + ON CONFLICT).

Usage:
    python scripts/parse_places.py
    PARSER_DELAY=0.5 PARSER_WORKERS=6 python scripts/parse_places.py

Respects:
    - robots.txt via urllib.robotparser.RobotFileParser (raises on disallowed paths).
    - Disk cache under ./cache so re-runs during debugging do not re-hit the site.
    - Honest User-Agent with contact info.
    - Per-worker delay between requests (PARSER_DELAY, default 0.5s). With the default
      4 workers, peak throughput is ~8 req/s — polite for a one-shot scrape of a small
      regional CMS. Tighten PARSER_DELAY or raise PARSER_WORKERS at your own risk.

Crawl strategy: planetabelarus.by exposes a server-side filtered listing at
`/sights/filter/location-is-0000000275/apply/?PAGEN_1=N` (0000000275 = Гродненская
область in their Bitrix taxonomy). Other query-param filter shapes return
mixed Belarus-wide results in HTML; this one returns only Grodno-region pages,
saving ~50x network traffic vs. crawling the full Belarus sitemap and bbox-filtering
later.

Coordinates come from the page's inline JS `const initialCenter = [LON, LAT];`
(server-rendered, full precision) — no Nominatim call needed.
"""

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import psycopg
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

BASE = "https://planetabelarus.by"
GRODNO_LISTING = f"{BASE}/sights/filter/location-is-0000000275/apply/"
UA = "grodno-poc-collector/1.0 (research POC; contact: dev@example.com)"
HEADERS = {"User-Agent": UA}
CACHE = Path(os.environ.get("PARSER_CACHE", "./cache"))
DELAY_S = float(os.environ.get("PARSER_DELAY", "0.5"))
WORKERS = int(os.environ.get("PARSER_WORKERS", "4"))

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5433/grodno")

# `const initialCenter = [23.82318289, 53.67719794];` — full precision, [lon, lat].
INITIAL_CENTER_RE = re.compile(
    r"const\s+initialCenter\s*=\s*\[\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]"
)
# Detail-page slug in href like /sights/boriso-glebskaya-kolozhskaya-tserkov-v-grodno/
# Trailing slash excludes pagination links like /filter/.../?PAGEN_1=N.
SLUG_RE = re.compile(r'href="(/sights/[a-z0-9][a-z0-9-]+/)"')

# Errors worth retrying (DNS blips, connection drops, transient timeouts).
TRANSIENT_HTTP = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def http_get(session: requests.Session, url: str, attempts: int = 4) -> requests.Response:
    """session.get with exponential-backoff retry on transient network errors."""
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return session.get(url, timeout=20)
        except TRANSIENT_HTTP as e:
            if attempt == attempts:
                raise
            print(f"  retry {attempt}/{attempts} in {delay:.0f}s ({type(e).__name__})")
            time.sleep(delay)
            delay *= 2


def fetch_cached(url: str, session: requests.Session, rp: RobotFileParser) -> str:
    if not rp.can_fetch("*", url):
        raise RuntimeError(f"disallowed by robots.txt: {url}")
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    r = http_get(session, url)
    r.raise_for_status()
    p.write_text(r.text, encoding="utf-8")
    time.sleep(DELAY_S)  # delay only on real network hit; cache hits are instant
    return r.text


def crawl_grodno_urls(session: requests.Session, rp: RobotFileParser) -> list[str]:
    """Paginate the server-side Grodno filter and collect unique sight URLs."""
    seen: set[str] = set()
    page = 0
    while True:
        page += 1
        url = f"{GRODNO_LISTING}?PAGEN_1={page}"
        if not rp.can_fetch("*", url):
            raise RuntimeError(f"disallowed by robots.txt: {url}")
        r = http_get(session, url)
        r.raise_for_status()
        slugs = set(SLUG_RE.findall(r.text))
        if not slugs:
            break
        new = slugs - seen
        if page > 1 and not new:
            break  # pagination looped back to the same content
        seen.update(slugs)
        if page >= 50:  # safety cap; Grodno currently sits at 7
            print(f"  warning: stopped paginating at page {page} (safety cap)")
            break
        time.sleep(DELAY_S)
    print(f"  paginated {page - 1} pages, {len(seen)} unique slugs")
    return [urljoin(BASE, s) for s in sorted(seen)]


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

    return {
        "name": name,
        "description": desc,
        "lat": lat,
        "lon": lon,
        "photo_url": photo_url,
        "source_url": url,
    }


def process_one(url: str, session: requests.Session, rp: RobotFileParser):
    """Worker function. Returns ("ok", rec) | ("skip", reason) | ("fail", error)."""
    try:
        html = fetch_cached(url, session, rp)
        rec = parse_detail(url, html)
        return ("ok", rec) if rec else ("skip", "no_match")
    except RuntimeError as e:
        return ("skip", str(e))
    except Exception as e:
        return ("fail", repr(e))


INSERT_SQL = """
    INSERT INTO places (name, description, lat, lon, photo_url, source_url)
    VALUES (%(name)s, %(description)s, %(lat)s, %(lon)s, %(photo_url)s, %(source_url)s)
    ON CONFLICT (source_url) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        lat = EXCLUDED.lat,
        lon = EXCLUDED.lon,
        photo_url = EXCLUDED.photo_url
    RETURNING (xmax = 0) AS was_insert
"""


def main() -> None:
    rp = RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    rp.read()

    # shared session with connection pool — TCP/TLS handshake reused across threads
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=WORKERS + 2, pool_maxsize=WORKERS + 2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    print(f"crawling {GRODNO_LISTING} ...")
    urls = crawl_grodno_urls(session, rp)
    print(f"{len(urls)} Grodno sight URLs (workers={WORKERS}, delay={DELAY_S}s)")

    inserted = updated = skipped = failed = 0
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {pool.submit(process_one, url, session, rp): url for url in urls}
                for i, fut in enumerate(as_completed(futures), 1):
                    kind, payload = fut.result()
                    if kind == "ok":
                        cur.execute(INSERT_SQL, payload)
                        was_insert = cur.fetchone()[0]
                        if was_insert:
                            inserted += 1
                        else:
                            updated += 1
                    elif kind == "skip":
                        skipped += 1
                    else:
                        failed += 1
                        print(f"  fail: {payload}")
                    if i % 25 == 0 or i == len(urls):
                        print(f"[{i}/{len(urls)}] inserted={inserted} updated={updated} skipped={skipped} failed={failed}")
                        conn.commit()
        conn.commit()

    print(f"done. inserted={inserted} updated={updated} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
