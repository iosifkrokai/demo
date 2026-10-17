"""What `ticket_price` says about admission: the one place the prose is read.
The column holds OSM's `fee`/`charge` tags (`yes`/`no`) on automatic rows and a human
sentence on hand-authored ones; every string not positively understood answers ``None``.
"""

from __future__ import annotations

import re

FREE_PHRASES = (
    "вход свободный",
    "вход свободный (пожертвования)",
    "бесплатно",
    "free",
)

PAID_PHRASES = ("тариф", "билет", "стоимость", "платн")

FREE_TAGS = frozenset({"no", "none", "0"})
PAID_TAGS = frozenset({"yes"})

_PRICE = re.compile(r"\d")
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


def free_entry(ticket_price: str | None) -> bool | None:
    """Whether admission is free, or ``None`` when the text does not say."""
    text = " ".join((ticket_price or "").split()).lower()
    if not text or _PUNCTUATION_ONLY.match(text):
        return None
    if text in FREE_TAGS or text in FREE_PHRASES:
        return True
    if text in PAID_TAGS or any(phrase in text for phrase in PAID_PHRASES):
        return False
    return False if _PRICE.search(text) else None
