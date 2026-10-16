"""Column lists, declared once."""

from __future__ import annotations

PLACE_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "description",
    "category",
    "blurb",
    "fun_fact",
    "fun_facts",
    "links",
    "opening_hours",
    "ticket_price",
    "is_free",
    "visit_minutes",
    "district",
    "town",
    "lat",
    "lon",
    "source_url",
    "category_source",
    "photo_url",
    "photo_author",
    "photo_license",
    "photo_source",
    "created_at",
)

PLACE_SELECT = ", ".join(PLACE_COLUMNS)

AREA_COLUMNS: tuple[str, ...] = (
    "id",
    "code",
    "name_ru",
    "name_en",
    "aliases",
    "kind",
    "source",
    "license",
    "note",
    "is_project_area",
    "created_at",
)

AREA_SELECT = ", ".join(AREA_COLUMNS)

CATEGORY_COLUMNS: tuple[str, ...] = (
    "code",
    "ru",
    "en",
    "role",
    "osm_tags",
    "visit_minutes",
    "aliases_ru",
    "aliases_en",
    "highlight",
)

CATEGORY_SELECT = ", ".join(CATEGORY_COLUMNS)

ITINERARY_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "blurb",
    "transport",
    "position",
)

ITINERARY_SELECT = ", ".join(ITINERARY_COLUMNS)

PLACE_WRITE_COLUMNS: tuple[str, ...] = (
    "name",
    "lat",
    "lon",
    "source_url",
    "category",
    "town",
    "district",
    "blurb",
    "fun_fact",
    "visit_minutes",
    "opening_hours",
    "ticket_price",
)

EMBEDDED_TEXT_COLUMNS: tuple[str, ...] = (
    "name",
    "category",
    "town",
)

USER_COLUMNS: tuple[str, ...] = (
    "id",
    "email",
    "display_name",
    "role",
    "created_at",
    "updated_at",
    "last_login_at",
)

USER_SELECT = ", ".join(USER_COLUMNS)

USER_SECRET_SELECT = f"{USER_SELECT}, password_hash"
