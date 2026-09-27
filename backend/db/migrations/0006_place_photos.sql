-- 0006_place_photos.sql: the illustration of a point, with its attribution.
--
-- `places.photo_url` already existed but was never filled and had nowhere to
-- keep the credit. A Wikimedia image without its author and licence is not
-- usable in a product, so the three columns travel together with the URL:
-- every write sets all four or none of them.
--
--   * photo_url     — direct image URL (upload.wikimedia.org …), not a page.
--   * photo_author  — as the source states it ("Александр Липилин"), may be a
--                     nick or a link label; kept verbatim, never reformatted.
--   * photo_license — short licence name ("CC BY-SA 3.0"), verbatim.
--   * photo_source  — the file page the three above were read from, so anyone
--                     can re-check the credit.
--
-- Idempotent and non-destructive: ADD COLUMN IF NOT EXISTS, so it can be
-- applied to a live volume without touching existing rows. Mirrors db/init.sql
-- for fresh installs. Run after init.sql on a fresh volume.
--
-- Applying this file does NOT fill anything. The photos themselves come from
-- scripts/seed_photos.py (reproducible: osm hints -> Wikimedia -> json -> DB).

ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_author  TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_license TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS photo_source  TEXT;
