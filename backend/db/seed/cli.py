"""The one seed command: ``python -m db.seed``.
Exit codes: 0 = ok, 1 = DB/network error, 2 = fatal validation failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from core.paths import GEO_DIR, PHOTOS_DIR, PLACES_DIR

from . import (
    datasets as ds_mod,
    itineraries as itineraries_mod,
    taxonomy as taxonomy_mod,
    version as version_mod,
)
from .datasets import (
    CoverageContext,
    Dataset,
    build_coverage_report,
    collect_records,
    read_curated,
)

DEFAULT_DSN = "postgresql://grodno:grodno@localhost:5432/grodno"


def _dsn(args: argparse.Namespace) -> str:
    return args.database_url or os.environ.get("DATABASE_URL", DEFAULT_DSN)


def _prune_stale(conn: Any, collected: dict[str, Any], *, skip: bool) -> int | str:
    """Delete the automatic rows no dataset carries any more, or say why nothing was."""
    if skip:
        return "skipped (--no-prune)"
    absent = [d["name"] for d in collected["datasets"] if d.get("missing")]
    if absent:
        return f"skipped (datasets absent: {', '.join(absent)})"
    from . import pipeline

    return pipeline.prune_stale(conn, [r["source_url"] for r in collected["records"]], apply=True)


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


def _write_registries(conn: Any, *, areas: bool) -> dict[str, Any]:
    """Write what is not a row of a dataset: the places' declared aliases and the taxonomies."""
    from . import pipeline

    written: dict[str, Any] = {"areas_written": 0}
    if areas:
        landed = pipeline.load_areas(conn, GEO_DIR)
        written["areas_written"] = landed["written"]
        written["areas_undeclared_removed"] = landed["removed"]
    written["aliases_written"] = pipeline.apply_place_aliases(conn)
    written["categories_written"] = pipeline.load_categories(conn, taxonomy_mod.all_categories())
    written["itineraries_written"] = pipeline.load_itineraries(
        conn, itineraries_mod.load_itineraries()
    )
    written["photos_written"] = pipeline.apply_photos(conn, PHOTOS_DIR)
    return written


def _collect(datasets: list[Dataset]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """The valid records to project, the ones another record shadows, and the
    declarations the data no longer holds."""
    collected = collect_records(datasets)
    kept, hand = ds_mod.resolve_shadowed(collected["records"], aliases=ds_mod.load_place_aliases())
    kept, twins = ds_mod.resolve_twins(kept)
    kept, declared, stale = ds_mod.resolve_declared_merges(kept, ds_mod.load_declared_merges())
    collected["records"] = kept
    return collected, [*hand, *twins, *declared], stale


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

    collected, shadowed, stale_merges = _collect(datasets)
    data_version = version_mod.content_version()

    merged_rows = 0
    db_stats: dict[str, Any] | None = None
    if not args.dry_run:
        try:
            with pipeline.connect(_dsn(args)) as conn:
                pipeline.allow_curated_category_change(conn)
                uncurated = pipeline.reset_curated(conn, collected["records"])
                totals = {"inserted": 0, "updated": 0}
                for ds in datasets:
                    rows = [r for r in collected["records"] if r["_dataset"] == ds.name]
                    out = pipeline.apply_dataset(conn, ds, rows)
                    totals["inserted"] += out["inserted"]
                    totals["updated"] += out["updated"]
                merged_rows = pipeline.apply_shadowed(
                    conn, shadowed, {d.name: d.license for d in datasets}
                )
                if curated:
                    curated_stats.update(pipeline.apply_curated_rows(conn, curated))
                    curated_stats["applied"] = True
                registries = _write_registries(conn, areas=not args.no_areas)
                pruned = _prune_stale(conn, collected, skip=args.no_prune)
                pipeline.record_data_version(conn, data_version)
                conn.commit()

                embedded = 0
                if not args.no_embed:
                    from db.seed.embeddings import embed_missing
                    from db.store.places import PostgresPlaceRepository

                    embedded = embed_missing(PostgresPlaceRepository(connect=lambda: conn))
                db_stats = pipeline.gather_db_stats(conn)
                db_stats.update(
                    {
                        "upserted": totals,
                        **registries,
                        "curated_reset": uncurated,
                        "embedded": embedded,
                        "embeddings_skipped": args.no_embed,
                        "merged_removed": merged_rows,
                        "stale_pruned": pruned,
                    }
                )
        except Exception as exc:
            sys.stderr.write(f"[seed] apply failed: {exc}\n")
            return 1

    report = build_coverage_report(
        collected,
        CoverageContext(
            mode="dry-run" if args.dry_run else "apply",
            curated=curated,
            curated_stats=curated_stats,
            db_stats=db_stats,
            data_version=data_version,
            merged=shadowed,
            stale_merges=stale_merges,
        ),
    )
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
            "fix backend/db/data/places/*.csv before publishing\n"
        )
        return 2
    return 0


