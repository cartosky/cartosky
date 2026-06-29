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

for cmd in curl ogr2ogr tippecanoe tile-join sqlite3 python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

mkdir -p "$SOURCE_DIR" "$BUILD_DIR" "$TMP_DIR" "$OUT_DIR" "$LEGACY_OUT_DIR"
rm -f "$TMP_DIR"/*.mbtiles
rm -f "$OUT_MBTILES" "$LEGACY_V3_OUT_MBTILES"

PBF_URL="https://download.geofabrik.de/north-america-latest.osm.pbf"
PBF_PATH="$SOURCE_DIR/north-america-latest.osm.pbf"

curl -L "$PBF_URL" -o "$PBF_PATH"

extract_roads() {
  local output_path="$1"
  local where_clause="$2"

  ogr2ogr -f GeoJSON "$output_path" "$PBF_PATH" lines \
    -t_srs EPSG:4326 \
    -skipfailures \
    -where "$where_clause"
}

extract_roads "$BUILD_DIR/roads_major_raw.geojson" "highway IN ('motorway','motorway_link','trunk','trunk_link')"
extract_roads "$BUILD_DIR/roads_primary_secondary_raw.geojson" "highway IN ('primary','primary_link','secondary','secondary_link')"
extract_roads "$BUILD_DIR/roads_local_raw.geojson" "highway IN ('tertiary','tertiary_link','residential','unclassified')"

python3 - "$BUILD_DIR/roads_major_raw.geojson" "$BUILD_DIR/roads_major.geojson" major <<'PY'
import json
import sys

source_path, output_path, road_class = sys.argv[1], sys.argv[2], sys.argv[3]

with open(source_path, 'r', encoding='utf-8') as src_file:
  feature_collection = json.load(src_file)

features = []
for feature in feature_collection.get('features', []):
  if not isinstance(feature, dict):
    continue
  geometry = feature.get('geometry')
  if not isinstance(geometry, dict) or not geometry.get('type'):
    continue
  properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
  properties = {
    'road_class': road_class,
    'highway': str(properties.get('highway', '')).strip(),
  }
  feature['properties'] = properties
  features.append(feature)

feature_collection['features'] = features

with open(output_path, 'w', encoding='utf-8') as out_file:
  json.dump(feature_collection, out_file, separators=(',', ':'))
PY

python3 - "$BUILD_DIR/roads_primary_secondary_raw.geojson" "$BUILD_DIR/roads_primary_secondary.geojson" primary_secondary <<'PY'
import json
import sys

source_path, output_path, road_class = sys.argv[1], sys.argv[2], sys.argv[3]

with open(source_path, 'r', encoding='utf-8') as src_file:
  feature_collection = json.load(src_file)

features = []
for feature in feature_collection.get('features', []):
  if not isinstance(feature, dict):
    continue
  geometry = feature.get('geometry')
  if not isinstance(geometry, dict) or not geometry.get('type'):
    continue
  properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
  properties = {
    'road_class': road_class,
    'highway': str(properties.get('highway', '')).strip(),
  }
  feature['properties'] = properties
  features.append(feature)

feature_collection['features'] = features

with open(output_path, 'w', encoding='utf-8') as out_file:
  json.dump(feature_collection, out_file, separators=(',', ':'))
PY

python3 - "$BUILD_DIR/roads_local_raw.geojson" "$BUILD_DIR/roads_local.geojson" local <<'PY'
import json
import sys

source_path, output_path, road_class = sys.argv[1], sys.argv[2], sys.argv[3]

with open(source_path, 'r', encoding='utf-8') as src_file:
  feature_collection = json.load(src_file)

features = []
for feature in feature_collection.get('features', []):
  if not isinstance(feature, dict):
    continue
  geometry = feature.get('geometry')
  if not isinstance(geometry, dict) or not geometry.get('type'):
    continue
  properties = feature.get('properties') if isinstance(feature.get('properties'), dict) else {}
  properties = {
    'road_class': road_class,
    'highway': str(properties.get('highway', '')).strip(),
  }
  feature['properties'] = properties
  features.append(feature)

feature_collection['features'] = features

with open(output_path, 'w', encoding='utf-8') as out_file:
  json.dump(feature_collection, out_file, separators=(',', ':'))
PY

tippecanoe -f -o "$TMP_DIR/roads_major.mbtiles" -l roads -Z5 -z14 --buffer=6 --drop-smallest-as-needed --coalesce-smallest-as-needed --coalesce-densest-as-needed --simplification=4 "$BUILD_DIR/roads_major.geojson"
tippecanoe -f -o "$TMP_DIR/roads_primary_secondary.mbtiles" -l roads -Z8 -z14 --buffer=6 --drop-smallest-as-needed --coalesce-smallest-as-needed --coalesce-densest-as-needed --simplification=5 "$BUILD_DIR/roads_primary_secondary.geojson"
tippecanoe -f -o "$TMP_DIR/roads_local.mbtiles" -l roads -Z10 -z14 --buffer=6 --drop-smallest-as-needed --coalesce-smallest-as-needed --coalesce-densest-as-needed --simplification=6 "$BUILD_DIR/roads_local.geojson"

tile-join -f -o "$OUT_MBTILES" \
  "$TMP_DIR/roads_major.mbtiles" \
  "$TMP_DIR/roads_primary_secondary.mbtiles" \
  "$TMP_DIR/roads_local.mbtiles"

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