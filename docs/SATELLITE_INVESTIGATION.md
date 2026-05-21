# Satellite Product Investigation

## goes2go Output Format

`goes2go` was installed only in the local investigation virtualenv. Installing `goes2go==2025.10.0` also pulled `cartopy`, `metpy`, `s3fs`, `h5netcdf`, and related dependencies. Reading the downloaded NetCDF required one additional local investigation dependency, `h5py==3.16.0`. I did not add any of this to `requirements.txt`.

The current-date `goes_latest(satellite="noaa-goes16", product="ABI-L2-CMIP", domain="C")` probe failed for `noaa-goes16/ABI-L2-CMIPC/2026/141/12`, so GOES-16 should not be treated as a current "latest" source in 2026 without a fallback plan. The real format probe used a historical GOES-16 CONUS scan nearest `2024-05-21T18:00:00Z`, which did pull successfully from the `noaa-goes16` S3 bucket.

Both Band 13 and Band 9 returned an `xarray.Dataset`. The downloaded files were:

| Band | Product file | Raw NetCDF size |
| --- | --- | ---: |
| 13 | `OR_ABI-L2-CMIPC-M6C13_G16_s20241421801175_e20241421803560_c20241421804046.nc` | 3,975,675 bytes |
| 9 | `OR_ABI-L2-CMIPC-M6C09_G16_s20241421801175_e20241421803554_c20241421804046.nc` | 2,958,881 bytes |

The key data variables are `CMI` and `DQF`. `CMI` is the imagery field; `DQF` is the data quality flag. Both candidate bands had shape `(y=1500, x=2500)`. `CMI` was `float32`, units `K`, with `grid_mapping="goes_imager_projection"`. `DQF` was read as `float32`, units `1`, and all sampled pixels in this scan were good (`min=0`, `max=0`).

Band 13, Clean IR 10.3 micrometer:

| Field | Value |
| --- | --- |
| `CMI` range | 206.3199 K to 328.6120 K |
| `CMI` mean | 283.5022 K |
| `band_id` | 13 |
| `band_wavelength` | 10.33 micrometer |
| `time_coverage_start` / `end` | `2024-05-21T18:01:17.5Z` / `2024-05-21T18:03:56.0Z` |

Band 9, Mid-level Water Vapor:

| Field | Value |
| --- | --- |
| `CMI` range | 205.1885 K to 267.3847 K |
| `CMI` mean | 246.8197 K |
| `band_id` | 9 |
| `band_wavelength` | 6.93 micrometer |
| `time_coverage_start` / `end` | `2024-05-21T18:01:17.5Z` / `2024-05-21T18:03:55.4Z` |

Relevant projection metadata from `goes_imager_projection`:

| Field | Value |
| --- | --- |
| `grid_mapping_name` | `geostationary` |
| `perspective_point_height` | `35786023.0` |
| `semi_major_axis` / `semi_minor_axis` | `6378137.0` / `6356752.31414` |
| `longitude_of_projection_origin` | `-75.0` |
| `sweep_angle_axis` | `x` |

## Reprojection Path

The source grid is ABI fixed-grid geostationary. The `x` and `y` coordinates are in radians, not projected meters. For rasterio, multiply those fixed-grid coordinates by `perspective_point_height` to get source geostationary meter coordinates, build a source affine from pixel-center coordinates, and construct a CRS equivalent to:

```text
+proj=geos +h=35786023 +lon_0=-75 +sweep=x +a=6378137 +b=6356752.31414 +units=m +no_defs
```

The actual source-derived values from the sample were:

| Field | Value |
| --- | ---: |
| Source shape | `1500 x 2500` |
| Source pixel spacing | about `2003.97 m` in geostationary projection space |
| Source x extent, center-to-center | `-3626269.33` to `1381769.94` |
| Source y extent, center-to-center | `4588197.76` to `1584175.83` |

`rasterio.warp.reproject` with `pyproj` handled this directly into `EPSG:3857` without extra geospatial dependencies. The investigation warp targeted CartoSky's current CONUS Web Mercator bbox from `backend/app/services/builder/cog_writer.py` at 4 km:

| Field | Value |
| --- | --- |
| Target bbox | `[-14916811.77, 2753408.11, -6679169.45, 7361866.11]` |
| Target resolution | `4000 m` |
| Target shape | `1153 x 2061` |
| Finite target coverage | `79.9467%` for both bands |

For IR brightness temperature, `bilinear` is a reasonable v1 resampling method. It smooths continuous scalar temperatures and avoids nearest-neighbor blockiness. For data quality flags, use `nearest` or apply the source `DQF` mask before warping the CMI field. The sample warp masked pixels where `DQF > 1` before bilinear reprojection.

