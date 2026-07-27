# Phase 1 — Value-COG dependency inventory and deletion boundary

Status: **inventory complete, deletion not started.** Produced 2026-07-27 against
`305fec9f`. Companion to `docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md` Phase 1.

---

## Headline finding: NAM blocks the substrate removal, not the refactor

The plan's Phase 1 kickoff prompt opens with "the COG-to-binary-sampling
migration is complete across all products." **That premise is not currently
true: NAM was deliberately not migrated** and is scheduled for decommission
rather than migration (`docs/MAX_WEEK_EXECUTION_PLAN_2026-07-27.md:403` —
RRFS upstream parallel data not available until 2026-08-11, NAM replacement
early October).

This does **not** block all of Phase 1, because the COG surface splits cleanly
into two groups (see the split proposal at the end).

### Unresolved production question — must be answered before any deletion

Nothing checked into the repo sets `CARTOSKY_COG_SAMPLING_MODELS`. Per the
checked-in configuration alone, **NAM resolves as binary-only like every other
model.** If NAM is genuinely still on COGs, production must be setting that
variable in a file outside version control:
`deployment/systemd/csky-nam-scheduler.service:13` reads
`EnvironmentFile=-/etc/cartosky/scheduler-nam.env`, and
`deployment/systemd/scheduler-nam.env.example` does **not** contain the flag.

Prod env drift is already a known pattern this week
(`MAX_WEEK_EXECUTION_PLAN_2026-07-27.md:143`).

Both the scheduler (write path) and the API (read path) must agree. A flag set
on only one produces a split-brain: COGs written but binaries sampled, or the
reverse.

```bash
sudo grep -r COG_SAMPLING /etc/cartosky/ ; systemctl show csky-nam-scheduler csky-api -p Environment
```

---

## Gating mechanism (verified in source)

`backend/app/config/__init__.py:88-134`.

- `CARTOSKY_COG_SAMPLING_MODELS` — comma-separated **emergency opt-OUT** list.
  Empty (the checked-in default) means *no* model uses the COG path.
- `binary_sampling_enabled(model)` returns `True` unless the model is in that list.
- `CARTOSKY_BINARY_SAMPLING_MODELS` — the retired opt-IN allowlist. Now a
  **no-op** that logs a deprecation warning once (`config/__init__.py:104-116`).

Note the lever's documented weakness (`config/__init__.py:125-129`): opting a
model back onto COGs only affects runs published *after* the flip. Recent
binary-only runs have no COGs, so it cannot recover existing runs. The
"emergency fallback" is therefore weaker than its name implies.

### What a COG-enabled model actually still uses

`grid_build_enabled()` returns `True` unconditionally
(`config/__init__.py:170-171`), and the grid-binary write at
`builder/pipeline.py:1972` sits outside the `binary_only` branch. **A
COG-enabled model still writes grid binaries.** Rendering is on binaries for
every model; only *sampling reads* differ.

So NAM's live dependency is narrow:

| Need | Location |
|---|---|
| Value-COG write | `builder/pipeline.py:1874` |
| Structural gate | `validate_cog`, `builder/pipeline.py:633`, called `:1888` |
| Sanity gate | `check_value_sanity`, `builder/pipeline.py:852`, called `:1899` |
| Sampling read | `_resolve_val_cog`, `services/sampling.py:579`; `sample_value` `:947` |
| Sample endpoints | `main.py:5830-5874`, `main.py:6040-6085` |
| Meteogram fan-out | `services/forecast_page.py:3341-3371` |
| Completion marker | `services/scheduler.py:655-662` → `_frame_value_path` `:621` |
| Hover-availability flag | `_frame_has_cog`, `main.py:3565-3571` |
| Cache-key substrate tag | `main.py:3652-3658`, `:3669-3672`; `forecast_page.py:3301` |
| Admin telemetry | `_value_cog_path`, `services/admin_telemetry.py:655`, used `:1653`, `:1777` |

---

## `cog_writer.py` classification (684 lines)

`backend/app/services/builder/cog_writer.py`. Eleven modules import from it.

### PRESERVE — live, must survive extraction

| Symbol | Line | Live callers |
|---|---|---|
| `warp_to_target_grid` | 482 | `wpc_publish`, `ndfd_publish:291`, `rtma_ru_poller:357,410`, `builder/members.py:1110,1198,1401,1419,1435`, `builder/derive.py:3356`, `builder/pipeline.py:313,580,1785`, `mrms_publish:1091,1108` |
| `compute_transform_and_shape` | 213 | `climatology:242,313`, `goes_processing:58`, `builder/derive.py:3340`, `pipeline:129`, `mrms_publish:1058,1064` |
| `get_grid_params` | 168 | `goes_processing:57`, `pipeline:128,1487,1885`, `mrms_publish:1057,1063`, `climatology:58,242,313` |
| `REGION_BBOX_3857` | — | `climatology.py:11` |
| `_grid_meters_from_capabilities` | 193 | internal support for `get_grid_params` |
| `TARGET_GRID_METERS` | — | internal fallback for `get_grid_params` |
| `_gdal` | 150 | **`builder/pipeline.py:1130-1131`** |
| `_find_gdal_tool`, `_gdal_tools` | 121, 147 | internal support for `_gdal` |

> [!IMPORTANT]
> `_gdal` / `_find_gdal_tool` are **not** COG-specific. `pipeline.py:1130-1131`
> resolves `gdalwarp` and `gdal_contour` through `_gdal()` for **contour
> generation**. Deleting them breaks contours for every model. An earlier
> reconnaissance pass classified these as deletable; that was wrong.
>
> `warp_to_target_grid` is pure `rasterio.reproject` and does **not** touch the
> GDAL CLI — it carries no COG dependency at all.

### DELETE — COG-specific

