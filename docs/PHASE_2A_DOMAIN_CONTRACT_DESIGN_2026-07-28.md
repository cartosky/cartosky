# Phase 2A Design — Backend Artifact-Domain Contract

**Date:** 2026-07-28 · **Status:** locked (operator decisions recorded below) · **Parent plan:** `MAX_WEEK_EXECUTION_PLAN_2026-07-27.md`
**Scope:** design record for implementation; canonical-region paths, URLs, payloads, and timing must be preserved byte-for-byte.

All citations verified against `main` through commit `25bbc29e` (post-Phase-1).

## Operator decisions — locked 2026-07-28

1. **Layout: Option B — parallel domain run tree.** Non-canonical domains live under a `domains/` namespace parallel to the canonical run tree (details in §2).
2. **Declaration granularity: var-level `supported_build_regions`** (existing field, `backend/app/models/base.py:63`). Per-variable global rollout permitted; a domain's run manifest covers the declaring subset.
3. **Admin status and telemetry remain canonical-only in 2A** — domain trees invisible, not misreported. Per-domain surfaces are a Phase 3 deliverable.
4. **frames-404 telemetry gains a `domain` column now** (persisted schema + admin consumer — `record_frames_404` gains the parameter, `_recent` entries gain the field, `admin_telemetry.build_frames_404_section` surfaces it).
5. **Edge-served artifact URLs carry the domain in the path, not a query parameter** (supersedes the earlier query-param sketch; adversarial review finding M1). For every non-canonical edge-served artifact — grid binaries, contours, vectors — `domains/{d}` is inserted immediately before the `{model}` segment of the existing route. Canonical artifact URLs remain exactly unchanged and contain neither a domain path segment nor `domain=`. Isolation is structural and does not depend on unversioned Cloudflare cache-key configuration. `domain=` remains the mechanism on control/selection APIs (`/runs`, `/manifest`, `/vars`, `/frames`, `/grid-manifest`, `/sample`, `/sample/batch`, meteogram, `/bootstrap`) and permalink state — never for isolating immutable artifact bodies.

An adversarial design review ran 2026-07-28 (verdict: approve with amendments). All amendments are incorporated below; the review record is §7.

