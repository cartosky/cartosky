# Variable Rollout Guide

## Purpose

This document is a high-level checklist for adding a new weather variable to CartoSky.

It is based on the `vort500` rollout across GFS, HRRR, and NAM and focuses on the full path:

- upstream field confirmation
- backend model registration
- unit conversion and color mapping
- optional contour overlays
- grid packing
- frontend behavior
- prod rebuild/publish validation

## Rollout Order

For a new variable, work in this order:

1. Confirm upstream availability for each model you plan to support.
2. Decide the canonical variable key, units, and display behavior.
3. Add model registry entries and aliases.
4. Add color map and legend metadata.
5. Add unit conversions if upstream units differ from display units.
6. Add grid packing support.
7. Add any special overlay metadata such as contours.
8. Adjust frontend behavior if the variable needs different UX rules.
9. Verify locally.
10. Rebuild and republish on prod.

Do not skip the upstream confirmation step. A model plugin can support pressure-level structure generally while still missing a specific field.

## 1. Confirm Upstream Availability

Confirm the actual upstream GRIB inventory for each model and field.

For pressure-level variables, the most reliable quick check is usually the `.idx` inventory on NOMADS. Example targets:

- GFS: `.../gfs.tHHz.pgrb2.0p25.fXXX.idx`
- HRRR: `.../hrrr.tHHz.wrfprsfXX.grib2.idx`
- NAM: `.../nam.tHHz.conusnest.hiresfXX.tm00.grib2.idx`

What to confirm:

- the primary shaded field exists with the exact level you need
- any contour companion field exists with the same level
- the model product is correct for that field, such as `sfc`, `prs`, or model-specific pressure-level products

Example from `vort500`:

- shaded field: `ABSV:500 mb`
- contour field: `HGT:500 mb`

## 2. Define the Variable Contract

Before editing code, decide:

- canonical key, for example `vort500`
- display name
- units shown to the user
- whether the variable is primary or derived
- whether it should be considered a core variable across models
- whether it needs a second component for contours or derived display

Keep this contract stable across models when possible. Model-specific differences should usually live in selectors or product hints, not in the user-facing variable shape.

## 3. Add Model Registry Entries

Files:

- `backend/app/models/gfs.py`
- `backend/app/models/hrrr.py`
- `backend/app/models/nam.py`

For each model:

- add aliases in `normalize_var_id`
- add the primary `VarSpec`
- add any hidden/component `VarSpec` such as `hgt500`
- add color map mapping
- add display order
- add variable grouping
- add conversion metadata if needed

For pressure-level fields, prefer helper builders such as:

- `_model_tmp_level_component(level_hpa)`
- `_model_hgt_level_component(level_hpa)`
- `_model_absv_level_component(level_hpa)`

This keeps the implementation model-agnostic and easy to expand later.

## 4. Add Color Map Metadata

File:

- `backend/app/services/colormaps.py`

Add:

- range
- anchors or colors
- legend title
- display name
- palette kind
- transparency rules if needed

If the variable uses a display range that is intentionally different from its physical domain, be explicit. `vort500` is one example because the product is displayed as a positive-focused vorticity field even though the data can include negative values.

## 5. Add Unit Conversion

File:

- `backend/app/services/builder/fetch.py`

If upstream GRIB units do not match the display units, add a converter and register it.

This was a key pitfall for `vort500`:

- upstream `ABSV` arrived in `s^-1`
- display expected `10^-5 s^-1`
- without conversion, the overlay appeared transparent because values were far below the palette thresholds

Also add the conversion key in each model registry that uses the variable.

## 6. Add Grid Packing Support

File:

- `backend/app/services/grid.py`

If a variable is buildable, it must be added to `_PACKING_BY_MODEL_VAR` for each supported model.

This was the main rollout pitfall for NAM:

- the model registry was correct
- the frame built successfully through validation
- the build still failed at grid-frame write time because `nam/vort500` was missing from grid packing

