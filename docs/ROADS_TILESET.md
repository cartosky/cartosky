# Roads Tileset

This repo supports a self-hosted, OSM-derived vector roads tileset for the viewer map overlay.

## Runtime endpoints

- `GET /tiles/v3/roads/v1/tilejson.json`
- `GET /tiles/v3/roads/v1/{z}/{x}/{y}.mvt`

## Frontend usage

`frontend/src/components/map-canvas.tsx` consumes:

- `https://api.cartosky.com/tiles/v3/roads/v1/tilejson.json`

The tileset exposes one source layer:

- `roads` with `road_class` in `major|primary_secondary|local`

## Build

Build with:

```bash
./scripts/build_roads_tileset.sh
```

Output MBTiles path:

- `data/roads/v1/cartosky_roads.mbtiles`

## Build strategy

- Downloads `us`, `canada`, and `mexico` OSM PBF extracts sequentially from Geofabrik instead of a single continent-scale monolith.
- Extracts one road class at a time directly from each PBF with `ogr2ogr`.
- Emits line-delimited GeoJSON features (`GeoJSONSeq`) that `tippecanoe` can tile without loading the full dataset into Python memory.
- Deletes each intermediate extract after it is tiled to keep peak disk usage down.

## Zoom strategy

- Major highways (`motorway`, `trunk`, links): `z5-z14`
- Primary/secondary roads (and links): `z8-z14`
- Local roads (`tertiary`, `residential`, `unclassified`): `z10-z14`

## Deployment

Set road env vars in:

- `deployment/systemd/api.env.example`

Then restart the main API unit after copying the MBTiles artifact into place:

```bash
sudo systemctl restart csky-api
```

## Notes

- Road labels are intentionally excluded.
- Roads are rendered above county borders but below city labels in the viewer.
- Road opacity is tied to the weather overlay opacity, with higher-contrast defaults for dense filled palettes.
- This script is intentionally optimized for lower peak memory use on modest servers; it trades some extra sequential processing time for much lower RAM pressure.