## Grid Binary Compatibility

The current grid binary path is compatible with scalar GOES bands after reprojection. The important contract is in `backend/app/services/grid.py` and `frontend/src/lib/grid-webgl.ts`:

- Grid frames are raw packed bytes with no file header.
- Supported dtypes are `uint8` and `uint16`; most scalar fields default to little-endian `uint16`.
- `uint16` samples are written as little-endian bytes with `encoded = round((value - offset) / scale)`, clipped below the nodata sentinel.
- Nodata is `65535` for `uint16`.
- Frame metadata supplies `width`, `height`, `bbox`, `projection`, and the manifest supplies `dtype`, `endianness`, `scale`, `offset`, `nodata`, and `units`.
- The frontend uploads `uint16` as two bytes per sample (`R=low`, `G=high`) and decodes in the shader using the manifest `scale` and `offset`, then maps the scalar to a LUT palette.

The sample GOES reprojection was packed successfully as little-endian `uint16` with:

| Field | Value |
| --- | --- |
| `dtype` | `uint16` |
| `endianness` | `little` |
| `scale` | `0.01` |
| `offset` | `150.0` |
| `nodata` | `65535` |
| `units` | `K` |

This covers 150.00 K through 805.34 K, so it is comfortably safe for ABI IR brightness temperatures. A coarser `scale=0.1` would also work and shrink gzip/brotli only marginally; I would keep `0.01 K` for fidelity unless we later decide that frontend legend precision should be lower.

No renderer changes are required for Band 13 or Band 9 as scalar brightness-temperature fields. Required backend additions would be a satellite model/product config entry, packing config entries, a color map/legend for each band, and a satellite publisher that writes the normal value/grid/manifest artifacts.

One mismatch: `grid_supported_pair()` must know the new satellite model/variables before `build_grid_manifests_for_run_root()` will emit manifests. That is configuration/wiring, not a binary-format blocker.

## Geocolor Feasibility

Geocolor is not a good v1 fit for the current renderer. The existing WebGL grid renderer is a scalar renderer: it fetches one packed grid field, decodes one physical value per texel, and looks up RGBA through a palette/LUT. It does not currently support a native RGB or RGBA product where each texel already has three color channels.

Possible implementation paths:

- Add a separate RGB/RGBA raster layer path with a new manifest subtype, raw RGB(A) frame format, texture upload branch, and shader branch that samples color directly.
- Encode Geocolor as three separate scalar grids and composite client-side. This is heavier, awkward for manifests, and still needs renderer changes.
- Pre-render Geocolor as raster tiles/COGs outside the scalar grid path. That would bypass the current WebGL scalar contract and create a second renderer path.

Recommendation: Geocolor is out for v1. Launch scalar IR/WV first, learn the scheduler/storage/freshness shape, and then add an explicit RGB satellite renderer if Geocolor becomes a priority.

## Scheduler Integration Pattern

MRMS is the closest pattern because it is observed data, not forecast-hour model data:

- `backend/app/services/mrms_poller.py` discovers recent scans, freezes a rolling window, decodes only new scans, reuses already-published frames when possible, publishes a run, and enforces retention.
- Defaults are in `deployment/systemd/scheduler-radar.env.example`: `CARTOSKY_MRMS_POLL_SECONDS=120`, `CARTOSKY_MRMS_WINDOW_MINUTES=120`, `CARTOSKY_MRMS_FRAME_CADENCE_MINUTES=5`, and `CARTOSKY_MRMS_KEEP_RUNS=6`.
- MRMS computes target frame count as `(window_minutes // frame_cadence_minutes) + 1`, so a 3-hour / 15-minute satellite window should be treated as 13 frames if we mirror this inclusive-window convention.

A satellite job at 15-minute cadence should follow the same observed-data bundle pattern:

1. List or query candidate ABI-L2-CMIPC files for the desired satellite/product/domain/bands.
2. Freeze the latest complete 15-minute scan times for the rolling window.
3. Download/decode only scans not already present in the previous published bundle.
4. Reuse prior grid/value artifacts for unchanged frames.
5. Reproject new CMI fields to the target `EPSG:3857` grid.
6. Write value COGs, grid binaries, sidecars, grid manifests, run manifest, and latest pointer.
7. Enforce staging/published/manifest retention.

Manifest time semantics should be scan-time based, closer to MRMS than HRRR/GFS. Use a run id based on publish time, but each frame's `valid_time` should be the ABI scan time. The best scan time fields are `time_coverage_start`, `time_coverage_end`, and the scalar `t` midpoint. For user-visible frame time, use the midpoint `t` or `time_coverage_end`; keep the full coverage interval in `source_metadata` for freshness/debugging.

