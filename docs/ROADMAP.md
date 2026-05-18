CartoSky (CSKY) - 12 Month Development Roadmap

This roadmap is designed for a solo developer moving quickly with AI assistance. It prioritizes reliability, user trust, product differentiation, and eventual monetization readiness without assuming a larger team or long parallel workstreams.

This revision reflects the current codebase rather than planning from a blank slate.

Current baseline already in the repo:
- First-party telemetry pipeline exists in [frontend/src/lib/telemetry.ts](../frontend/src/lib/telemetry.ts), [backend/app/main.py](../backend/app/main.py), and [backend/app/services/admin_telemetry.py](../backend/app/services/admin_telemetry.py).
- Viewer performance work already has a detailed implementation plan in [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md).
- Sharing flows already exist in [frontend/src/components/twf-share-modal.tsx](../frontend/src/components/twf-share-modal.tsx) and backend share routes.
- Point sampling and anchor batch plumbing already exist in [frontend/src/App.tsx](../frontend/src/App.tsx).
- Model capability expansion is already being pushed toward a metadata-driven architecture in [docs/MODEL_AGNOSTIC_PLAN.md](./MODEL_AGNOSTIC_PLAN.md).

Guiding principles:
- Ship visible improvements frequently.
- Prioritize speed, trust, and freshness over raw feature count.
- Focus on HRRR / NAM / GFS experience first.
- Build sharing and discovery early, but not ahead of stability.
- Defer hard monetization until usage data is trustworthy.
- Prefer feature flags and phased rollouts for anything operationally risky.

Cross-cutting product requirements:
- Every quarter should improve both user-facing value and operational resilience.
- New UX features should ship with telemetry, rollback paths, and at least a lightweight QA checklist.
- Public beta should be gated by performance, freshness, and mobile usability rather than calendar date alone.
- Storm-time traffic and export-heavy features should include cost and abuse guardrails before broad rollout.

---

Recommended Observability Stack

Use a layered stack rather than forcing one system to do every job.

1. Custom first-party telemetry for product and weather-domain behavior
- Keep the existing event pipeline in [frontend/src/lib/telemetry.ts](../frontend/src/lib/telemetry.ts), [backend/app/main.py](../backend/app/main.py), and [backend/app/services/admin_telemetry.py](../backend/app/services/admin_telemetry.py) as the source of truth for viewer performance and domain-specific usage.
- This is the right place for metrics such as `viewer_first_frame`, `scrub_latency`, `variable_switch`, `frame_scrub`, `share_action`, `screenshot_export`, and weather-model-specific interaction metadata.
- Reason: these events depend on app-specific semantics and should stay under your control.

2. Prometheus + Grafana for operational metrics
- Use Prometheus + Grafana for service and infrastructure visibility:
  - API latency and error rates
  - scheduler health
  - background job success and failure rates
  - share upload failure rates
  - system resource pressure
  - storage and cache behavior
- Reason: this is the right tooling for alerting, dashboards, uptime trends, and service health.

3. Optional external product analytics
- If beta traffic becomes meaningful and you want funnels, retention, and product analysis beyond the custom dashboards, add PostHog.
- Use Plausible only if the primary need is lightweight traffic analytics and acquisition visibility.
- Do not adopt both PostHog and Plausible at the same stage unless there is a specific marketing requirement.

Recommended near-term choice
- Now: custom telemetry + Prometheus/Grafana.
- Later, if beta usage justifies it: add PostHog.

---

Quarterly Milestones + Gates

Q2 2026 - Performance, observability, and beta launch window
- Primary outcome: move from active development into an early or mid-summer 2026 public beta.
- Focus months: Month 1, Month 2, Month 3.
- Gate to open public beta:
  - Phase 0a through Phase 3 priorities from [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md) are complete or intentionally deferred with known tradeoffs.
  - Viewer interaction metrics are stable enough to detect regressions.
  - Users can tell whether a run is fresh, stale, or incomplete.
  - Mobile layout is good enough that beta traffic will not immediately hit trust-breaking friction.
  - Sharing and permalink restore work reliably enough to support organic discovery.
- Target beta window: late June through mid July 2026, depending on gate status.

Q3 2026 - Sharing and local forecast utility
- Primary outcome: improve shareability and deepen product usefulness after beta traffic begins.
- Focus months: Month 4, Month 5, Month 6.
- Gate to move beyond local utility work:
  - Share export and share links are reliable and measurable.
  - Point forecast usage demonstrates real adoption or the UX is trimmed back.
  - Added product depth does not materially regress core viewer performance.