| Symbol | Line | Note |
|---|---|---|
| `write_value_cog` | 381 | NAM-blocked |
| `write_rgba_cog` | 270 | **zero production callers** — dead regardless of NAM |
| `_build_continuous_rgba_cog` | 615 | only from `write_rgba_cog` |
| `_continuous_rgba_overviews_use_nearest` | 365 | only from `write_rgba_cog` |
| `_write_base_gtiff` | 580 | only from the two write functions |
| `_gtiff_to_cog` | 666 | only from the two write functions |
| `_overview_levels` | 243 | only from the two write functions |
| `_run_gdal` | 561 | COG-internal only (333, 351, 461, 645, 652, 676). Contours use their own `subprocess.run` at `pipeline.py:1156`. |
| `ensure_gdal` | 158 | optional startup helper, no production caller |
| `COG_BLOCKSIZE`, `COG_COMPRESS` | 111, 114 | |
| `REGION_BBOX_4326` | — | test-only (`test_conus_bbox_consistency.py:34`) |

`write_rgba_cog` is referenced only by `backend/scripts/test_pipeline.py:118`
and three test modules — no service imports it.

---

## Production-dead COG read path

`build_grid_for_run` (`services/grid.py:2417`) and
`write_grid_frame_from_value_cog_for_run_root` (reads COGs via `rasterio.open`
at `grid.py:2030`; globs `fh*.val.cog.tif` at `grid.py:2438`) have **zero
production callers** — verified exhaustively. This is the plan's "emergency COG
sampling/write fallback."

**But it is the fixture factory for roughly 50 tests**, which is the single
largest cost in Phase 1 and is *not* "delete COG-only tests":

| File | Uses |
|---|---|
| `tests/test_grid.py` | ~28 `_write_value_cog` fixtures feeding `build_grid_for_run` |
| `tests/test_grid_only_contracts.py` | `:134`, `:176` |
| `tests/test_frames_cache_control.py` | `:200`, `:222` |
| `tests/test_api_eps_ensemble_contract.py` | `:155` |
| `tests/test_api_gefs_ensemble_contract.py` | `:502` |

These tests do not test COGs — they test grid manifests, frames cache-control,
and ensemble contracts *through* a COG-shaped on-ramp. Removing the fallback
forces migrating them onto `write_grid_frame_for_run_root`
(`grid.py:1819`), which is the live production write path
(`pipeline.py:1972`, `grid.py:1928`) and is already exercised COG-free at
`test_grid.py:107`.

## Migration tooling

- `backend/scripts/canary_binary_sampler.py` — COG-vs-binary shadow comparison.
  Purpose ends when the last COG model does.
- `backend/scripts/test_pipeline.py:26-43` — imports `write_value_cog`,
  `write_rgba_cog`, `validate_cog`.
- `tests/test_canary_binary_sampler.py` — covers the above.

## Test blast radius

36 of 132 backend test files reference COG. Four are pure COG-writer tests
(`test_cog_writer_overviews.py`, `test_cog_writer_value_grid_parity.py`,
`test_cog_writer_continuous_fallback.py`, `test_cog_writer_na_region.py`).
Import-breakage on rename: `test_precip_anomaly_products.py:23`,
`test_gefs_tmp2m_anomaly.py:27`, `test_binary_sampler_parity.py:33`,
`test_conus_bbox_consistency.py:34`, `test_forecast_meteogram_api.py:1074,1370,1651,1910`,
plus `backend/scripts/` importers.

## Frontend

No frontend COG-substrate coupling. `has_cog` is a **substrate-neutral
"hover-samplable frame exists" flag** (`main.py:3565-3571`), consumed at
`frontend/src/lib/api.ts:200` and `frontend/src/App.tsx:2864,3582`. It should be
renamed eventually but carries no COG dependency. `pages/admin/roadmap.tsx:117`
has a stale roadmap string.

---

## Test baseline (pre-Phase 1)

`../.venv/bin/python -m pytest tests -q -p no:randomly` →
**1865 passed, 1 skipped, 0 failed** in 32.30s.

The previously reported `1864 passed, 1 skipped, 1 failed` did **not**
reproduce; the passed count rose by exactly one. Consistent with an external
DNS-dependent Herbie test that passes when resolution succeeds. No code defect.
This baseline is environment-dependent and will regress offline.

---

## Proposed split

### Phase 1A — NAM-neutral, safe now

1. Extract the PRESERVE set into an accurately named module (e.g.
   `builder/raster_grid.py`), re-pointing all eleven importers. Keep `_gdal`
   and friends with it.
2. Delete the `write_rgba_cog` subtree — zero production callers.
3. Delete the `CARTOSKY_BINARY_SAMPLING_MODELS` no-op flag and its warning.
4. Leave `write_value_cog`, `validate_cog`, and every substrate branch intact;
   `cog_writer.py` shrinks to genuine COG-write code only.

Highest-value piece is (1): Phases 2A and 3 edit warping and grid-geometry code
all week, so renaming now avoids churn later.

### Phase 1B — blocked until NAM decommission (~October)

Substrate branch removal, `_resolve_val_cog`, cache-key tags, scheduler
completion marker, admin telemetry, `canary_binary_sampler.py`, the
`build_grid_for_run` fallback, and the ~50-test fixture migration.

### Impact on Phase 2A

Phase 1's stated purpose was shrinking Phase 2A's surface — specifically
`_resolve_val_cog` carrying the discarded-`region` pattern
(`MAX_WEEK_EXECUTION_PLAN_2026-07-27.md:250`). Deferring 1B means `sampling.py`
keeps that function through Phase 2A. The tax is modest: NAM is not going
global, so `_resolve_val_cog` only needs the canonical-region no-op default,
which Phase 2A must support anyway.
