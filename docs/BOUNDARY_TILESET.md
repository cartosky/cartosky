# Boundary Tileset (Topology-First)

This repo now supports a canonical vector tileset for border linework and Great Lakes hydro geometry.

## Runtime endpoints

Served by `backend/app/main.py`:

- `GET /tiles/v3/boundaries/v2/tilejson.json`
- `GET /tiles/v3/boundaries/v2/{z}/{x}/{y}.mvt`

## Frontend source model

`frontend/src/components/map-canvas.tsx` now uses one vector source:

- `https://api.cartosky.com/tiles/v3/boundaries/v2/tilejson.json`

Expected source layers:

- `boundaries` with `kind` in `country|state`
- `counties` with `kind` in `county`
- `hydro` with `kind` in `coastline|land_polygon|great_lake_polygon|great_lake_shoreline`

`land_polygon` is the Natural Earth 10m **land** polygon set — the polygon
counterpart of the `coastline` lines, from the same NE 10m physical family so the
two edges coincide. The frontend uses it as a fill mask drawn immediately *above*
the grid WebGL layer for water-only variables (`display_prep.clip_to_water`, i.e.
SST), which turns the grid's texel staircase into a real coastline and hides the
deliberate under-land fringe bleed. The Great Lakes are erased out of it during
the build (NE 10m land does not cut them out), so it never conflicts with
`great_lake_polygon` — which the runtime layer order actually places *above*
the grid layer — and the lakes keep reading as water when the mask is active.

## Build script

Build with:

```bash
./scripts/build_boundaries_tileset.sh
```

Output MBTiles path:

- `data/boundaries/v1/cartosky_boundaries.mbtiles`
- Legacy compatibility symlink: `data/boundaries/v1/twf_boundaries.mbtiles`
- Legacy `v3` path symlinks are also emitted under `data/v3/boundaries/v1/`

## Zoom strategy (hard minzoom/maxzoom)

Implemented in the build script via separate tippecanoe passes and `tile-join`:

- Country boundaries: `z0-z6` + `z7-z10`
- Coastline: `z0-z6` + `z7-z10`
- State boundaries: `z0-z10`
- County boundaries low detail: `z5-z7`
- County boundaries high detail: `z8-z10`
- Great Lakes polygons: `z3-z8`
- Great Lakes shoreline: `z0-z10`
- Land mask polygons: `z0-z6` + `z7-z10` (deliberately identical tiers, buffer
  and simplification to the coastline passes — any divergence makes the mask edge
  shear away from the drawn coastline)

## Simplification and artifact controls

The build pipeline applies:

- Topology cleanup and snapping with `mapshaper` before tiling
- Strong county simplification at low zoom (`county_lines_low.geojson`)
- Additional county simplification tier for higher zoom (`county_lines_high.geojson`)
- Tippecanoe artifact/payload controls:
  - `--drop-smallest-as-needed`
  - `--coalesce-smallest-as-needed`
  - `--coalesce-densest-as-needed`
  - `--buffer` tuned small (`5-6`)

## Deployment

Set boundary env vars in:

- `deployment/systemd/api.env.example`

Important for browsers: set `CARTOSKY_TILES_PUBLIC_BASE_URL` so TileJSON emits absolute tile URLs (not relative `/tiles/...`).

The API keeps serving `v1` routes for backward compatibility, but the frontend should use `v2` to bypass stale immutable caches after boundary content changes.

Current intent:
- State boundaries stay visible down to low continental zooms.
- County boundaries keep their prior behavior and do not appear until zoom 5.
- Great Lakes shoreline outlines stay visible down to the same low zoom tier as states.

Then restart the main API unit:

```bash
sudo systemctl restart csky-api
```

## Notes

- CARTO vector boundaries and runtime Plotly counties GeoJSON are no longer used by the frontend boundary stack.
- CARTO raster basemap/labels remain in use.
- The build emits the CartoSky-named MBTiles file and a legacy `twf_boundaries.mbtiles` symlink so older runtime defaults still resolve.
- If a source schema changes, keep the `kind` taxonomy stable so frontend filters continue to work.