Q4 2026 - Differentiation release
- Primary outcome: ship comparison mode deliberately, not as an architectural guess.
- Focus months: Month 7, Month 8, Month 9.
- Gate to broaden promotion of differentiator features:
  - Comparison compatibility rules are explicit.
  - Comparison mode has acceptable performance on supported layouts.
  - Export-heavy features have guardrails for cost, failure handling, and queueing.

Q1 2027 - Growth, advanced analysis, and monetization prep
- Primary outcome: turn observed usage into durable product strategy.
- Focus months: Month 10, Month 11, Month 12.
- Gate to call the platform monetization-ready:
  - Growth features are backed by actual usage signals.
  - At least one advanced analytical feature exists and is understandable to users.
  - Tier metadata and backend route structure support future gating without a major rewrite.

---

Q2 2026 Beta Launch Checklist

This is the concrete checklist for deciding whether the early or mid-summer 2026 beta opens.

Performance gate
- Phase 0a and 0b instrumentation from [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md) are in place.
- Phase 1 scrub redesign is complete or deferred with known risk accepted.
- Phase 2 anti-flash variable switching is complete or deferred with known risk accepted.
- Phase 3 first viewer frame work is complete enough that cold-start behavior is not obviously broken.
- Admin telemetry can show whether viewer performance is improving or regressing.

Operational gate
- Ingestion status can be reviewed without filesystem spelunking.
- Stale runs, missing artifacts, and unreadable artifact failures are visible in operational reporting.
- The team can answer whether the current latest run is healthy for core models.
- Basic alerting or at least a repeatable daily health review exists.

Trust UX gate
- Users can see last-updated or freshness information clearly.
- Stale-run and degraded-state messaging exist for the most important failure cases.
- Error and loading states are acceptable on desktop and mobile.
- Permalink restore is reliable enough that a shared link does not feel broken.

Sharing gate
- Screenshot export works reliably on the main happy path.
- Share links restore exact or near-exact viewer state.
- Share failure rate is measurable.
- The share flow is simple enough that first-time users can complete it without explanation.

Mobile gate
- Core viewer controls remain usable on small screens.
- Scrubbing, variable switching, and region switching are not obviously frustrating on mobile.
- No major layout breakages exist in the share flow or point-sampling entry points.

Product analytics gate
- Usage events are captured for the most important viewer actions.
- You can answer which models, variables, and interaction paths are most used during beta.
- Beta feedback can be tied back to measurable behavior where possible.

Launch decision rule
- If performance, trust UX, and operational gates are green, launch beta even if growth and advanced analytics are still light.
- If the viewer is still visibly unstable or freshness is ambiguous, slip the beta by a few weeks rather than launch into distrust.

---

Month 1 - Viewer Stability + Performance

Goals
- Make the viewer reliably fast on common paths.
- Reduce visible stutter during frame changes, variable switches, and loop playback.
- Finish the highest-value performance work first while recent implementation context is still fresh.

Tasks
- Use [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md) as the execution plan for this month.
- Prioritize the implementation plan in this order:
  - Phase 0a and 0b instrumentation
  - Phase 1 scrub redesign and request prioritization
  - Phase 2 anti-flash variable switching
  - Phase 3 first viewer frame reduction
- Improve loading and error states while performance changes are underway.
- Fix obvious mobile layout regressions discovered during performance work.
- Review sample request behavior in [frontend/src/App.tsx](../frontend/src/App.tsx) so batch sampling and scrub interactions do not create avoidable request churn.

Target Performance
- `frame_change` p95 < 250 ms
- `loop_start` p95 < 1 second
- `variable_switch` p95 materially reduced toward the targets in the rendering plan
- Scrubbing feels responsive on a warm path

Codebase touchpoints
- [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md)
- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/components/map-canvas.tsx](../frontend/src/components/map-canvas.tsx)
- [frontend/src/components/bottom-forecast-controls.tsx](../frontend/src/components/bottom-forecast-controls.tsx)

Done Criteria
- Viewer feels faster than typical static-map weather sites.
- No obvious UI flash during variable switches.
- No obvious stutter during normal loop playback.
- Performance regressions are measurable in admin telemetry rather than anecdotal.

---

Month 2 - Telemetry Expansion + Operational Observability

Goals
- Understand how the site is actually used.
- Improve visibility into pipeline failures and viewer bottlenecks.
- Turn existing telemetry plumbing into decision-ready reporting.

Tasks
- Expand usage event coverage on top of the existing telemetry pipeline in [frontend/src/lib/telemetry.ts](../frontend/src/lib/telemetry.ts).
- Add missing usage events:
  - `frame_scrub`
  - `screenshot_export`
  - `share_action`
  - `point_sample`
  - `permalink_open`
