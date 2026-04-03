# SPC Probabilistic Outlooks Implementation Plan

## Summary
This plan extends the existing SPC latest-only vector product so the viewer can switch between multiple SPC outlook products from the current SPC variable dropdown.

The repo-specific decision is:
1. Probabilistic SPC products will remain inside the existing `spc` model.
2. The current variable dropdown will become the SPC product selector when `SPC` is selected.
3. V1 will add probabilistic severe outlooks as sibling SPC variables, not as separate top-level models.
4. The viewer will continue to show one SPC product at a time.
5. Hover, legend, and labels will be driven directly from vector feature metadata, not grid sampling.

This is the cleanest fit for the current codebase because:
1. SPC is already implemented as a latest-only valid-time vector product.
2. The frontend already supports model-specific variable options in the existing toolbar.
3. The map already supports vector overlays and feature-driven hover text.
4. The publish flow already writes immutable per-frame sidecars and vector GeoJSON payloads.

## Product Scope

### Recommended V1 Products
Start with the three Day 1 and Day 2 severe probabilistic outlook products.

1. `convective`
   - Categorical severe outlook
   - already shipped
2. `tornado_prob`
   - probabilistic tornado outlook
3. `wind_prob`
   - probabilistic wind outlook
4. `hail_prob`
   - probabilistic hail outlook

### Why These Are The Best First Additions
1. They are vector polygon products and match the current SPC ingestion/rendering path.
2. They fit naturally in the same day-based slider pattern as categorical SPC.
3. They use legend and hover semantics that are easier than point/event products.
4. They add meaningful user value without introducing a new viewer architecture.

### Explicitly Out Of Scope For This Plan
1. Watches
2. Mesoscale discussions
3. Local storm reports
4. Fire weather outlooks
5. Day 4-8 severe probability products
6. Multi-layer SPC overlay comparisons

## UX Decisions Locked

### Selector Behavior
The current SPC variable dropdown should become the product selector.

When `SPC` is selected, the dropdown options should be:
1. `Categorical`
2. `Tornado`
3. `Wind`
4. `Hail`

These are short UI labels only. The full descriptive context belongs in the legend title, hover copy, and share/screenshot metadata.

### Viewer Model
1. `SPC` remains one model.
2. The product dropdown changes which SPC variable is active.
3. The day slider remains the primary time navigation control.
4. Only one SPC product is shown at a time.

### Legend Behavior
1. `Categorical` uses the current categorical legend.
2. Probability products use a product-specific title such as:
   - `Tornado Probability`
   - `Wind Probability`
   - `Hail Probability`
3. Legends display actual thresholds as user-facing labels such as `2%`, `5%`, `10%`, `15%`, `30%`, `45%`, `60%`.
4. If SPC significant areas are present, the legend must explicitly represent them rather than relying on hover text alone.

### Hover Behavior
Hover for probability products should remain vector-feature based.

Examples:
1. `15% Tornado Probability`
2. `30% Wind Probability`
3. `15% Hail Probability`
4. `Significant Tornado Area`

Raw internal codes must never be exposed to the user.

## Data Model Decisions

### Model Structure
`spc` remains one model with multiple variables.

Planned variable keys:
1. `convective`
2. `tornado_prob`
3. `wind_prob`
4. `hail_prob`

### Time Semantics
1. `spc` remains `latest_only = true`.
2. `spc` remains `time_axis_mode = "valid"`.
3. The day slider continues to represent ordered valid-day frames.
4. Product-specific issue and valid times continue to live in frame sidecars.

### Rendering Semantics
1. All probability products are vector-only in V1.
2. `supports_sampling = false` remains correct for all SPC products.
3. Hover data comes from rendered feature properties.
4. `render_substrates = ["vector"]` remains correct.

## Upstream Data Requirements

Before coding, confirm the exact SPC GIS layer mapping for each product.

For each planned product, verify:
1. FeatureServer layer ids for Day 1 and Day 2
2. property names for threshold labels and significance flags
3. issue time fields
4. valid time fields
5. whether threshold coding differs by day or layer
6. whether hatched significant areas are encoded as separate polygons, flags, or alternate labels

This is critical because the categorical SPC service already differed from initial assumptions.

### Verified Live Layer Mapping
The live SPC FeatureServer layer ids verified during implementation are:

1. Categorical
   - Day 1: `1`
   - Day 2: `9`
   - Day 3: `17`
2. Tornado probability
   - Day 1: `3`
   - Day 2: `11`
3. Wind probability
   - Day 1: `7`
   - Day 2: `15`
