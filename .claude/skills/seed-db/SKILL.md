---
name: seed-db
description: Load or refresh the place data in Postgres. Use when the database is empty, when a dataset file changed, when the schema was just migrated, or when someone reports "no places / no search results".
---

# Seed the database

The database is a projection of the committed files under `backend/data/`, built
by one command. `apply` never touches the network; only `fetch` does.

```bash
make migrate   # first, if the schema may be behind — Alembic owns the schema
make seed      # = python -m db.seed — validate, upsert, embed, report
```

`python -m db.seed` is idempotent: a second run inserts nothing and never overwrites
a curated category. Run it from `backend/`, or use the Makefile targets, which
run it in the container.

## Before you touch anything

```bash
python -m db.seed --dry-run     # validate + report from the CSVs; no DB, no network
```

Use it to see what a change would do. `--report FILE` writes the same thing as
JSON, `--no-embed` skips the embedding pass.

## The subcommands

| Command | What it does |
|---|---|
| `python -m db.seed` | validate → upsert → embed → report (the normal path) |
| `python -m db.seed fetch --source osm\|poi\|all` | re-acquire OSM rows over Overpass into `data/places/*.csv` — the only networked step |
| `python -m db.seed photos --apply` | resolve photo columns and write them into `places` |
| `python -m db.seed prune` | list rows outside the project area (add `--apply` to delete) |
| `python -m db.seed admin --email …` | create or promote the first administrator |

`make seed` / `make fetch` / `make photos` / `make prune` / `make admin` wrap these
and run them in the compose `seed` service.

## When it fails

- **`relation "places" does not exist`** — the schema is missing or behind:
  `make migrate` first. Do not create tables by hand.
- **"no photos on any place"** — the committed `data/photos/place_photos.json` was
  not applied; a plain `make seed` applies it. `make photos` re-resolves it from
  Wikimedia, which needs the network.
- **rows missing after a fetch that looked fine** — the ingest quarantines rows
  outside the Grodno project area and counts them; the coverage report says how
  many, it does not silently keep them.

Two more things worth knowing: the counts now live in the report printed at the
end of an apply (`db: {…}`), and the curated-category guard is a DB trigger — see
`add-dataset` before writing any row yourself.