def _print_summary(report: dict[str, Any]) -> None:
    t = report["totals"]
    dup = report["suspected_duplicates"]
    print(
        f"[seed] mode={report['mode']} records={t['records']} "
        f"geofence_rejects={t['geofence_rejects']} invalid={t['invalid']} "
        f"duplicates={dup['count']} namesakes={dup['namesakes']['count']} "
        f"merged={report['merged']['count']}"
    )
    for declaration in report["merged"].get("stale_declarations") or []:
        print(f"    declared merge not in the data: {declaration}")
    for ds in report["datasets"]:
        note = " (absent — run `seed fetch`)" if ds.get("missing") else ""
        print(
            f"    {ds['name']:>6}: {ds['valid']:>5} valid / {ds['rows']:>5} rows "
            f"(geofence {ds['geofence_rejects']}, invalid {ds['invalid']}, "
            f"category_source={ds['category_source']}){note}"
        )
    print("  coverage: " + ", ".join(f"{k}={v['share']}" for k, v in report["coverage"].items()))
    curated = report["curated"]
    if curated.get("applied"):
        print(
            f"  curated: {curated['matched']} applied of {curated['rows']}, "
            f"{curated['unmatched_count']} unmatched, {curated.get('ambiguous_count', 0)} ambiguous"
        )
        for name in curated.get("ambiguous") or []:
            print(f"    a curated name with no single row behind it: {name}")
    if "data_version" in report:
        print(f"  data_version: {report['data_version']}")
    if "db" in report:
        print(f"  db: {report['db']}")


def _drop_excluded_names(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Rows whose name is on the exclusion list (data, not a pattern — see the file)."""
    excluded = ds_mod.load_excluded_names()
    if not excluded:
        return rows, 0
    kept = [r for r in rows if not ds_mod.is_excluded_name(r.get("name"), excluded)]
    return kept, len(rows) - len(kept)


def _previous_districts(path: Path) -> dict[str, str]:
    """``source_url → district`` from the snapshot the fetch is about to replace.

    Read before writing, so a re-fetch keeps the districts already resolved instead of
    blanking the column (`overpass.fill_districts`).
    """
    if not path.is_file():
        return {}
    return {
        row["source_url"]: str(row.get("district") or "").strip()
        for row in ds_mod.read_pipe_csv(path)
        if str(row.get("district") or "").strip()
    }


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
        sources.append(
            ("osm", overpass.SIGHT_QUERY, osm_tags.sight_element_to_row, "places_osm_raw.csv")
        )
    if args.source in ("poi", "all"):
        sources.append(
            ("poi", overpass.SERVICE_QUERY, osm_tags.service_element_to_row, "places_poi.csv")
        )

    town_names = ds_mod.load_town_names()
    for name, template, to_row, filename in sources:
        query = overpass.build_overpass_query(template, bbox)
        elements = overpass.fetch_overpass(
            query, limit=args.limit, dry_run=mock is not None, mock=mock
        )
        if elements is None:
            sys.stderr.write(f"[seed] fetch {name}: Overpass returned nothing\n")
            return 1
        rows = [row for el in elements if (row := to_row(el))]
        out_path = out_dir / filename
        rows, excluded = _drop_excluded_names(rows)
        districts = overpass.fill_districts(rows, _previous_districts(out_path))
        towns = overpass.fill_town_names(rows, town_names.get)
        note = f", {excluded} dropped (excluded names)" if excluded else ""
        tail = " (dry-run, not written)" if args.dry_run else ""
        print(
            f"[seed] fetch {name}: {len(rows)} rows{note} — district: "
            f"{districts['carried']} kept from the previous snapshot, "
            f"{districts['derived']} from the nearest raion; town: "
            f"{towns['translated']} translated, {towns['kept']} left as the source spelled "
            f"them{tail}"
        )
        if args.dry_run:
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


def cmd_admin(args: argparse.Namespace) -> int:
    from .admin import create_admin

    return create_admin(email=args.email, password=args.password, name=args.name)


def _add_apply_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dry-run", action="store_true", help="Validate + report only. No DB, no network."
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=PLACES_DIR,
        help="Directory holding the CSV datasets (default: backend/db/data/places).",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the machine-readable JSON report to this path.",
    )
    p.add_argument("--json", action="store_true", help="Print the JSON report to stdout.")
    p.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embeddings (local model; still works without it).",
    )
    p.add_argument("--no-areas", action="store_true", help="Do not (re)load areas.")
    p.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep rows the datasets no longer carry (they are dropped by default).",
    )
    p.add_argument("--database-url", default=None, help="Override DATABASE_URL.")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m db.seed",
        description="One reproducible seed: validate → upsert → embed → report.",
    )
    _add_apply_flags(p)
    sub = p.add_subparsers(dest="command")

    f = sub.add_parser("fetch", help="Acquire OSM sights/services from Overpass into CSVs.")
    f.add_argument("--source", choices=["osm", "poi", "all"], default="all")
    f.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("W", "S", "E", "N"),
        default=None,
        help="Bounding box in W S E N order (default: the Grodno voblast).",
    )
    f.add_argument("--limit", type=int, default=None, help="Max elements (for testing).")
    f.add_argument(
        "--input-json",
        type=Path,
        default=None,
        help="Reuse a saved Overpass response instead of the network.",
    )
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

    ad = sub.add_parser("admin", help="Create or promote the first administrator.")
    ad.add_argument("--email", required=True)
    ad.add_argument(
        "--password", default=None, help="omitted: read from GRODNO_ADMIN_PASSWORD or prompt"
    )
    ad.add_argument("--name", default=None, help="display name")
    ad.set_defaults(func=cmd_admin)

    return p


def cmd_all(args: argparse.Namespace) -> int:
    """Re-acquire the OSM sources, then apply them."""
    fetch_ns = argparse.Namespace(
        source=args.source,
        bbox=args.bbox,
        limit=None,
        input_json=args.input_json,
        out_dir=args.out_dir,
        dry_run=args.dry_run,
    )
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