4. Hail probability
   - Day 1: `5`
   - Day 2: `13`

Observed live schema notes:
1. Probability threshold values are exposed in `label` as decimals such as `0.05` and `0.15`.
2. Human-readable text is exposed in `label2` such as `5% Wind Risk`.
3. Colors are already present upstream in `fill` and `stroke`.
4. Significant probability areas appear as `CIG1`-style labels in the probability layers.
5. Some layers may legitimately have zero features on a given day, which must not collapse the whole SPC bundle.

## Backend Plan

### Phase 1: Capability And Variable Catalog Expansion
Update `backend/app/models/spc.py`.

Required changes:
1. Add new `VarSpec` entries for probability products.
2. Add matching `VariableCapability` entries.
3. Use short display labels suitable for the toolbar.
4. Preserve `group = "Outlooks"` unless a better SPC-specific grouping is needed.
5. Set product-specific legend titles in capability metadata where useful.

Example desired UI-facing names:
1. `Categorical`
2. `Tornado`
3. `Wind`
4. `Hail`

### Phase 2: Publisher Generalization
Refactor `backend/app/services/spc_publish.py` so it can publish multiple SPC products without duplicating the whole categorical pipeline.

The main design change should be:
1. a product configuration table keyed by SPC variable id
2. product-specific day/layer mappings
3. product-specific normalization rules
4. product-specific legend construction
5. product-specific feature-label formatting

Recommended configuration shape per product:
1. variable id
2. display name
3. supported days
4. FeatureServer layer ids by day
5. threshold-to-style mapping
6. hover label formatting rules
7. legend title
8. significance handling rules

### Phase 3: Probability Feature Normalization
For each probability product, normalize upstream polygons into a stable feature schema.

Each normalized feature should include at least:
1. `risk_code` or `threshold_code`
2. `risk_label` or `threshold_label`
3. `hover_label`
4. `fill`
5. `fill_opacity`
6. `stroke`
7. `stroke_width`
8. `sort_rank`
9. `day_label`
10. `is_significant` if applicable

The key rule is that frontend hover and legend should not need SPC-source-specific logic beyond reading standardized properties.

### Phase 4: Bundle Layout
Keep the existing published layout.

Target shape:
1. `published/spc/<run_id>/convective/...`
2. `published/spc/<run_id>/tornado_prob/...`
3. `published/spc/<run_id>/wind_prob/...`
4. `published/spc/<run_id>/hail_prob/...`

Each variable keeps its own:
1. frame sidecars
2. vector GeoJSON files
3. manifest frame entries

This avoids inventing a special SPC bundle format.

### Phase 5: Poller Behavior
`backend/app/services/spc_poller.py` should publish all configured SPC variables inside one run.

Required behavior:
1. fetch all configured SPC products for the current latest cycle
2. publish all available SPC variables under the same bundle run id
3. fail product-by-product where possible instead of collapsing the whole cycle for one product
4. reflect partial availability honestly in manifests and status tooling

This should be a deliberate decision:
1. Either V1 requires all four SPC products before publishing
2. Or V1 allows partial product availability and only publishes the products that normalized successfully

Recommended choice:
Allow partial product availability, but log clearly and surface it in admin status.

## Frontend Plan

### Phase 1: SPC Dropdown Labels
Reuse the existing variable selector in `frontend/src/App.tsx` and `frontend/src/components/weather-toolbar.tsx`.

When model is `spc`:
1. map `convective` to `Categorical`
2. map `tornado_prob` to `Tornado`
3. map `wind_prob` to `Wind`
4. map `hail_prob` to `Hail`

No new toolbar control should be introduced in V1.

### Phase 2: Legend Presentation
Use the existing legend component with product-specific metadata.

Required behavior:
1. categorical products show categorical labels
2. probability products show percent thresholds
3. legend title changes with selected SPC product
4. significant or hatched areas get explicit legend entries

### Phase 3: Hover Presentation
Reuse the vector-feature hover path that was just added.

Required behavior:
1. read normalized `hover_label` from feature properties when available
2. fall back to a composed label from threshold and product name if needed
3. keep hover human-readable
4. keep hover consistent with the legend

Recommended hover text format:
1. `15% Tornado Probability`
2. `30% Wind Probability`
3. `15% Hail Probability`
4. `Significant Tornado Area`

### Phase 4: Day Support Rules
Probability products may not all support the same day range.

The UI must behave deterministically when a selected SPC product does not support the current day.