If grid packing is missing, the scheduler will fail with:

`Unsupported grid pack target: {model}/{var}`

## 7. Add Optional Contour Overlay Support

If a product needs a shaded field plus contour lines:

- store contour metadata in selector hints on the primary variable
- keep the contour component as a reusable model variable such as `hgt500`
- let the builder generate contour GeoJSON from those hints

Useful hint fields:

- `contour_component`
- `contour_interval`
- `contour_start`
- `contour_end`
- `contour_key`
- `contour_label`
- `contour_product` when the contour source lives in a different upstream product

Example:

- HRRR `vort500` uses pressure-level data from `prs`, so the contour companion needed `contour_product: "prs"`

## 8. Review Frontend Behavior

Files to check:

- `frontend/src/App.tsx`
- `frontend/src/lib/anchor-labels.ts`
- `frontend/src/pages/variables.tsx`

Questions to answer:

- should anchor city labels appear for this variable?
- should special legends or labels be shown?
- does the product need contour overlays enabled?
- should the variable be documented in the variables page?

For `vort500` we hid anchor city labels because they cluttered the map.

## 9. Verification Checklist

At minimum run:

- backend compile checks for touched modules
- the relevant per-model invariant tests, such as `backend/tests/test_gfs_invariants.py`, `backend/tests/test_hrrr_invariants.py`, and `backend/tests/test_nam_invariants.py`
- capability serialization test
- frontend typecheck/build

Useful checks after a build:

- confirm the variable appears in the dropdown for supported models
- confirm sidecar `units`, `min`, and `max` match the intended scale
- confirm contours load and update across forecast hours
- confirm grid manifests are generated for the variable

For converted variables, inspect a published sidecar directly. If the values still look like raw upstream units, the conversion did not actually apply.

Invariant tests are especially important when adding a new buildable variable because they catch drift in:

- buildable variable sets
- default variable ordering and schema snapshots
- selector and alias expectations
- model-specific assumptions that are easy to miss during a local smoke test

## 10. Prod Rebuild and Publish

If a variable was already built before a fix landed, remove both staging and published artifacts before rebuilding:

- `.../staging/{model}/{run}/{var}`
- `.../published/{model}/{run}/{var}`

Then rerun the scheduler for that model and run.

Important operational lesson:

- if a previous run built correct staging artifacts but failed during publish, a later scheduler invocation may report `built=0` and simply republish the old staged artifacts
- if those staged artifacts were built before a fix, you must delete the staging directory too or you will keep promoting stale output

## Common Pitfalls

### Upstream confirmed for one model, assumed for others

Do not assume. Confirm each model independently.

### Missing unit conversion

Symptom:

- variable renders with contours but no visible color fill

Cause:

- display palette expects transformed units but the raw GRIB values were used

### Missing grid packing entry

Symptom:

- build succeeds until grid-frame generation, then fails with unsupported pack target

### False-positive value range warnings

If the display range is intentionally not a strict physical range, either relax the check or mark the spec so those warnings do not fire unnecessarily.

### Stale staged artifacts republished after a fix

If prod logs show `built=0`, confirm you actually removed the staging var directory before rebuilding.

### Temp directories in published/staging runs blocking promotion

Promotion copies the run tree. Stray temp directories with bad ownership can break publish even when the variable itself built correctly.

## Minimal Variable Rollout Checklist

- confirm upstream inventory for each model
- define canonical variable key and units
- add model aliases and `VarSpec`
- add color map metadata
- add unit conversion if needed
- add grid packing entries for every supported model
- add contour metadata if needed
- update frontend behavior and docs
- verify locally
- delete stale staging and published artifacts before prod rebuild

## Suggested Future Improvement

The most error-prone step today is remembering to add grid packing for every new buildable variable/model pair. A future improvement would be a startup-time validation that compares buildable capability pairs against `_PACKING_BY_MODEL_VAR` and fails fast when a model variable is missing grid support.
