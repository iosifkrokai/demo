"""DB-column parsers for a place row's text columns.

`fun_facts` and `links` are text columns holding JSON (or a legacy pipe format);
`parse_photo` reads the four photo columns.  Kept in the store layer so a browse
card (`store/places.py`) and a planned route (`planner/retrieve.py`) share the
same reading of a row.
"""

from __future__ import annotations


def parse_fun_facts(raw: str | None) -> list[str]:
    """Accept both '["a","b"]' JSON (region dataset) and 'a|b' pipe format (curated)."""
    raw = (raw or "").strip()
    if not raw:
        return []
    import json as _json
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in _json.loads(raw) if str(x).strip()][:3]
        except _json.JSONDecodeError:
            pass
    return [f.strip() for f in raw.split("|") if f.strip()][:3]


def parse_links(raw: str | None) -> list[dict]:
    """Accept both '[{"title":...}]' JSON and 'title | url' pipe items."""
    raw = (raw or "").strip()
    if not raw:
        return []
    import json as _json
    if raw.startswith("["):
        try:
            return [x for x in _json.loads(raw) if isinstance(x, dict)][:4]
        except _json.JSONDecodeError:
            pass
    links: list[dict] = []
    for item in raw.split("|"):
        item = item.strip()
        if not item:
            continue
        if " | " in item:
            title, url = (s.strip() for s in item.split(" | ", 1))
            links.append({"title": title, "url": url})
        else:
            try:
                links.append(_json.loads(item))
            except Exception:
                pass
    return links[:4]


def parse_photo(row: dict) -> dict | None:
    """The point's picture with its credit — or nothing at all.

    All four fields are written by one resolution pass (`python -m seed photos --apply`).
    A URL without its author and licence is deliberately not shown: Wikimedia
    files are licensed, and a credit-less image is a licence violation rather
    than a nice-to-have.
    """
    url = (row.get("photo_url") or "").strip()
    if not url:
        return None
    author = (row.get("photo_author") or "").strip()
    license_name = (row.get("photo_license") or "").strip()
    if not author or not license_name:
        return None
    return {
        "url": url,
        "author": author,
        "license": license_name,
        "source": (row.get("photo_source") or "").strip() or None,
    }