Because GOES-16 no longer appears to have current latest CONUS files, production should probably target the current operational GOES-East bucket/satellite for live imagery, while keeping the satellite id configurable. As of this local probe, `goes2go` defaults to `noaa-goes19`.

## Storage Estimate

The sample reprojected output targeted 4 km CONUS and wrote one packed grid frame plus compressed sidecars. Sizes:

| Band | Float32 GeoTIFF sample | Raw `u16.bin` | gzip sidecar | brotli sidecar |
| --- | ---: | ---: | ---: | ---: |
| 13 | 4,760,469 bytes | 4,752,666 bytes | 3,232,475 bytes | 3,120,641 bytes |
| 9 | 4,416,589 bytes | 4,752,666 bytes | 2,871,607 bytes | 2,585,582 bytes |

Using the MRMS inclusive rolling-window convention, 3 hours at 15-minute cadence is 13 frames.

Band 13 only:

| Artifact set | Estimate |
| --- | ---: |
| Raw upstream NetCDF retained | 51.7 MB |
| Raw grid bins only | 61.8 MB |
| Grid bin + gzip + brotli | 144.4 MB |
| Value GeoTIFF + grid bin + gzip + brotli | 206.3 MB |
| Brotli transfer footprint only | 40.6 MB |

Band 13 + Band 9:

| Artifact set | Estimate |
| --- | ---: |
| Raw upstream NetCDF retained | 90.1 MB |
| Raw grid bins only | 123.6 MB |
| Grid bin + gzip + brotli | 267.0 MB |
| Value GeoTIFF + grid bin + gzip + brotli | 396.5 MB |
| Brotli transfer footprint only | 74.2 MB |

I could not determine a current MRMS storage footprint from the local dev box because `/opt/cartosky/data/published/mrms` was not present/readable and no local `published/mrms` tree was found in the repo checkout. From configured MRMS shape alone, MRMS is likely materially larger per retained run because it is 1 km CONUS and currently keeps reflectivity plus radar precip type with multiple LODs.

## Recommended Launch Scope

Launch v1 with Band 13 only, at 15-minute cadence, with a 3-hour rolling retention window.

Band 13 is the best first satellite product: it is familiar, high-signal at all hours, scalar, compact, and maps cleanly onto the current palette-based grid renderer. Use `CMI` brightness temperature in Kelvin, mask bad `DQF`, reproject with bilinear resampling, and pack to little-endian `uint16` with `scale=0.01`, `offset=150.0`, `nodata=65535`.

Do not include Geocolor in v1. It needs a real RGB renderer path and would turn a clean scalar observed-product launch into a rendering-platform project.

Band 9 should be a fast follow, not part of the smallest first launch. It is technically feasible and storage is modest, but it doubles scheduler/download/publish surface area and needs its own palette/product UX. Once Band 13 is stable in production freshness and retention, Band 9 can use the same pipeline.

Operationally, do not hard-code GOES-16 for live current imagery. Keep `satellite`, `bucket`, `product`, `domain`, and `bands` configurable and default production to the current operational GOES-East source. The investigation confirmed historical GOES-16 access, not current GOES-16 latest availability.

## Open Questions

- Which live satellite should production use for GOES-East in 2026: `noaa-goes19` as `goes2go` defaults, or a different operational target?
- Should user-visible frame time be ABI midpoint `t` or scan `time_coverage_end`? I recommend midpoint for meteorological validity and end time for freshness diagnostics.
- Should the pipeline retain raw NetCDF source files, or treat them as disposable download cache after publishing?
- What palette should CartoSky use for Clean IR and Water Vapor? This is product/design work, but it is the main remaining frontend decision for scalar bands.
- Should satellite get its own model id such as `goes-east`/`satellite`, or a provider id such as `goes` with region/domain metadata?

## Suggested Next Steps

1. Decide the live satellite identifier and public product naming.
2. Add a small satellite plugin/config with Band 13 only and a 4 km CONUS target grid.
3. Implement a satellite poller modeled on MRMS with `poll_seconds` around 300 seconds, `frame_cadence_minutes=15`, `window_minutes=180`, and `keep_runs` consistent with the rolling-window policy.
4. Add scalar packing/color-map entries for Band 13 and grid support wiring.
5. Add focused tests for ABI projection metadata to Web Mercator warp, DQF masking, `uint16` packing, manifest scan-time metadata, and retention.
6. Run a production-like dry run against the current operational GOES-East bucket before enabling the frontend picker.
