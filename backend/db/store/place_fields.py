"""DB-column parsers for a place row's text columns.

`fun_facts`/`links` hold JSON or legacy pipe text; `parse_photo` reads the photo.
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

    A URL without its author and licence is not shown (a licence violation).
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
