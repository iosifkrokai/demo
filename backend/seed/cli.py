"""The one seed command: ``python -m seed``.
Exit codes: 0 = ok, 1 = DB/network error, 2 = fatal validation failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.paths import GEO_DIR, PHOTOS_DIR, PLACES_DIR

from . import datasets as ds_mod
from .datasets import Dataset, build_coverage_report, collect_records, read_curated

DEFAULT_DSN = "postgresql://grodno:grodno@localhost:5432/grodno"


def _dsn(args: argparse.Namespace) -> str:
    return args.database_url or os.environ.get("DATABASE_URL", DEFAULT_DSN)


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def cmd_apply(args: argparse.Namespace) -> int:
    from . import pipeline

    data_dir: Path = args.data_dir
    datasets: list[Dataset] = [d.with_data_dir(data_dir) for d in ds_mod.default_datasets()]
    curated = read_curated(data_dir / "places_curated.csv")
    curated_stats = {
        "rows": len(curated),
        "applied": False,
        "by_category": _count_by(curated, "category") if curated else {},
    }

    collected = collect_records(datasets)

    db_stats: dict | None = None
    if not args.dry_run:
        try:
            with pipeline.connect(_dsn(args)) as conn:
                pipeline.allow_curated_category_change(conn)
                totals = {"inserted": 0, "updated": 0}
                for ds in datasets:
                    rows = [r for r in collected["records"] if r["_dataset"] == ds.name]
                    out = pipeline.apply_dataset(conn, ds, rows)
                    totals["inserted"] += out["inserted"]
                    totals["updated"] += out["updated"]
                if curated:
                    curated_stats.update(pipeline.apply_curated_rows(conn, curated))
                    curated_stats["applied"] = True
                areas_written = 0
                if not args.no_areas:
                    areas_written = pipeline.load_areas(conn, collected["records"], GEO_DIR)
                photos_written = pipeline.apply_photos(conn, PHOTOS_DIR)
                conn.commit()

                embedded = 0
                if not args.no_embed:
                    from infra import embeddings

                    embedded = embeddings.embed_missing(conn)
                db_stats = pipeline.gather_db_stats(conn)
                db_stats.update({"upserted": totals, "areas_written": areas_written,
                                 "photos_written": photos_written,
                                 "embedded": embedded, "embeddings_skipped": args.no_embed})
        except Exception as exc:
            sys.stderr.write(f"[seed] apply failed: {exc}\n")
            return 1

    report = build_coverage_report(
        collected, mode="dry-run" if args.dry_run else "apply",
        curated=curated, curated_stats=curated_stats, db_stats=db_stats)
    _print_summary(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[seed] report written to {args.report}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if collected["fatal"]:
        sys.stderr.write(
            "[seed] fatal: a hand-authored dataset (city/region) has invalid rows — "
            "fix data/places/*.csv before publishing\n")
        return 2
    return 0


def _print_summary(report: dict) -> None:
    t = report["totals"]
    print(f"[seed] mode={report['mode']} records={t['records']} "
          f"geofence_rejects={t['geofence_rejects']} invalid={t['invalid']} "
          f"duplicates={report['suspected_duplicates']['count']}")
    for ds in report["datasets"]:
        note = " (absent — run `seed fetch`)" if ds.get("missing") else ""
        print(f"    {ds['name']:>6}: {ds['valid']:>5} valid / {ds['rows']:>5} rows "
              f"(geofence {ds['geofence_rejects']}, invalid {ds['invalid']}, "
              f"category_source={ds['category_source']}){note}")
    print("  coverage: " + ", ".join(f"{k}={v['share']}" for k, v in report["coverage"].items()))
    if "db" in report:
        print(f"  db: {report['db']}")


def cmd_fetch(args: argparse.Namespace) -> int:
    from . import osm_tags, overpass
    from .datasets import COLUMNS

    bbox = tuple(args.bbox) if args.bbox else overpass.DEFAULT_BBOX
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    mock = None
    if args.input_json:
        mock = json.loads(Path(args.input_json).read_text(encoding="utf-8"))

    sources = []
    if args.source in ("osm", "all"):
        sources.append(("osm", overpass.SIGHT_QUERY, osm_tags.sight_element_to_row,
                        "places_osm_raw.csv"))
    if args.source in ("poi", "all"):
        sources.append(("poi", overpass.SERVICE_QUERY, osm_tags.service_element_to_row,
                        "places_poi.csv"))

    for name, template, to_row, filename in sources:
        query = overpass.build_overpass_query(template, bbox)
        elements = overpass.fetch_overpass(
            query, limit=args.limit, dry_run=mock is not None, mock=mock)
        if elements is None:
            sys.stderr.write(f"[seed] fetch {name}: Overpass returned nothing\n")
            return 1
        rows = [row for el in elements if (row := to_row(el))]
        out_path = out_dir / filename
        if args.dry_run:
            print(f"[seed] fetch {name}: {len(rows)} rows (dry-run, not written)")
            continue
        overpass.write_pipe_csv(rows, out_path, COLUMNS)
        print(f"[seed] fetch {name}: wrote {len(rows)} rows to {out_path}")
    return 0


def cmd_photos(args: argparse.Namespace) -> int:
    from . import photos

    data_dir: Path = args.data_dir
    done = photos.run_stage(
        stage=args.stage,
        data_dir=data_dir,
        pbf_path=args.pbf,
        limit=args.limit,
        apply=args.apply,
        dsn=_dsn(args) if args.apply else None,
    )
    print(f"[seed] photos ({args.stage}): {done}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    from . import pipeline

    with pipeline.connect(_dsn(args)) as conn:
        found = pipeline.prune_foreign(conn, apply=args.apply)
        conn.commit()
        if not args.apply:
            print(f"[seed] prune: {found} foreign rows (dry-run — pass --apply to delete)")
        else:
            print(f"[seed] prune: deleted {found} foreign rows")
    return 0


def cmd_admin(args: argparse.Namespace) -> int:
    from .admin import create_admin

    return create_admin(email=args.email, password=args.password, name=args.name)


def _add_apply_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + report only. No DB, no network.")
    p.add_argument("--data-dir", type=Path, default=PLACES_DIR,
                   help="Directory holding the CSV datasets (default: backend/data/places).")
    p.add_argument("--report", type=Path, default=None,
                   help="Write the machine-readable JSON report to this path.")
    p.add_argument("--json", action="store_true", help="Print the JSON report to stdout.")
    p.add_argument("--no-embed", action="store_true",
                   help="Skip embeddings (local model; still works without it).")
    p.add_argument("--no-areas", action="store_true",
                   help="Do not (re)load areas.")
    p.add_argument("--database-url", default=None, help="Override DATABASE_URL.")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m seed",
        description="One reproducible seed: validate → upsert → embed → report.")
    _add_apply_flags(p)
    sub = p.add_subparsers(dest="command")

    f = sub.add_parser("fetch", help="Acquire OSM sights/services from Overpass into CSVs.")
    f.add_argument("--source", choices=["osm", "poi", "all"], default="all")
    f.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), default=None,
                   help="Bounding box in W S E N order (default: the Grodno voblast).")
    f.add_argument("--limit", type=int, default=None, help="Max elements (for testing).")
    f.add_argument("--input-json", type=Path, default=None,
                   help="Reuse a saved Overpass response instead of the network.")
    f.add_argument("--out-dir", type=Path, default=PLACES_DIR, help="Where to write the CSVs.")
    f.add_argument("--dry-run", action="store_true", help="Fetch but write nothing.")
    f.set_defaults(func=cmd_fetch)

    ph = sub.add_parser("photos", help="Acquire/enrich photo columns.")
    ph.add_argument("--stage", choices=["hints", "resolve", "all"], default="all")
    ph.add_argument("--pbf", type=Path, default=None, help="OSM PBF for the hints stage.")
    ph.add_argument("--limit", type=int, default=None)
    ph.add_argument("--apply", action="store_true", help="Write the photos into the DB.")
    ph.add_argument("--data-dir", type=Path, default=PHOTOS_DIR)
    ph.add_argument("--database-url", default=None)
    ph.set_defaults(func=cmd_photos)

    a = sub.add_parser("all", help="fetch, then apply.")
    _add_apply_flags(a)
    a.add_argument("--source", choices=["osm", "poi", "all"], default="all")
    a.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), default=None)
    a.add_argument("--input-json", type=Path, default=None)
    a.add_argument("--out-dir", type=Path, default=PLACES_DIR)
    a.set_defaults(func=None)

    pr = sub.add_parser("prune", help="Drop places outside the project area.")
    pr.add_argument("--apply", action="store_true", help="Actually delete.")
    pr.add_argument("--database-url", default=None)
    pr.set_defaults(func=cmd_prune)

    ad = sub.add_parser("admin", help="Create or promote the first administrator.")
    ad.add_argument("--email", required=True)
    ad.add_argument("--password", default=None,
                    help="omitted: read from GRODNO_ADMIN_PASSWORD or prompt")
    ad.add_argument("--name", default=None, help="display name")
    ad.set_defaults(func=cmd_admin)

    return p


def cmd_all(args: argparse.Namespace) -> int:
    """Re-acquire the OSM sources, then apply them."""
    fetch_ns = argparse.Namespace(
        source=args.source, bbox=args.bbox, limit=None, input_json=args.input_json,
        out_dir=args.out_dir, dry_run=args.dry_run)
    rc = cmd_fetch(fetch_ns)
    if rc != 0:
        return rc
    return cmd_apply(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        return cmd_apply(args)
    if args.command == "all":
        return cmd_all(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
