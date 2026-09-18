#!/usr/bin/env bash
# Downloads belarus-latest.osm.pbf from Geofabrik (follows 302 to a dated filename),
# verifies MD5, and cuts it to the Grodno Oblast bbox with osmium.
#
# Requires: wget, osmium-tool.
#
# Grodno Oblast bbox (OSM relation 59173, approximate):
#   osmium order: WEST,SOUTH,EAST,NORTH  ->  23.50,52.85,26.55,54.10
# If you want it tighter, get the polygon via Overpass first.

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p valhalla-data

UA="grodno-poc-collector/1.0 (research POC; contact: dev@example.com)"
PBF=valhalla-data/belarus-latest.osm.pbf
MD5=valhalla-data/belarus-latest.osm.pbf.md5
SMALL=valhalla-data/grodno.osm.pbf

# Geofabrik's "latest" URL is a 302 to a date-stamped file. wget follows it by default
# when --max-redirect is > 0 (default 20). Using --continue so a flaky connection
# resumes instead of restarting from zero.
PBF_URL="https://download.geofabrik.de/europe/belarus-latest.osm.pbf"
MD5_URL="${PBF_URL}.md5"

download_with_verify() {
  if [ ! -s "$PBF" ] || [ "$(stat -c%s "$PBF" 2>/dev/null || echo 0)" -lt 100000000 ]; then
    echo "[1/3] downloading belarus-latest.osm.pbf (follows 302 to dated file)..."
    wget --tries=3 --timeout=60 --continue --user-agent="$UA" -O "$PBF" "$PBF_URL"
  else
    echo "[1/3] $PBF already present ($(du -h "$PBF" | cut -f1)), skipping download"
  fi

  echo "[2/3] verifying MD5..."
  wget --tries=3 --timeout=30 --user-agent="$UA" -O "$MD5" "$MD5_URL"
  expected=$(awk '{print $1}' "$MD5" | tr -d '\r\n')
  actual=$(md5sum "$PBF" | awk '{print $1}')
  if [ "$expected" != "$actual" ]; then
    echo "MD5 mismatch: expected $expected, got $actual — deleting and retrying..."
    rm -f "$PBF"
    wget --tries=3 --timeout=60 --user-agent="$UA" -O "$PBF" "$PBF_URL"
    actual=$(md5sum "$PBF" | awk '{print $1}')
    if [ "$expected" != "$actual" ]; then
      echo "FATAL: MD5 still mismatches after retry ($actual vs $expected)" >&2
      exit 1
    fi
  fi
  echo "MD5 ok: $actual"
}

extract() {
  echo "[3/3] osmium extract bbox=23.50,52.85,26.55,54.10..."
  osmium extract -b 23.50,52.85,26.55,54.10 -o "$SMALL" --overwrite "$PBF"
  ls -lh "$SMALL"
}

download_with_verify
extract