Deferred to Phase 3 (recorded, not decided): per-domain retention counts; entitlement gating for `domain=global`; storage placement if a separate volume is ever purchased (would break the same-filesystem promote invariant unless the global domain's staging and published trees move together).

---

## 1. Inventory: where `region` is accepted and discarded, and what collides today

### 1.1 Scheduler — `backend/app/services/scheduler.py`

| Site | Lines | Behavior today |
|---|---|---|
| `CANONICAL_COVERAGE = "conus"` | 71 | Global fallback constant |
| `_default_build_region` | 352–355 | Reads `capabilities.canonical_region`, falls back to `CANONICAL_COVERAGE` |
| `_build_regions_for_var` | 358–364 | **Always returns `[default_region]`** — the single build-target chokepoint |
| `_scheduled_targets_for_cycle` | 562–586 | Targets are already `(region, var, fh)` tuples; region threaded into targets… |
| `_frame_sidecar_path` | 589–601 | …then **`del region` (600)**; `staging/{model}/{run}/{runtime_var}/fh{fh:03d}.json` |
| `_frame_primary_artifact_path` | 604–624 | **`del region` (621)**; grid meta under `staging/{model}/{run}` |
| `_frame_artifacts_exist`, `_sidecar_quality` | 627–646, 649–675 | Accept `region=`, forward into the discarding functions |
| `_build_one` / `_build_bundle` | 862–898, 936–992 | Region genuinely used for **fetch coverage and warp extent** (`build_frame(region=...)`) but never for output paths |
| `_normalized_publish_region` | 1044–1046 | Normalizes then feeds discarding path fns |
| `_latest_pointer_path` | 1049–1051 | **`del region` (1050)**; always `published/{model}/LATEST.json` |
| `_manifest_path` | 1054–1056 | **`del region` (1055)**; always `manifests/{model}/{run}.json` |
| `_write_latest_pointer` | 1113–1126 | Records `"region"` in payload but writes the single canonical path |
| `_promotion_ready_regions` | 1129–1151 | Loops regions, but all regions probe the **same** artifact paths |
| `_promote_run` | 1297–1353 | `staging/{model}/{run}` → `published/{model}/{run}`; region-blind |
| `_write_run_manifest` | 1405–1510 | `region=` (1412) only lands in payload (1505); manifest path (1430) domain-blind — global manifest would **overwrite** canonical, last writer wins |
| `_enforce_run_retention` | 1513–1532 | Root-scoped; skips non-run-id children (1521–1523) |
| `_enforce_manifest_retention` | 1535–1563 | Files-only, run-id-parsed stems |
| `_enforce_ensemble_stats_health_retention` | 1566–1594 | Keyed to canonical published root |
| `_publish_run_snapshot` | 2002–2042 | One publish path, one namespace |
| Retention tail | 2583–2592 | Canonical roots only |
| Member/stats promote + backfill | 2732, 2742, 2819, 2829, 2845; scan 2891–2907 | Canonical-only; scan skips non-run-id dirs (2901–2903) |

### 1.2 Grid layer — `backend/app/services/grid.py`

| Site | Lines | Behavior |
|---|---|---|
| `grid_dir` | 1258–1260 | **`del region` (1259)**; `published/{model}/{run}/{var}/grid` |
| `grid_manifest_path` | 1263–1264 | Inherits the discard |
| `grid_frame_path` | 1310–1321 | `region=` kwarg discarded via `grid_dir` |
| `grid_frame_meta_path` | 1343–1344 | No region param at all |
| `_iter_grid_variable_run_roots` | 1230–1255 | **Already discovers one level of nesting** `run_root/{region_dir}/{var}` (1248) |
| `build_grid_manifests_for_run_root` | 2365–2386 | Takes `run_root` param — reusable for a domain-scoped run root |

### 1.3 Sampling — `backend/app/services/sampling.py`

| Site | Lines | Behavior |
|---|---|---|
| `sample_binary_value_seek` | 299–329 | `region=` (307) forwarded into the discarding resolver |
| `sample_member_values_seek` | 332–435 | **`del region` (354)** |
| `_resolve_sidecar` | 504–521 | **`del region` (513)** |
| `_resolve_binary_grid_frame` | 524–562 | **`del region` (542)** — the single resolver behind `/api/v4/sample`, `/sample/batch`, `has_cog`, all meteogram sampling |
| `resolve_run` | 568–572 | Forwards to `main._resolve_run`, which discards |
| `run_complete_for_variables` | 645–670 | Region → `_load_manifest`, discarded |
| `run_has_member_data` | 673–690 | **`del region` (683)** |
| `resolve_latest_complete_run` | 693–746 | Region threaded to `_scan_manifest_runs`/`_load_manifest`, both discard |
| `manifest_frame_entries` / `manifest_frame_hours` | 749–796 / 799–806 | Same |
| `sample_binary_value` | 818–857 | Same |
| `read_frame_valid_times` | 861–881 | Same |

### 1.4 API layer — `backend/app/main.py`

| Site | Lines | Behavior |
|---|---|---|
| `DATA_ROOT` / `PUBLISHED_ROOT` / `MANIFESTS_ROOT` | 103, 106–107 | All under one data root |
| `_canonical_region_for_model` / `_normalized_request_region` | 2734–2744 | **Dead code — no callers.** Fold into domains module or delete |
| `_latest_pointer_path` | 2747–2749 | **`del region` (2748)** |
| `_manifest_path` | 2752–2754 | **`del region` (2753)** |
| `_latest_run_from_pointer_for_region` | 2757–2780 | Validates pointer against canonical run dir + manifest (2775–2777) |
| `_scan_manifest_runs` | 2856–2878 | **`del region` (2857)** |
| `_var_has_grid_runtime_ready_for_region` / `_ready_runtime_state_for_run` | 2903–2916 / 2919–2957 | Region threaded, discarded downstream |
| Bootstrap selection state | 3245–3302 | `region` used only for **viewport preset**; resolve calls discard it |
| `_resolve_latest_run` | 3372–3394 | Region threaded, discarded |
| `_resolve_run` | 3397–3409 | **`del region` (3398)** |
| `_run_version_token` / `_grid_version_token` | 3456–3462 / 3465–3471 | mtime of canonical manifest paths — tokens **shared across domains** today |
| `_published_var_dir` | 3520–3522 | **`del region` (3521)** |
| `_frame_has_cog` | 3529–3533 | Forwards into discarding resolver |
| `_load_grid_manifest` | 3536–3545 | **`del region` (3537)** |
| `_grid_file_url` | 3548–3554 | **`del region` (3549)**; URL `/api/v4/grid/{model}/{run}/{var}/{file}?v={token}` |
| `_grid_manifest_frame_file_is_valid` | 3568–3589 | Region discarded via `grid_frame_path` |
| `_resolve_frame_var_dir` | 3592–3602 | **`del region` (3594)** |
| `_sample_cache_key` / `_sample_batch_cache_key` | 3605–3615 / 3618–3627 | **No domain component** — cross-domain cache poisoning risk |
| Meteogram `MeteogramRequestIn.region` | 4036, forwarded 4073 | Threaded into forecast_page, discarded at bottom |
| Routes accepting `region=` (viewport semantics, discarded) | `/runs` 4974, `/manifest` 4996, `/vars` 5050, `/frames` 5083/5090, `/grid-manifest` 5192/5199, grid files 5728/5740 (5734/5746), `/sample` 5752/5758, `SampleBatchIn.region` 2600, bootstrap 4787/4794 | |
| `_seconds_since_publish` | 5569–5591 | **`del region` (5578)** — publish-recency telemetry |
| `_emit_frames_404` | 5594–5625 | Accepts `region`, never persists it; `frames_404_telemetry.py` has no region/domain field |
| `_get_grid_file` X-Accel-Redirect | 5706–5719 | Relative to `PUBLISHED_ROOT` — any layout under published root keeps the nginx contract |
| `LOOP_CACHE_ROOT` | 108–113 | Loop cache keyed without domain |

### 1.5 Adjacent services

- `backend/app/services/forecast_page.py` — meteogram `region` threading at 3097, 3144, 3182, 3257–3275, 3320–3330, 3370, 3387–3402 (all terminate in discarding resolvers); hardcoded `region="conus"` at 1359 and 1381 (MRMS recent-precip — should route through the canonical-domain helper).
- `backend/app/services/admin_telemetry.py` — `_published_run_ids` 639–652, `_sidecar_path` 654–655, `_manifest_path` ~795, run scans 1382+, grid-manifest checks 1602/1660–1662: canonical-path only.
- `backend/app/services/builder/pipeline.py` — **the write-side collision**: `staging_dir = data_root / "staging" / model / run_id / var_key` (1514). Region used for fetch coverage (1437), plugin validation (1456), sidecar payload (944–945) — never the path.
- `backend/app/services/builder/raster_grid.py` — `REGION_BBOX_3857` (45–49) has `conus|na|pnw`; `get_grid_params` (147–169) prefers `ModelCapabilities.grid_meters_by_region`. A `global` region needs a bbox + grid meters in Phase 3 (EPSG:3857 needs ±85.05° clipping — Phase 3's problem).
- Observed publishers (`goes_publish.py:126,177`, `rtma_ru_publish.py:91`, `mrms_publish.py:196`, `publish_utils.py:36–43`) write `published/{model}/LATEST.json` directly — canonical-only, untouched under the locked layout.
- `backend/app/models/base.py` — `VariableCapability.supported_build_regions` (63) exists, currently write-only (serialized at `serialization.py:91–115`); `ModelCapabilities.canonical_region` (118), `grid_meters_by_region` (119).

### 1.6 What collides if a global run were published today

Same model/run/var, canonical + global: identical staging var dirs (`pipeline.py:1514`), published var dirs, grid dirs and `grid/manifest.json`, frame binaries and meta sidecars, numeric sidecars `fh*.json`, run manifest (last writer wins), `published/{model}/LATEST.json`, version tokens/ETags, sample caches, loop cache, frames-404 telemetry, admin status. A global publish would silently corrupt the canonical run in place.

---

## 2. Locked physical directory layout — Option B, parallel domain run tree

The namespace literal is `domains/`, chosen because it can never match `RUN_ID_RE` — every existing scanner already skips it with zero code changes: run retention (`scheduler.py:1521–1523`), manifest retention (files-only, 1547), member backfill (2901–2903), API run scans (`main.py:2862–2868`), admin status (`admin_telemetry.py:643`). The domain module must reserve the word (no region may be named `domains`; no region id may parse as a run id).

Canonical (unchanged, byte-for-byte — today's literal paths):

```
{data_root}/staging/{model}/{run}/{runtime_var}/fh{fh:03d}.json
{data_root}/staging/{model}/{run}/{runtime_var}/grid/fh{fh:03d}.l{L}.u16.bin
{data_root}/published/{model}/{run}/{runtime_var}/grid/manifest.json
{data_root}/published/{model}/LATEST.json
{data_root}/manifests/{model}/{run}.json
URL: /api/v4/grid/{model}/{run}/{runtime_var}/{filename}?v={run}-{var}-{mtime_ns}
```

Non-canonical domain `d` (new namespace, mirror-symmetric):

```
{data_root}/staging/{model}/domains/{d}/{run}/{runtime_var}/...           (same inner shape)
{data_root}/published/{model}/domains/{d}/{run}/{runtime_var}/grid/manifest.json
{data_root}/published/{model}/domains/{d}/LATEST.json
{data_root}/manifests/{model}/domains/{d}/{run}.json
```

Non-canonical edge-served artifact URLs (locked decision #5 — `domains/{d}` inserted immediately before `{model}`; canonical routes at `main.py:5728, 5740, 6094, 6168` byte-identical and untouched):

```
/api/v4/grid/domains/{d}/{model}/{run}/{var}/{filename}?v={run}-{var}-{mtime_ns}
/api/v4/grid/v1/domains/{d}/{model}/{run}/{var}/{filename}?v=...
/api/v4/domains/{d}/{model}/{run}/{var}/{fh}/contours/{key}
/api/v4/domains/{d}/{model}/{run}/{var}/{fh}/vectors/{key}
```

Implementation notes: declare the `domains/`-prefixed routes **before** the parameterized canonical routes so FastAPI cannot bind `model="domains"`; reserve `domains` as a model id as well as a region id. X-Accel-Redirect keeps working because domain files remain under `PUBLISHED_ROOT` — no nginx change for serving; the new path shapes need nginx/CF rule coverage when Phase 3 goes live (existing G2 checklist item).

Option A (domain subtree inside the shared run dir, `{run}/{domain}/{var}`) was **rejected**: retention crosses domains by construction (evicting a canonical run deletes its global artifacts), and every canonical progress-publish would `copytree`-walk the global subtree (~1.2–1.4 TiB at 9 km per the sizing spike), degrading canonical promote latency.

---

## 3. Promotion atomicity

`_promote_run(data_root, model, run, *, domain)` computes `published_model = data_root/"published"/model` for the canonical domain (today's code path, untouched) or `data_root/"published"/model/"domains"/{d}` otherwise. The `.{run}.tmp` build dir, `.{run}.trash` dir, and final run dir are all siblings inside that one directory (`scheduler.py:1305–1353`), so the two-rename swap is unchanged and each `os.rename` stays within a single directory — atomic on POSIX. Staging→tmp hardlink copies (`_copy_or_link_file`, 1059–1071) stay on the same mount; production pins the entire data root to `/dev/vda4` (plan line 55). An inode-identity test proves the hardlink path executed.

---

## 4. Domain type design and API threading

### 4.1 The type

Not an enum. A domain is an open, normalized string ID — a *published build-region ID* — whose universe is defined per model by capabilities. Canonical IDs in production: `na` (GFS, GEFS, AIFS; aigfs/ecmwf/eps per `TARGET_GRID_METERS`), `conus` (HRRR, NAM, NBM, WPC, CPC), observed products' own IDs (`rtma_ru.py:216`, `goes_east.py:269`, MRMS).

New leaf module `backend/app/services/domains.py` (import-light; no cycles):

```python
DomainId = str  # normalized: strip().lower(); reserved: "domains"; must not match RUN_ID_RE

def canonical_domain(plugin_or_capabilities) -> DomainId
    # str(capabilities.canonical_region).strip().lower() or "conus"
    # single authority replacing scheduler._default_build_region (352–355),
    # main._model_canonical_region (3196–3200), and the dead
    # main._normalized_request_region / _canonical_region_for_model (2734–2744)

def normalize_domain(model_id: str, domain: str | None) -> DomainId
    # None / "" -> canonical_domain(model); "<canonical id>" -> canonical id.
    # THE chokepoint making "absent domain= resolves exactly as today" true,
    # and ?domain=na on GFS identical to no domain at all.

def is_canonical(model_id: str, domain: DomainId) -> bool

def declared_domains_for_var(plugin, var_key) -> tuple[DomainId, ...]
    # canonical first, then VariableCapability.supported_build_regions entries
    # filtered by plugin.get_region(r) is not None and grid-params availability.

def validate_requested_domain(model_id: str, domain: str | None) -> DomainId | None
    # None result -> API returns the existing 404 bodies.

def domain_scoped_model_root(root: Path, model: str, domain: DomainId, *, canonical: DomainId) -> Path
    # root/model                      if domain == canonical  (byte-for-byte today)
    # root/model/"domains"/domain     otherwise
```

**Activation switch is capability data, not code**: 2A ships with no model declaring extra `supported_build_regions`, so `_build_regions_for_var` still returns exactly `[canonical]` and every path resolves to today's literals. Phase 3 turns global on for GFS via a `global` `RegionSpec`, a `REGION_BBOX_3857`/`grid_meters_by_region` entry, and `supported_build_regions=["global"]` on chosen variables.

### 4.2 Scheduler threading

- `_build_regions_for_var` → `declared_domains_for_var(plugin, var_id)`, canonical first. `BuildTarget` already carries region; no target-shape change. Must preserve the existing fail-closed behavior: raise `SchedulerConfigError` when the plugin does not define its canonical region (`scheduler.py:359–364`).
- Path layer honors it: `_frame_sidecar_path`/`_frame_primary_artifact_path`, `pipeline.py:1514` staging dir, `_latest_pointer_path`, `_manifest_path`, `grid.grid_dir` — all via `domain_scoped_model_root`.
- `_promotion_ready_regions` becomes genuinely per-domain (probed paths now differ). **Publish gating (review blockers B1/B2 — mandatory):**
  - `_should_promote`, `_promote_run(canonical)`, and the canonical LATEST write are gated on **`canonical_domain in ready_domains` specifically**, never on `bool(ready_domains)`. Today `_should_promote` (`scheduler.py:1160`) is `bool(_promotion_ready_regions(...))`, which is safe only while all regions probe identical paths; once probing is per-domain, an only-global-ready state must not trigger a canonical promote of an unready run or publish a canonical LATEST pointing at one.
  - `_publish_run_snapshot` publishes **canonical first**, then each non-canonical domain **independently wrapped in its own try/except** that logs and continues. A non-canonical failure (realistic case: ENOSPC on a large global tree) must not abort canonical publication, the catch-up loop, or the retention tail (`scheduler.py:2583–2592`). Call sites at 2183/2562/2570/2576 are unguarded today.
  - Non-canonical domains promote only on their own readiness.
- `_write_run_manifest` **filters `targets` by domain** (review M3): today `scheduler.py:1420–1428` discards `_target_region`, so a domain manifest would inherit the union of all domains' expected variables and never read complete. The domain manifest's `expected_frames`/vars cover only the declaring subset.
- `_normalized_publish_region` (`scheduler.py:1044–1046`) is retired or re-based on `domains.normalize_domain` (review M5): its `CANONICAL_COVERAGE = "conus"` fallback would write `"region": "conus"` payloads for models whose canonical domain is `na`.
- Retention tail: for each domain any built var declares, run `_enforce_run_retention`/`_enforce_manifest_retention` against the domain-scoped roots; existing canonical calls untouched (provably skip `domains/`).
- Member/stats/backfill passes: canonical-only in 2A. Loop pregeneration: canonical-only in 2A, explicit guard rejecting non-canonical domains (loop cache paths carry no domain segment yet).

### 4.3 API threading

- Add `domain: str | None = Query(None)` to the control/selection APIs: `/api/v4/{model}/runs`, `/{run}/manifest`, `/{run}/vars`, `/{run}/{var}/frames`, `/grid-manifest`; optional `domain` fields on `SampleBatchIn` (2600) and `MeteogramRequestIn` (4036); optional on `/sample` and `/bootstrap`.
- Edge-served artifact routes get **new `domains/`-prefixed path variants** (locked decision #5, exact shapes in §2): both grid-file routes (`main.py:5728, 5740`), `/contours/{key}` (`main.py:6094`), and `/vectors/{key}` (`main.py:6168`) — the contour/vector routes were missing from the original threading list (review M2; wrong-domain vectors would otherwise edge-cache for 24h under the nginx `s-maxage=86400` override). Canonical routes untouched.
- **Do not reinterpret `region=`.** Existing `region=` query params are viewport-preset IDs and remain accepted-and-ignored by data resolution; Phase 2B formalizes the split. **In particular (review B3): `_bootstrap_selection_state` (`main.py:3262–3276`) currently threads its viewport preset (`region=midwest` etc.) into `_resolve_run`, `_resolve_latest_run`, `_load_manifest`, `_run_version_token`, and `_published_var_dir`. It must stop passing the preset into any data resolver (pass `domain=None` unless an explicit `domain=` was supplied) and the local is renamed `selected_viewport_preset`. A mechanical `region→domain` rename here would blank bootstrap for every regional permalink.**
- The dead `region: str | None = None` kwargs on resolver helpers are renamed to `domain: str | None = None` and **implemented**: normalize once at the route boundary, thread the normalized ID down. Full list: `_resolve_run`, `_resolve_latest_run`, `_latest_run_from_pointer_for_region`, `_load_manifest`, `_manifest_path`, `_latest_pointer_path`, `_scan_manifest_runs`, `_published_var_dir`, `_load_grid_manifest`, `_grid_file_url`, `_grid_manifest_frame_file_is_valid`, `_resolve_frame_var_dir`, `_run_version_token`, `_grid_version_token`, `_seconds_since_publish`, all `sampling.py` resolvers, the `forecast_page.py` threading chain.
- **The rename is not mechanical** (review M4). These helpers drop the argument internally when calling their own dependencies; each must forward the domain, not just accept it:

  | Site | Internal drop |
  |---|---|
  | `main.py:2775–2776` | `_latest_run_from_pointer_for_region` validates against `PUBLISHED_ROOT / model / run_id` and `_manifest_path(model, run_id)` without forwarding |
  | `main.py:2817–2818` | same pattern in the RGB pointer path |
  | `main.py:2868` | `_scan_manifest_runs` cross-checks `(PUBLISHED_ROOT / model / run_id).is_dir()` — a domain scan must re-root **both** the manifest dir and the published cross-check |
  | `main.py:3400` | `_resolve_run`'s `run == "latest"` branch calls `_resolve_latest_run(model)` with no domain |
  | `main.py:3405–3406` | `_resolve_run` composes `PUBLISHED_ROOT / model / run` and `_manifest_path(model, run)` directly |
  | `sampling.py:516–518` | `_resolve_sidecar` calls `_main._resolve_run` / `_published_var_dir` without forwarding |
  | `sampling.py:545–547` | `_resolve_binary_grid_frame` — same; this is the resolver behind `/sample`, `/sample/batch`, `has_cog`, and all meteogram sampling |

- Cache keys: `_sample_cache_key`/`_sample_batch_cache_key` gain the normalized domain component (canonical requests may keep the legacy key shape to avoid a cold-cache blip — implementation's choice).
- Unknown domain → existing 404 bodies (`run not found`; pinned `val.cog.tif not found` sample body). `_emit_frames_404` gains a `domain` attribute (locked decision #4); the `reason="stale_run"` call at `main.py:5640–5643` must pass it like its five siblings.
- `_grid_file_url` emits the `domains/{d}` path prefix only for non-canonical domains; canonical output byte-identical.

---

## 5. Test list

**A. Canonical byte-for-byte preservation**
1. Golden path-literal test: every path helper in §1, `domain=None` **and** `domain=<canonical id>` return the exact hardcoded pre-change strings (assert literals, not helper composition), parametrized over every model in `MODEL_REGISTRY`.
2. API URL snapshot: `/frames` and `/grid-manifest` over a canonical fixture — `url` fields byte-identical (`?v={run}-{var}-{mtime}`, no `domain=`).
3. `normalize_domain(m, None) == normalize_domain(m, canonical_id) == canonical_id` for all models; `"domains"` and run-id-shaped strings rejected.
4. Scheduler one-shot on a fixture model with no declared extra domains: file tree, manifest payload, LATEST payload identical (minus timestamps) to a pre-change golden listing.

**B. Coexistence without collision**
5. Synthetic canonical + `global` fixtures, same model/run/var: all artifact paths distinct; canonical files hash-identical before vs after global publish.
6. `_build_regions_for_var` with a test plugin declaring `supported_build_regions=["global"]` (+ `global` RegionSpec) returns `[canonical, "global"]`; without, exactly `[canonical]`.
7. `build_grid_manifests_for_run_root` over the domain staging root writes manifests only under the domain tree.

**C. LATEST / manifests / retention / pruning cannot cross domains**
8. LATEST isolation: only canonical LATEST → `resolve(domain="global")` returns None (never falls back); global LATEST present → canonical resolution unaffected.
9. `_scan_manifest_runs(model, domain="global")` sees only `manifests/{model}/domains/global/`; canonical scan unchanged when domain manifests exist.
10. Retention: canonical retention never removes anything under `domains/` (and vice versa); `manifests/{model}/domains/` survives canonical manifest retention.
11. Member backfill scan, `_enforce_ensemble_stats_health_retention`, admin `_published_run_ids` neither crash on nor list `domains/`.
12. Sampling isolation: different values per domain — `_resolve_binary_grid_frame(domain="global")` returns global; `domain=None` returns canonical; `_sample_cache_key` differs across domains.
13. `/frames`, `/grid-manifest`, grid-file routes with `domain=global` serve only global artifacts; absent `domain=` serves only canonical, even with both present. Global grid file produces X-Accel-Redirect under the published root.

**D. Promotion atomicity**
14. `_promote_run(domain="global")`: tmp/trash/final siblings under `published/{model}/domains/global/`; monkeypatched `os.rename` failure restores previous published run.
15. Inode-identity: staged and published frame files share `st_ino` after promote.
16. Swap-window: canonical published run dir remains stat-able throughout a concurrent global promote.

**E. Absent `domain=` resolves exactly as today**
17. Meteogram without `domain`, both domains published with distinguishable values → sampled values from canonical frames.
18. Permalink regression: legacy `region=midwest`/`pnw`/`na` on `/frames`, `/sample`, grid-file routes behave identically to no-param requests.
19. `domain=<unknown>` → existing 404 bodies verbatim (including pinned sample-endpoint body).

**F. Review-mandated additions (2026-07-28)**
20. `/bootstrap?region=midwest` returns a byte-identical payload to `/bootstrap` with no region (both domains published with distinguishable values).
21. Only-global-ready: canonical LATEST unchanged, no canonical promote attempted (`_should_promote` false for canonical).
22. Monkeypatched global-promote raise → canonical run still published, retention tail still executed.
23. Domain contour and vector routes serve only that domain's artifacts; canonical contour/vector routes with a `domain=` query (if supplied) 404 rather than falling back cross-domain.
24. Domain run manifest lists only the declaring variable subset (`expected_frames` filtered by target region).
25. `_scan_manifest_runs(domain=…)` re-roots both the manifest dir **and** the published-tree cross-check.
26. Two-domain distinct-body test per artifact family (grid binary, contour, vector): URLs differ structurally, bodies differ, no fallback across domains; plus Cloudflare cold `MISS` → identical-second-request `HIT` recorded per family at Phase 3 rollout (G2).
27. frames-404 telemetry persists `domain` end-to-end: `record_frames_404` parameter, `_recent` field, admin section surfaces it; the `stale_run` call site passes it.
28. Reserved-word enforcement against **every** `RegionSpec` id in every plugin (not just canonical ids): no region id equals `domains` or matches `RUN_ID_RE`.
29. `/rgb-manifest` and `/api/v4/rgb/…` (`main.py:5380, 5461`) asserted canonical-only: domain requests rejected, canonical behavior unchanged.
30. Golden-path test asserts canonical grid/contour/vector URLs contain neither `domains/` segment nor `domain=`.

---

## 6. Cleanup in passing

- `_normalized_request_region`/`_canonical_region_for_model` (`main.py:2734–2744`) are dead; fold into `domains.py` or delete.
- `forecast_page.py:1359,1381` hardcoded `region="conus"` routes through the canonical-domain helper.

---

## 7. Adversarial review record — 2026-07-28

Fresh-context adversarial review (Opus) attacked all six constraints against source. Verdict: **approve with amendments**; the Option B layout and `domains/` reserved literal survived intact. Confirmed holding: promotion atomicity; no hardcoded union; every published-tree scanner filters on `RUN_ID_RE` (`scheduler.py:1521, 1547, 1577, 2901`, `admin_telemetry.py:644`, `main.py:2864`, `publish_utils.py:168`); no unfiltered `rmtree` of model dirs anywhere; frames ETag unreachable in prod (nginx `proxy_hide_header ETag` + `no-store`).

Amendments incorporated above: B1 canonical-readiness gating (§4.2), B2 per-domain failure isolation (§4.2), B3 bootstrap viewport-preset trap (§4.3), M1 domain-in-path artifact URLs (decision #5, §2), M2 contour/vector routes (§4.3), M3 manifest target filtering (§4.2), M4 internal-drop table (§4.3), M5 `_normalized_publish_region` retirement (§4.2), minor telemetry/fail-closed items, tests 20–30 (§5F).
