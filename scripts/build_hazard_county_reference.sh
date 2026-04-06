#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${ROOT_DIR}/data/hazards/work"
SOURCE_DIR="${WORK_DIR}/source"
BUILD_DIR="${WORK_DIR}/build"
OUT_DIR="${ROOT_DIR}/data/hazards"
OUT_FILE="${OUT_DIR}/county_reference.geojson"

for cmd in curl unzip ogr2ogr mapshaper; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$SOURCE_DIR" "$BUILD_DIR" "$OUT_DIR"

COUNTIES_ZIP_URL="https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_5m.zip"

curl -L "$COUNTIES_ZIP_URL" -o "$SOURCE_DIR/counties.zip"
unzip -o "$SOURCE_DIR/counties.zip" -d "$SOURCE_DIR/counties_shp" >/dev/null

ogr2ogr -f GeoJSON \
  "$BUILD_DIR/counties_polygons.geojson" \
  "$SOURCE_DIR/counties_shp/cb_2023_us_county_5m.shp" \
  -t_srs EPSG:4326 \
  -select GEOID,NAME,STATEFP,COUNTYFP

mapshaper "$BUILD_DIR/counties_polygons.geojson" \
  -snap interval=0.00003 \
  -clean \
  -simplify weighted 18% keep-shapes \
  -filter-fields GEOID,NAME,STATEFP,COUNTYFP \
  -o format=geojson "$OUT_FILE"

echo "Built hazard county reference: $OUT_FILE"