- Extend backend usage summaries beyond raw event counts in [backend/app/services/admin_telemetry.py](../backend/app/services/admin_telemetry.py) so you can answer questions by model, variable, region, and device type.
- Keep first-party telemetry as the source of truth for product and weather-domain behavior.
- If you want external user analytics, prefer one product analytics tool rather than two:
  - choose PostHog if you want event funnels, retention, and product analysis
  - choose Plausible only if you mainly want lightweight traffic analytics
- For infrastructure and service metrics, prefer Prometheus + Grafana rather than extending the custom telemetry tables for everything.
- Build basic dashboards for:
  - ingestion status
  - scheduler timing
  - artifact validation failures
  - viewer interaction volume
  - share uploads and screenshot exports
- Add basic alerting or at least a daily operational review pass for stale runs, missing artifacts, and upload failures.

Codebase touchpoints
- [frontend/src/lib/telemetry.ts](../frontend/src/lib/telemetry.ts)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/services/admin_telemetry.py](../backend/app/services/admin_telemetry.py)
- [frontend/src/pages/admin/usage.tsx](../frontend/src/pages/admin/usage.tsx)
- [frontend/src/pages/admin/performance.tsx](../frontend/src/pages/admin/performance.tsx)

Done Criteria
- You can answer which variables are most used by actual selection volume, not just total event count.
- You can answer which models drive the most viewer interaction.
- You can answer how often screenshots and shares are used.
- You can identify stale-run and artifact-failure incidents without manual digging.

---

Month 3 - Beta Readiness + Trust UX

Goals
- Prepare for public beta without overcommitting to marketing before the product is trustworthy.
- Improve user confidence in run freshness and current system state.

Tasks
- Add lightweight feedback collection.
- Improve homepage messaging and value proposition.
- Improve Models and Variables pages.
- Add clearer freshness and availability cues:
  - last updated time
  - stale-run messaging
  - partial-run or degraded-state messaging where needed
- Define beta launch gates around:
  - viewer performance
  - mobile usability
  - share reliability
  - run freshness visibility

Done Criteria
- Public beta can be announced without known trust-breaking issues.
- Users can tell whether a run is fresh, stale, or incomplete.
- Feedback is being collected in a form that can drive prioritization.

---

Month 4 - Sharing Optimization

Goals
- Make sharing fast and reliable.
- Improve conversion from "interesting weather view" to "shared link or image."

Tasks
- Simplify the screenshot and share flow in [frontend/src/components/twf-share-modal.tsx](../frontend/src/components/twf-share-modal.tsx).
- Add one-click copy link.
- Standardize screenshot attribution footer.
- Ensure permalink restore continues to round-trip exact viewer state.
- Add telemetry for share flow drop-off, upload failures, and export duration.
- Review share media retention, naming, and error handling before promoting sharing more aggressively.

Example footer:

TheWeatherModels.com
HRRR | Reflectivity | 18z | Hour 24

Done Criteria
- Screenshot export takes < 3 seconds on a normal path.
- Share links restore exact viewer state.
- Share failures are measurable and debuggable.

---

Month 5 - Point Forecast MVP

Goals
- Ship a high-utility local forecast interaction that builds on existing sampling infrastructure.

Tasks
- Turn existing point sampling and anchor plumbing in [frontend/src/App.tsx](../frontend/src/App.tsx) into a visible point forecast workflow.
- Let users click the map to pin a location.
- Display current variable value at the point.
- Add a lightweight forecast-hour timeline for the selected point.
- Keep the first version constrained to one pinned point and core variables.

Example panel:

Location: Sioux Falls
Temp
Wind
Precip
Snowfall

Done Criteria
- Users can click anywhere and get useful local forecast values.
- The point tool feels reliable enough to use during active weather.
- The point workflow does not noticeably degrade viewer performance.

---

Month 6 - Point Tool Polish

Goals
- Make point tools feel native to the viewer rather than bolted on.

Tasks
- Allow multiple pinned points.
- Improve anchor label UX.
- Add better unit display and compact comparison formatting.
- Add permalink support for active point state if the UX remains understandable.
- Add usage telemetry so you can tell whether the point tool is actually valuable.

Done Criteria
- Point tools feel like a natural part of the product.
- Multi-point state is understandable on desktop and mobile.

---

Month 7 - Comparison Mode Groundwork

Goals
- De-risk comparison mode before shipping the full slider experience.

Tasks
- Define model and variable compatibility rules.
- Decide how forecast-hour locking behaves across mismatched model horizons.
- Validate shared extent, legend, and render assumptions.
- Extend permalink state shape for two-panel or comparison state.
- Identify performance and cache implications before the full UI lands.

Codebase dependencies
- [docs/MODEL_AGNOSTIC_PLAN.md](./MODEL_AGNOSTIC_PLAN.md)
- [docs/RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md](./RENDERING_PERFORMANCE_IMPLEMENTATION_PLAN.md)