Recommended behavior:
1. if the current selected day exists for the new product, preserve it
2. if it does not exist, snap to the nearest supported day
3. disable unsupported days in the slider if the control supports per-day disabling
4. otherwise keep the slider selectable set limited to the available published frames for that variable

### Phase 5: Share And Screenshot Labels
Update product labeling so shares and screenshots reflect the selected SPC product clearly.

Required output examples:
1. `SPC Tornado Probability`
2. `SPC Wind Probability`
3. `SPC Hail Probability`

Do not let these exports fall back to the generic `SPC Convective Outlook` label when a probability product is selected.

## API Contract Considerations

The existing runtime contract should remain unchanged structurally.

Expected existing endpoints to continue working:
1. `/api/v4/spc/latest/manifest`
2. `/api/v4/spc/latest/<var>/frames`
3. `/api/v4/spc/<run>/<var>/<fh>/vectors/<key>`

The implementation should prefer richer sidecar metadata over new endpoint shapes.

Needed sidecar metadata for probability products:
1. `display_name`
2. `legend_title`
3. `legend_entries`
4. `valid_time`
5. `issue_time`
6. vector layer path metadata

## Testing Plan

### Backend Tests
Add or extend tests for:
1. SPC capability serialization includes new variables
2. normalization of tornado probability payloads
3. normalization of wind probability payloads
4. normalization of hail probability payloads
5. significance or hatched area handling
6. manifest publication across multiple SPC variables
7. frame API behavior for each new variable
8. partial-product publish behavior if V1 allows it

### Frontend Tests
Add targeted tests for:
1. SPC dropdown labels render as `Categorical`, `Tornado`, `Wind`, `Hail`
2. switching SPC product updates vector URL correctly
3. switching SPC product updates legend title and entries
4. hover shows normalized probability labels
5. unsupported day fallback behavior
6. share/screenshot summaries reflect the selected SPC product

### Manual Validation Checklist
1. Select `SPC` and confirm the dropdown shows short product labels.
2. Select `Categorical` and confirm existing SPC behavior is unchanged.
3. Select each probability product and confirm the map updates without basemap flash.
4. Confirm hover text matches the visible polygon category.
5. Confirm legend entries match SPC thresholds.
6. Confirm the day slider only offers supported days for the selected product.
7. Confirm mobile layout remains usable with the shortened labels.

## Operational Plan

### Rollout Strategy
1. ship backend support for new SPC variables
2. run a one-shot publish in staging or prod-like environment
3. verify manifests and frames for all SPC variables
4. ship frontend dropdown/legend/hover updates
5. republish SPC bundles on prod so all sidecars carry final metadata

### Deployment Caveat
Because SPC is latest-only and sidecar-driven, already-published bundles may not contain newly required legend or hover metadata.

That means the rollout is not complete until SPC is republished after the backend deployment.

## Risks And Mitigations

### Risk 1: Upstream SPC property schemas differ by product
Mitigation:
1. fetch live payloads for every planned product before coding
2. build product-specific normalization tables
3. add fixtures based on live payload semantics

### Risk 2: Significant areas are visually ambiguous
Mitigation:
1. normalize significance explicitly into feature properties
2. add a distinct legend entry
3. use a clear hover label for significant areas

### Risk 3: Partial availability confuses users
Mitigation:
1. keep unsupported products out of the dropdown if not published
2. keep unsupported days out of the available frame list
3. expose missing-product state in admin telemetry

### Risk 4: SPC dropdown labels lose context
Mitigation:
1. use short labels only inside the control
2. use full product names in legend, hover, share, and screenshot output

## Recommended Implementation Order
1. Confirm live SPC FeatureServer layer ids and payload schemas for tornado, wind, and hail probabilities.
2. Extend `backend/app/models/spc.py` with the new variables.
3. Refactor `backend/app/services/spc_publish.py` into product-config-driven publication.
4. Add backend tests for multi-product SPC publication.
5. Update the frontend SPC variable labels to `Categorical`, `Tornado`, `Wind`, `Hail`.
6. Wire product-specific legend and hover labels.
7. Validate day availability behavior.
8. Republish SPC bundles and verify runtime output.

## Done Checklist
1. `SPC` remains one model in the viewer.
2. The existing SPC dropdown shows `Categorical`, `Tornado`, `Wind`, and `Hail`.
3. Each SPC probability product publishes as a vector-only latest-only variable.
4. Hover text reflects product-specific probabilities directly from vector features.
5. Legends are product-specific and user-readable.
6. Unsupported days are handled cleanly.
7. Shares and screenshots use the selected SPC product name.
8. A prod republish updates live bundles with final metadata.