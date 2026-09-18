#!/usr/bin/env bash
# Downloads belarus-latest.osm.pbf from Geofabrik and cuts it to the Grodno Oblast bbox
# with osmium. The resulting small PBF is what gisops/valhalla builds tiles from on first start.
#
# Requires: wget, osmium-tool (apt: osmium-tool, brew: osmium-tool, or run osmium in a docker one-shot).
#
# Grodno Oblast bbox (OSM relation 59173, approximate):
#   south=52.85  west=23.50  north=54.10  east=26.55
#   osmium order:  WEST,SOUTH,EAST,NORTH

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p valhalla-data

PBF=valhalla-data/belarus-latest.osm.pbf
SMALL=valhalla-data/grodno.osm.pbf

if [ ! -f "$PBF" ]; then
  echo "[1/2] downloading belarus-latest.osm.pbf..."
  wget -q -O "$PBF" https://download.geofabrik.de/europe/belarus/belarus-latest.osm.pbf
fi

echo "[2/2] osmium extract bbox=23.50,52.85,26.55,54.10..."
osmium extract -b 23.50,52.85,26.55,54.10 -o "$SMALL" --overwrite "$PBF"
ls -lh "$SMALL"