Done Criteria
- There is a clear compatibility matrix.
- Comparison mode no longer depends on unresolved architecture questions.
- The implementation scope is predictable enough for a solo developer.

---

Month 8 - Model Comparison MVP

Goals
- Ship the first major differentiator, but only after groundwork is done.

Tasks
- Implement dual-map comparison mode.
- Implement swipe or slider UI.
- Lock forecast hour between compatible models.
- Support permalink state.
- Instrument comparison usage and performance from day one.

Done Criteria
- Users can visually compare HRRR vs NAM or NAM vs GFS quickly.
- Comparison mode is useful on both desktop and tablet-sized layouts.
- The initial rollout is stable enough to expand rather than rewrite.

---

Month 9 - Animated Export

Goals
- Enable shareable forecast loops without creating operational pain.

Tasks
- Export GIF loops.
- Export MP4 loops.
- Add social-media-ready presets.
- Decide whether encoding is synchronous, queued, or pre-generated for the first release.
- Add quotas, retention rules, and failure telemetry before broad rollout.

Done Criteria
- Users can export and share animated loops easily.
- Export failures, queue time, and storage costs are measurable.

---

Month 10 - Growth + Storm-Time UX

Goals
- Improve discoverability and usability during high-interest weather events.

Tasks
- Add popular variable indicators backed by actual telemetry.
- Improve run freshness indicators.
- Improve event-time UX so the viewer is easier to navigate under stress.
- Improve landing pages and model-variable discoverability if beta traffic suggests that acquisition is becoming a bottleneck.

Done Criteria
- Storm-time usage is easier to navigate.
- Growth features are backed by real usage data rather than guesswork.

---

Month 11 - Advanced Feature Prototype

Choose ONE:

Option A - Model Consensus Maps

Example:

Probability of 6+ inch snowfall

Option B - Event Detection

Example:

Heavy snow band detected

Selection guidance
- Choose the option that best matches actual user behavior from the prior ten months.
- Prefer the feature with the clearest operational path and the smallest explanation burden.

Done Criteria
- One advanced analytical feature exists.
- The feature has a clear story for both utility and future differentiation.

---

Month 12 - Monetization Readiness

Goals
- Prepare architecture for a future Pro tier without degrading the free product.

Tasks
- Add feature-tier metadata to the capability model and route structure.
- Identify high-value variables and workflows using actual usage data.
- Clean up backend route structure where tiering or future policy enforcement would otherwise be awkward.
- Ensure tiering can be introduced gradually through capability metadata rather than hardcoded frontend branching.

Example config:

variables:
  snowfall_kuchera:
    tier: pro
  precip_total:
    tier: free

Done Criteria
- A paywall could be enabled later without major redesign.
- Tier metadata fits the model-driven architecture rather than fighting it.

---

End of Year Targets

Product
- Stable public platform.
- One major differentiator feature.
- One advanced analysis feature.

Usage
- Measurable organic sharing.
- Clear feature usage data.
- Enough product telemetry to make prioritization evidence-based.

Technical
- Clean backend architecture.
- Strong observability.
- Mature viewer performance baselines tied to the rendering plan.

Business
- Clear path to a future Pro tier.
- Better understanding of which workflows users would actually pay for.

## RoadMap Condensed
**PHASE 1 - Immediate Value (retain + attract users)**
- [X] Refactor backend/frontend pipeline to more performative option
- [X] Add radar
- [X] Add obs, NWS forecasts, AFDs for cities
- [X] Add SPC outlooks/products
- [X] Add NWS hazards per county
- [X] Add selected new core variables
- [X] Add Kuchera to NAM and GFS
- [X] Add selected new core models
- [X] Redesign frontend
- [ ] Add selected new secondary variables
- [X] Add groups/titles in dropdown
- [X] Expand to North America domain for certain variables
- [X] Add lightweight feedback collection/ability
- [X] Add Forecast page MVP (search + obs/forecast/AFDs/hazards)
- [ ] Beta polish and consistency pass

**LAUNCH PUBLIC BETA TO TWF**
* Can users check radar?
* Can users compare at least 2-3 models manually?
* Can users view current conditions + forecast?
* Does the app feel broken?

**PHASE 2 - Feedback-Driven Expansion**
- [ ] Top 3 user-requested additions from beta

**PHASE 3 - Stickiness**
- [ ] Comparison tools
- [ ] GIF/trends
- [ ] Model consensus/probabilities
- [ ] Add location search/favorites
- [ ] Expand to North America

**PHASE 4 - SCALE + UX**
- [ ] Add “storm/event” mode
- [ ] Add social sharing targets

**PHASE 5 - Platform + Monetization**
- [ ] Setup CSKY account system
- [ ] Lay groundwork for monetization (free/pro tiers)