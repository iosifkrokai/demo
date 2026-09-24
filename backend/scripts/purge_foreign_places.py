"""Drop places that are not in Belarus (Vilnius / Poland strays from the bbox ingest).

    export DATABASE_URL=postgresql://grodno:...@localhost:5432/grodno
    python scripts/purge_foreign_places.py            # dry run, lists samples
    python scripts/purge_foreign_places.py --apply    # delete

The ingest itself now filters with agent.geofence, so this only cleans DBs that
were filled before that filter existed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.geofence import inside_project_area

DSN = os.environ.get("DATABASE_URL", "postgresql://grodno:grodno@localhost:5432/grodno")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete")
    args = ap.parse_args()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, category, district, lat, lon FROM places")
        rows = cur.fetchall()

        foreign = [r for r in rows if not inside_project_area(float(r[4]), float(r[5]))]
        print(f"places={len(rows)} inside={len(rows) - len(foreign)} foreign={len(foreign)}")
        for r in foreign[:15]:
            print(f"  x {r[1][:45]:45s} {r[2]:12s} {r[3] or '-':18s} {float(r[4]):.4f},{float(r[5]):.4f}")
        if len(foreign) > 15:
            print(f"  ... and {len(foreign) - 15} more")

        if not args.apply:
            print("\ndry run - pass --apply to delete")
            return 0
        if not foreign:
            print("nothing to delete")
            return 0

        ids = [r[0] for r in foreign]
        cur.execute("DELETE FROM places WHERE id = ANY(%s)", (ids,))
        deleted = cur.rowcount
        conn.commit()
        cur.execute("SELECT count(*) FROM places")
        print(f"deleted={deleted} remaining={cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
