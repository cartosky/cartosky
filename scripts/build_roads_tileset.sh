#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${ROOT_DIR}/data/v3/roads/work"
SOURCE_DIR="${WORK_DIR}/source"
BUILD_DIR="${WORK_DIR}/build"
TMP_DIR="${WORK_DIR}/tmp"
OUT_DIR="${ROOT_DIR}/data/roads/v1"
LEGACY_OUT_DIR="${ROOT_DIR}/data/v3/roads/v1"
OUT_MBTILES="${OUT_DIR}/cartosky_roads.mbtiles"
LEGACY_V3_OUT_MBTILES="${LEGACY_OUT_DIR}/cartosky_roads.mbtiles"

for cmd in curl ogr2ogr tippecanoe tile-join sqlite3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$SOURCE_DIR" "$BUILD_DIR" "$TMP_DIR" "$OUT_DIR" "$LEGACY_OUT_DIR"
rm -f "$TMP_DIR"/*.mbtiles
rm -f "$OUT_MBTILES" "$LEGACY_V3_OUT_MBTILES"
rm -f "$SOURCE_DIR"/* "$BUILD_DIR"/*

SOURCE_PBFS=(
  "us|https://download.geofabrik.de/north-america/us-latest.osm.pbf"
  "canada|https://download.geofabrik.de/north-america/canada-latest.osm.pbf"
  "mexico|https://download.geofabrik.de/north-america/mexico-latest.osm.pbf"
)

ROAD_CLASS_SPECS=(
  "major|highway IN ('motorway','motorway_link','trunk','trunk_link')|5|14|4"
  "primary_secondary|highway IN ('primary','primary_link','secondary','secondary_link')|8|14|5"
  "local|highway IN ('tertiary','tertiary_link','residential','unclassified')|10|14|6"
)

declare -a TILESET_PARTS=()

extract_and_tile_class() {
  local region="$1"
  local pbf_path="$2"
  local road_class="$3"
  local where_clause="$4"
  local minzoom="$5"
  local maxzoom="$6"
  local simplification="$7"

  local geojsonseq_path="$BUILD_DIR/${region}_${road_class}.geojsonseq"
  local mbtiles_path="$TMP_DIR/${region}_${road_class}.mbtiles"

  ogr2ogr -f GeoJSONSeq "$geojsonseq_path" "$pbf_path" lines \
    -t_srs EPSG:4326 \
    -skipfailures \
    -dialect SQLite \
    -sql "SELECT highway, '${road_class}' AS road_class FROM lines WHERE ${where_clause}"

  if [[ ! -s "$geojsonseq_path" ]]; then
    rm -f "$geojsonseq_path"
    return
  fi

  tippecanoe -f -P -o "$mbtiles_path" -l roads -Z"$minzoom" -z"$maxzoom" \
    --buffer=6 \
    --drop-smallest-as-needed \
    --coalesce-smallest-as-needed \
    --coalesce-densest-as-needed \
    --simplification="$simplification" \
    "$geojsonseq_path"

  TILESET_PARTS+=("$mbtiles_path")
  rm -f "$geojsonseq_path"
}

for source_spec in "${SOURCE_PBFS[@]}"; do
  IFS='|' read -r region pbf_url <<< "$source_spec"
  pbf_path="$SOURCE_DIR/${region}.osm.pbf"

  curl -L "$pbf_url" -o "$pbf_path"

  for class_spec in "${ROAD_CLASS_SPECS[@]}"; do
    IFS='|' read -r road_class where_clause minzoom maxzoom simplification <<< "$class_spec"
    extract_and_tile_class "$region" "$pbf_path" "$road_class" "$where_clause" "$minzoom" "$maxzoom" "$simplification"
  done

  rm -f "$pbf_path"
done

if [[ ${#TILESET_PARTS[@]} -eq 0 ]]; then
  echo "No road features extracted; refusing to emit an empty roads tileset." >&2
  exit 1
fi

tile-join -f -o "$OUT_MBTILES" "${TILESET_PARTS[@]}"

VECTOR_LAYERS='[{"id":"roads","description":"OSM-derived road linework without labels","fields":{"road_class":"String","highway":"String"},"minzoom":5,"maxzoom":14}]'
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('name','CartoSky Roads v1');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('id','cartosky-roads-v1');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('description','North America OSM-derived road overlay for CartoSky weather maps');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('attribution','OpenStreetMap contributors; Geofabrik');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('minzoom','5');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('maxzoom','14');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('bounds','-178,5,-25,82');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('center','-101.5,45,4');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('format','pbf');"
sqlite3 "$OUT_MBTILES" "INSERT OR REPLACE INTO metadata(name,value) VALUES('vector_layers','$VECTOR_LAYERS');"

ln -sf "../../roads/v1/$(basename "$OUT_MBTILES")" "$LEGACY_V3_OUT_MBTILES"

echo "Built roads tileset: $OUT_MBTILES"