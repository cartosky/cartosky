import { describe, expect, it } from "vitest";

import {
  capabilityVarsForManifest,
  coverageBadgeLabel,
  coverageDegradedNote,
  coverageSegmentsForModel,
  coverageSelectionValue,
  coverageSelectionValueAgainstDefault,
  defaultDataDomainForSelection,
  domainRunProbeStatusForManifest,
  filterRegionOptionsForDataDomain,
  makeVariableOptions,
  normalizeCapabilityVarRows,
  normalizeRequestedDomain,
  normalizeRequestedDomainAgainstDefault,
  refineDataDomainForRun,
  resolveDataDomain,
  variableIdsMissingDomain,
  viewerVariableGroup,
} from "@/lib/app-utils";
import { domainRunProbeKey, resolveDomainRunProbeStatus } from "@/lib/use-domain-run-availability";
import { buildPermalinkSearch, viewerPermalinkStateFromSelection } from "@/lib/permalink";
import { readPermalink } from "@/lib/permalink-read";
import { ANALYTICS_EVENT_NAMES } from "@/lib/analytics-types";
import { ALLOWED_EVENT_NAMES } from "@/lib/posthog";
import type { CapabilityModel, RegionPreset } from "@/lib/api";

/**
 * Global go-live UI contract (docs/GLOBAL_GOLIVE_UI_DESIGN_2026-07-31.md).
 *
 * Everything the Coverage control renders is derived from capabilities by the
 * pure helpers exercised here — the React components are dumb consumers, so
 * these tests are the real contract for visibility, the degraded note, the
 * variable badges, and the coverage-gated World preset.
 *
 * The §7 block is the flip-readiness proof: the collapse rule and the boot
 * reader are run against BOTH the `null` default (today) and a `"global"`
 * default (post-flip), so the flip is provably a one-function change.
 */

const REGION_PRESETS: Record<string, RegionPreset> = {
  na: { label: "North America", bbox: [-178, 5, -25, 82], defaultCenter: [-101.5, 45], defaultZoom: 0.5 },
  conus: { label: "CONUS", bbox: [-134, 24, -60, 55], defaultCenter: [-96.25, 39.5], defaultZoom: 4 },
  midwest: { label: "Midwest", bbox: [-104, 36.5, -80, 49.5], defaultCenter: [-92, 43], defaultZoom: 5 },
  world: { label: "World", bbox: [-180, -85, 180, 85], defaultCenter: [-30, 25], defaultZoom: 1.3 },
};

/** A GFS-shaped model: canonical `na`, one global variable, one NA-only. */
const GLOBAL_MODEL = {
  canonical_region: "na",
  variables: {
    tmp2m: { display_name: "Surface Temp", supported_build_regions: ["na", "global"] },
    precip_7d_anom: { display_name: "7d Precip Anomaly", supported_build_regions: ["na"] },
  },
} as unknown as CapabilityModel;

/** An HRRR-shaped model: canonical only, no build-region declarations. */
const CANONICAL_ONLY_MODEL = {
  constraints: { canonical_region: "conus" },
  variables: {
    tmp2m: { display_name: "Surface Temp" },
    refc: { display_name: "Reflectivity", supported_build_regions: ["conus"] },
  },
} as unknown as CapabilityModel;

const SPC_MODEL = {
  variables: {
    tornado_prob: { display_name: "SPC Tornado Probability" },
    wind_prob: { display_name: "SPC Wind Probability" },
    hail_prob: { display_name: "SPC Hail Probability" },
    convective: { display_name: "SPC Convective Outlook" },
  },
} as unknown as CapabilityModel;

const HRRR_SEVERE_MODEL = {
  variables: {
    ltng: { display_name: "Lightning Flash Density" },
    sbcape: { display_name: "Surface-Based CAPE" },
    mlcape: { display_name: "Mixed-Layer CAPE" },
    mucape: { display_name: "Most-Unstable CAPE" },
  },
} as unknown as CapabilityModel;

const HRRR_AIR_QUALITY_MODEL = {
  variables: {
    vi_smoke: { display_name: "Vertically Integrated Smoke" },
    smoke_sfc: { display_name: "Near-Surface Smoke" },
    ltng: { display_name: "Lightning Flash Density" },
  },
} as unknown as CapabilityModel;

// ───────────────────────── §7 flip readiness ─────────────────────────────────

/**
 * The viewer's boot rule (App.tsx seed + bootstrap) and its permalink
 * write-back, reduced to the two lines that matter. Absent param → the
 * default; present param → verbatim; whatever ends up in state is serialized.
 */
function simulateBoot(search: string, defaultDomain: string | null) {
  const previous = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { location: { search } };
  try {
    const requested = readPermalink().domain ?? null;
    const domainState = requested ?? defaultDomain;
    return {
      domainState,
      search: buildPermalinkSearch({ model: "gfs", var: "tmp2m", domain: domainState || undefined }),
    };
  } finally {
    if (previous === undefined) {
      delete (globalThis as { window?: unknown }).window;
    } else {
      (globalThis as { window?: unknown }).window = previous;
    }
  }
}

describe("defaultDataDomainForSelection (design §7 single flip point)", () => {
  it("returns null (canonical) for every selection today", () => {
    expect(defaultDataDomainForSelection(null, null)).toBeNull();
    expect(defaultDataDomainForSelection(GLOBAL_MODEL, GLOBAL_MODEL.variables!.tmp2m)).toBeNull();
    expect(defaultDataDomainForSelection(CANONICAL_ONLY_MODEL, undefined)).toBeNull();
  });

  it("is what the model-facing wrappers consume", () => {
    // Both wrappers must agree with the injected-default core when handed the
    // current default — otherwise the flip would move only half the behavior.
    expect(normalizeRequestedDomain("global", GLOBAL_MODEL, GLOBAL_MODEL.variables!.tmp2m))
      .toBe(normalizeRequestedDomainAgainstDefault("global", "na", null));
    expect(coverageSelectionValue(null, GLOBAL_MODEL, GLOBAL_MODEL.variables!.tmp2m))
      .toBe(coverageSelectionValueAgainstDefault(null, "na", null));
  });
});

describe("§7 world A — default null (today): byte-identical serialization", () => {
  const DEFAULT = null;

  it("collapses an explicit canonical selection to no param", () => {
    expect(normalizeRequestedDomainAgainstDefault("na", "na", DEFAULT)).toBeNull();
    expect(normalizeRequestedDomainAgainstDefault("", "na", DEFAULT)).toBeNull();
    expect(normalizeRequestedDomainAgainstDefault(null, "na", DEFAULT)).toBeNull();
  });

  it("keeps an explicit global selection", () => {
    expect(normalizeRequestedDomainAgainstDefault("global", "na", DEFAULT)).toBe("global");
  });

  it("shows the canonical segment for an unset domain", () => {
    expect(coverageSelectionValueAgainstDefault(null, "na", DEFAULT)).toBe("na");
    expect(coverageSelectionValueAgainstDefault("global", "na", DEFAULT)).toBe("global");
  });

  it("reproduces today's exact URL matrix through boot + write-back", () => {
    expect(simulateBoot("?m=gfs&v=tmp2m", DEFAULT)).toEqual({
      domainState: null,
      search: "?m=gfs&v=tmp2m",
    });
    expect(simulateBoot("?m=gfs&v=tmp2m&domain=global", DEFAULT)).toEqual({
      domainState: "global",
      search: "?m=gfs&v=tmp2m&domain=global",
    });
    // Sticky, unchanged: an unsupported domain survives the round-trip.
    expect(simulateBoot("?m=gfs&v=tmp2m&domain=mars", DEFAULT).search)
      .toBe("?m=gfs&v=tmp2m&domain=mars");
  });

  it("round-trips a canonical flip with zero URL change", () => {
    // Global → canonical: state collapses to null, the param disappears.
    const flipped = normalizeRequestedDomainAgainstDefault("na", "na", DEFAULT);
    expect(buildPermalinkSearch({ model: "gfs", var: "tmp2m", domain: flipped || undefined }))
      .toBe("?m=gfs&v=tmp2m");
  });
});

describe("§7 world B — default 'global' (post-flip): reload-stable canonical", () => {
  const DEFAULT = "global";

  it("a fresh load with no param resolves to global", () => {
    expect(simulateBoot("?m=gfs&v=tmp2m", DEFAULT).domainState).toBe("global");
    expect(coverageSelectionValueAgainstDefault(null, "na", DEFAULT)).toBe("global");
  });

  it("an explicit canonical selection is spelled out as domain=na", () => {
    expect(normalizeRequestedDomainAgainstDefault("na", "na", DEFAULT)).toBe("na");
    expect(buildPermalinkSearch({ model: "gfs", var: "tmp2m", domain: "na" }))
      .toBe("?m=gfs&v=tmp2m&domain=na");
  });

  it("domain=na survives the round-trip instead of reverting to global", () => {
    // This is the exact regression the verifier found: without the spell-out,
    // a canonical selection serialized as absence and re-resolved to global.
    const booted = simulateBoot("?m=gfs&v=tmp2m&domain=na", DEFAULT);
    expect(booted.domainState).toBe("na");
    expect(booted.search).toBe("?m=gfs&v=tmp2m&domain=na");
    expect(simulateBoot(booted.search, DEFAULT).domainState).toBe("na");
  });

  it("keeps requests canonical for an explicit domain=na", () => {
    // `domain=na` is a URL-level statement of intent only; the request layer
    // still degrades it to canonical (no `domain=` on the wire).
    expect(resolveDataDomain("na", GLOBAL_MODEL, GLOBAL_MODEL.variables!.tmp2m)).toBeNull();
  });

  it("collapses an explicit global selection back to no param", () => {
    expect(normalizeRequestedDomainAgainstDefault("global", "na", DEFAULT)).toBeNull();
    expect(simulateBoot("?m=gfs&v=tmp2m&domain=global", DEFAULT).domainState).toBe("global");
  });

  it("share/compare serializers still carry the EFFECTIVE domain", () => {
    // Unchanged contract: share links are built from the RESOLVED dataDomain,
    // so a supported selection carries domain=global in both worlds...
    const supported = viewerPermalinkStateFromSelection({
      model: "gfs",
      variable: "tmp2m",
      run: "latest",
      fh: 0,
      dataDomain: resolveDataDomain("global", GLOBAL_MODEL, GLOBAL_MODEL.variables!.tmp2m),
    });
    expect(supported.domain).toBe("global");
    // ...and a degraded selection carries none, so the recipient lands on the
    // default — the same effective coverage the sharer was looking at.
    const degraded = viewerPermalinkStateFromSelection({
      model: "gfs",
      variable: "precip_7d_anom",
      run: "latest",
      fh: 0,
      dataDomain: resolveDataDomain("global", GLOBAL_MODEL, GLOBAL_MODEL.variables!.precip_7d_anom),
    });
    expect(degraded.domain).toBeUndefined();
  });
});

// ───────────────────────── control behavior ──────────────────────────────────

describe("coverageSegmentsForModel (design §1 visibility + segment set)", () => {
  it("renders no control for a canonical-only model", () => {
    expect(coverageSegmentsForModel(CANONICAL_ONLY_MODEL, REGION_PRESETS)).toEqual([]);
    expect(coverageSegmentsForModel(null, REGION_PRESETS)).toEqual([]);
  });

  it("derives the canonical segment from the model's canonical region id", () => {
    const segments = coverageSegmentsForModel(GLOBAL_MODEL, REGION_PRESETS);
    // The canonical segment carries the region id, never "" — absence means
    // "the default", which is not canonical after the §7 flip.
    expect(segments[0]).toEqual({ value: "na", label: "North America" });
  });

  it("derives the extra segments from the union of supported_build_regions", () => {
    const segments = coverageSegmentsForModel(GLOBAL_MODEL, REGION_PRESETS);
    expect(segments.map((segment) => segment.value)).toEqual(["na", "global"]);
    expect(segments[1]!.label).toBe("Global");
  });

  it("picks up a future domain with no UI change", () => {
    const futureModel = {
      canonical_region: "na",
      variables: {
        tmp2m: { supported_build_regions: ["na", "global"] },
        wspd10m: { supported_build_regions: ["na", "arctic"] },
      },
    } as unknown as CapabilityModel;
    expect(coverageSegmentsForModel(futureModel, REGION_PRESETS).map((s) => s.value))
      .toEqual(["na", "arctic", "global"]);
  });
});

describe("coverageDegradedNote (design §2 / decision U2)", () => {
  const globalVar = GLOBAL_MODEL.variables!.tmp2m;
  const naOnlyVar = GLOBAL_MODEL.variables!.precip_7d_anom;

  it("shows nothing on the canonical segment", () => {
    expect(coverageDegradedNote(null, GLOBAL_MODEL, naOnlyVar, REGION_PRESETS)).toBeNull();
    expect(coverageDegradedNote("na", GLOBAL_MODEL, naOnlyVar, REGION_PRESETS)).toBeNull();
  });

  it("shows nothing when the requested domain resolves", () => {
    expect(resolveDataDomain("global", GLOBAL_MODEL, globalVar)).toBe("global");
    expect(coverageDegradedNote("global", GLOBAL_MODEL, globalVar, REGION_PRESETS)).toBeNull();
  });

  it("shows the note exactly when requested is non-null but resolved is null", () => {
    expect(resolveDataDomain("global", GLOBAL_MODEL, naOnlyVar)).toBeNull();
    expect(coverageDegradedNote("global", GLOBAL_MODEL, naOnlyVar, REGION_PRESETS))
      .toBe("Not available for this variable — showing North America");
  });

  it("derives the canonical name rather than hardcoding North America", () => {
    const conusGlobalModel = {
      constraints: { canonical_region: "conus" },
      variables: {
        tmp2m: { supported_build_regions: ["conus", "global"] },
        precip: { supported_build_regions: ["conus"] },
      },
    } as unknown as CapabilityModel;
    expect(coverageDegradedNote("global", conusGlobalModel, conusGlobalModel.variables!.precip, REGION_PRESETS))
      .toBe("Not available for this variable — showing CONUS");
  });

  it("blames the MODEL, not the variable, when no segment offers the domain", () => {
    // ?domain=mars, or a sticky global carried onto a canonical-only model.
    expect(coverageDegradedNote("mars", GLOBAL_MODEL, globalVar, REGION_PRESETS))
      .toBe("Not available for this model — showing North America");
    expect(coverageDegradedNote("global", CANONICAL_ONLY_MODEL, CANONICAL_ONLY_MODEL.variables!.tmp2m, REGION_PRESETS))
      .toBe("Not available for this model — showing CONUS");
  });

  it("leaves an unknown domain unmatched by any segment (toggle unchecked)", () => {
    const segments = coverageSegmentsForModel(GLOBAL_MODEL, REGION_PRESETS);
    const selected = coverageSelectionValue("mars", GLOBAL_MODEL, globalVar);
    expect(selected).toBe("mars");
    expect(segments.some((segment) => segment.value === selected)).toBe(false);
  });
});

describe("domainRunProbeStatusForManifest (what counts as a definitive negative)", () => {
  const manifestWith = (entry: unknown) =>
    ({ model: "gfs", run: "20260731_12z", region: "global", variables: { precip_5d_anom: entry } } as never);

  it("treats a variable missing from the map as absent", () => {
    expect(domainRunProbeStatusForManifest(manifestWith({}), "tmp2m")).toBe("absent");
  });

  it("treats prod's declared-but-unbuilt shape as absent", () => {
    // Verified against api.cartosky.com run 20260731_12z, ?domain=global.
    expect(domainRunProbeStatusForManifest(
      manifestWith({ expected_frames: 105, available_frames: 0, ready_through_fh: null, expected_max_fh: 384, frames: [] }),
      "precip_5d_anom",
    )).toBe("absent");
  });

  it("treats a PARTIALLY built variable as present — mid-cycle is not missing", () => {
    // The normal in-progress state: some frames ready, more coming. Degrading
    // here would yank the operator to canonical halfway through every cycle.
    expect(domainRunProbeStatusForManifest(
      manifestWith({ expected_frames: 105, available_frames: 12, ready_through_fh: 33, expected_max_fh: 384, frames: [{ fh: 0 }] }),
      "precip_5d_anom",
    )).toBe("present");
    // A positive counter wins even if the frame LIST is still empty (the
    // manifest's two signals can lag each other).
    expect(domainRunProbeStatusForManifest(
      manifestWith({ available_frames: 12, frames: [] }),
      "precip_5d_anom",
    )).toBe("present");
  });

  it("treats a fully built variable as present", () => {
    expect(domainRunProbeStatusForManifest(
      manifestWith({ expected_frames: 2, available_frames: 2, ready_through_fh: 1, frames: [{ fh: 0 }, { fh: 1 }] }),
      "precip_5d_anom",
    )).toBe("present");
  });

  it("treats an unknown-shape entry as present, never absent", () => {
    // A legacy manifest entry carrying neither counter nor frame list is
    // unknown, not negative — only evidence may degrade.
    expect(domainRunProbeStatusForManifest(manifestWith({ display_name: "5d Precip Anomaly" }), "precip_5d_anom"))
      .toBe("present");
  });

  it("treats a null manifest / blank variable as absent", () => {
    expect(domainRunProbeStatusForManifest(null, "precip_5d_anom")).toBe("absent");
    expect(domainRunProbeStatusForManifest(manifestWith({ frames: [{ fh: 0 }] }), "")).toBe("absent");
  });
});

describe("domain-run probe verdict keying (the one-commit staleness window)", () => {
  const SELECTION_A = {
    model: "gfs", run: "latest", requestVariable: "precip_5d_anom",
    dataRegion: "na", ensembleView: "", domain: "global",
  };

  it("keys the verdict on every input the probe reads", () => {
    const keyA = domainRunProbeKey(SELECTION_A);
    for (const [field, value] of Object.entries({
      model: "ecmwf",
      run: "20260731_12z",
      requestVariable: "tmp2m",
      dataRegion: "conus",
      ensembleView: "mean",
      domain: "arctic",
    })) {
      expect(
        domainRunProbeKey({ ...SELECTION_A, [field]: value }),
        `changing ${field} must change the probe key`,
      ).not.toBe(keyA);
    }
  });

  it("has no key — and so no probe — when no domain applies", () => {
    expect(domainRunProbeKey({ ...SELECTION_A, domain: null })).toBe("");
    expect(resolveDomainRunProbeStatus("", { key: "", status: "disabled" })).toBe("disabled");
  });

  it("returns a stored verdict only for the selection it was reached for", () => {
    const keyA = domainRunProbeKey(SELECTION_A);
    expect(resolveDomainRunProbeStatus(keyA, { key: keyA, status: "absent" })).toBe("absent");
  });

  it("REGRESSION: a verdict for selection A reads as pending under selection B", () => {
    // Storing the status alone leaked A's verdict for exactly one commit —
    // React renders the new selection paired with the old status, because
    // "pending" could only arrive later from the effect. Every hold site read
    // `indeterminate === false` in that window and fired stale-domain
    // requests. Keying the verdict closes it in the same commit.
    const keyA = domainRunProbeKey(SELECTION_A);
    const keyB = domainRunProbeKey({ ...SELECTION_A, requestVariable: "tmp2m" });
    expect(keyB).not.toBe(keyA);
    for (const status of ["present", "absent", "error", "disabled"] as const) {
      expect(
        resolveDomainRunProbeStatus(keyB, { key: keyA, status }),
        `a "${status}" verdict for A must not leak into B`,
      ).toBe("pending");
    }
  });

  it("holds every consumer during that window", () => {
    const keyA = domainRunProbeKey(SELECTION_A);
    const keyB = domainRunProbeKey({ ...SELECTION_A, requestVariable: "tmp2m" });
    // A stale "absent" would otherwise have degraded B to canonical, and a
    // stale "present" would have fired requests for the outgoing domain.
    const refined = refineDataDomainForRun("global", resolveDomainRunProbeStatus(keyB, { key: keyA, status: "absent" }));
    expect(refined).toEqual({ domain: "global", indeterminate: true, runDegraded: false });
  });

  it("starts pending, never disabled, for a selection that has a domain", () => {
    // The initial verdict is {key: "", status: "disabled"}; a domain selection
    // must not read that as "no probe applies".
    expect(resolveDomainRunProbeStatus(domainRunProbeKey(SELECTION_A), { key: "", status: "disabled" }))
      .toBe("pending");
  });
});

describe("refineDataDomainForRun (run-scoped degradation, layer 3)", () => {
  const globalVar = GLOBAL_MODEL.variables!.tmp2m;
  const naOnlyVar = GLOBAL_MODEL.variables!.precip_7d_anom;
  /** Layer 2's output is layer 3's input; never the raw requested domain. */
  const supported = resolveDataDomain("global", GLOBAL_MODEL, globalVar);
  const unsupported = resolveDataDomain("global", GLOBAL_MODEL, naOnlyVar);

  it("keeps the domain when the run manifest carries the variable", () => {
    expect(refineDataDomainForRun(supported, "present"))
      .toEqual({ domain: "global", indeterminate: false, runDegraded: false });
  });

  it("degrades to canonical when the variable is absent from the domain manifest", () => {
    expect(refineDataDomainForRun(supported, "absent"))
      .toEqual({ domain: null, indeterminate: false, runDegraded: true });
  });

  it("keeps the domain for a PARTIALLY built variable (mid-cycle)", () => {
    // domainRunProbeStatusForManifest maps available_frames > 0 to "present",
    // so partial global data keeps rendering with its readiness hatch.
    const partial = domainRunProbeStatusForManifest(
      { model: "gfs", run: "r", variables: { tmp2m: { available_frames: 12, ready_through_fh: 33, frames: [] } } } as never,
      "tmp2m",
    );
    expect(partial).toBe("present");
    expect(refineDataDomainForRun(supported, partial))
      .toEqual({ domain: "global", indeterminate: false, runDegraded: false });
  });

  it("degrades to canonical when the domain manifest 404s for this run", () => {
    // The hook classifies a 404 as `absent` — same definitive negative, so the
    // selector sees one status and the note wording cannot diverge.
    expect(refineDataDomainForRun(supported, "absent").domain).toBeNull();
    expect(refineDataDomainForRun(supported, "absent").runDegraded).toBe(true);
  });

  it("is INDETERMINATE while in flight — no preemptive degrade, no flicker", () => {
    const pending = refineDataDomainForRun(supported, "pending");
    expect(pending.indeterminate).toBe(true);
    // Critically: it does NOT hand back canonical. Callers hold on
    // `indeterminate` instead of fetching canonical data they may discard.
    expect(pending.domain).toBe("global");
    expect(pending.runDegraded).toBe(false);
  });

  it("does NOT degrade on a transient fetch error", () => {
    // A network failure or 5xx is not evidence of non-publication; locking
    // into canonical would strand the selection there until a reload.
    expect(refineDataDomainForRun(supported, "error"))
      .toEqual({ domain: "global", indeterminate: false, runDegraded: false });
  });

  it("is a no-op when the probe never ran", () => {
    expect(refineDataDomainForRun(supported, "disabled"))
      .toEqual({ domain: "global", indeterminate: false, runDegraded: false });
  });

  it("leaves a capability-unsupported selection alone under every status", () => {
    // Layer 2 already took it to canonical; layer 3 must never re-attribute
    // that to the run, or the note would blame the wrong thing.
    expect(unsupported).toBeNull();
    for (const status of ["disabled", "pending", "present", "absent", "error"] as const) {
      expect(refineDataDomainForRun(unsupported, status))
        .toEqual({ domain: null, indeterminate: false, runDegraded: false });
    }
  });

  it("produces the run-scoped note for a capability-SUPPORTED variable", () => {
    const refined = refineDataDomainForRun(supported, "absent");
    expect(coverageDegradedNote("global", GLOBAL_MODEL, globalVar, REGION_PRESETS, refined.runDegraded))
      .toBe("Not available for this run — showing North America");
  });

  it("keeps the variable wording when the cause is capabilities, not the run", () => {
    const refined = refineDataDomainForRun(unsupported, "absent");
    expect(refined.runDegraded).toBe(false);
    expect(coverageDegradedNote("global", GLOBAL_MODEL, naOnlyVar, REGION_PRESETS, refined.runDegraded))
      .toBe("Not available for this variable — showing North America");
  });

  it("shows no note while the answer is still pending", () => {
    const refined = refineDataDomainForRun(supported, "pending");
    expect(coverageDegradedNote("global", GLOBAL_MODEL, globalVar, REGION_PRESETS, refined.runDegraded))
      .toBeNull();
  });

  it("keeps the toggle and the permalink on the REQUESTED domain when degraded", () => {
    // Stickiness is identical to the other two degrade paths: the run-scoped
    // degrade moves requests only.
    const refined = refineDataDomainForRun(supported, "absent");
    expect(coverageSelectionValue("global", GLOBAL_MODEL, globalVar)).toBe("global");
    expect(normalizeRequestedDomain("global", GLOBAL_MODEL, globalVar)).toBe("global");
    expect(refined.domain).toBeNull();
  });

  it("drops the World camera preset along with the effective domain", () => {
    const refined = refineDataDomainForRun(supported, "absent");
    expect(filterRegionOptionsForDataDomain(REGION_PRESETS, "na", refined.domain).map((o) => o.value))
      .not.toContain("world");
  });
});

describe("capabilityVarsForManifest under a domain-scoped manifest (U2: no hiding)", () => {
  const capabilityVars = normalizeCapabilityVarRows(GLOBAL_MODEL);
  /** Prod's ?domain=global manifest: only domain-declared variables. */
  const globalManifestVars = { tmp2m: { frames: [{ fh: 0 }] } } as never;

  it("prunes to the manifest for a CANONICAL manifest (unchanged behavior)", () => {
    expect(capabilityVarsForManifest(globalManifestVars, capabilityVars, { modelId: "gfs" }).map((e) => e.id))
      .toEqual(["tmp2m"]);
  });

  it("keeps every capability variable for a DOMAIN-SCOPED manifest", () => {
    // The global manifest answers "what does this DOMAIN have", never "what
    // does this MODEL have". Pruning with it hid precip_7d_anom from the
    // picker the moment Global was selected — the exact row the §3 badge
    // exists to advertise.
    expect(
      capabilityVarsForManifest(globalManifestVars, capabilityVars, { modelId: "gfs", domainScoped: true })
        .map((entry) => entry.id),
    ).toEqual(["precip_7d_anom", "tmp2m"]);
  });

  it("still appends manifest-only extras under a domain-scoped manifest", () => {
    const withExtra = { tmp2m: { frames: [{ fh: 0 }] }, surprise_var: { frames: [{ fh: 0 }] } } as never;
    expect(
      capabilityVarsForManifest(withExtra, capabilityVars, { modelId: "gfs", domainScoped: true })
        .map((entry) => entry.id),
    ).toEqual(["precip_7d_anom", "tmp2m", "surprise_var"]);
  });
});

describe("variableIdsMissingDomain / coverageBadgeLabel (design §3)", () => {
  const entries = normalizeCapabilityVarRows(GLOBAL_MODEL);

  it("badges nothing on the canonical segment", () => {
    expect(variableIdsMissingDomain(entries, null, GLOBAL_MODEL)).toEqual([]);
    expect(variableIdsMissingDomain(entries, "na", GLOBAL_MODEL)).toEqual([]);
  });

  it("badges exactly the variables lacking the requested domain", () => {
    expect(variableIdsMissingDomain(entries, "global", GLOBAL_MODEL)).toEqual(["precip_7d_anom"]);
  });

  it("badges variables that declare no build regions at all", () => {
    const canonicalEntries = normalizeCapabilityVarRows(CANONICAL_ONLY_MODEL);
    expect(variableIdsMissingDomain(canonicalEntries, "global", CANONICAL_ONLY_MODEL)).toEqual(["refc", "tmp2m"]);
  });

  it("labels the chip with the canonical region's short id", () => {
    expect(coverageBadgeLabel(GLOBAL_MODEL)).toBe("NA");
    expect(coverageBadgeLabel(CANONICAL_ONLY_MODEL)).toBe("CONUS");
  });

  it("is gated by the same predicate as the control, so canonical-only models never badge", () => {
    // R1: a sticky domain=global carried onto NAM must not badge every row of
    // a model that shows no Coverage control to explain them. App.tsx gates
    // both on `coverageSegmentsForModel(...).length > 1`, false here.
    expect(coverageSegmentsForModel(CANONICAL_ONLY_MODEL, REGION_PRESETS).length > 1).toBe(false);
  });
});

describe("SPC variable option ordering", () => {
  it("lists categorical ahead of tornado, wind, and hail", () => {
    const options = makeVariableOptions(normalizeCapabilityVarRows(SPC_MODEL), "spc");
    expect(options.map((option) => option.value)).toEqual([
      "convective",
      "tornado_prob",
      "wind_prob",
      "hail_prob",
    ]);
  });
});

describe("HRRR severe variable option ordering", () => {
  it("lists lightning flash density in SEVERE, immediately after the CAPE variables", () => {
    const options = makeVariableOptions(normalizeCapabilityVarRows(HRRR_SEVERE_MODEL), "hrrr");
    expect(options.map((option) => option.value)).toEqual([
      "mucape",
      "mlcape",
      "sbcape",
      "ltng",
    ]);
    expect(viewerVariableGroup("ltng", "Instability")).toBe("SEVERE");
    expect(options.find((option) => option.value === "ltng")?.label).toBe(
      "Lightning Flash Density",
    );
  });
});

describe("HRRR air quality variable grouping", () => {
  it("puts vertically integrated smoke in its own AIR QUALITY group", () => {
    const options = makeVariableOptions(normalizeCapabilityVarRows(HRRR_AIR_QUALITY_MODEL), "hrrr");
    expect(options.map((option) => option.value)).toEqual(["ltng", "vi_smoke", "smoke_sfc"]);
    expect(viewerVariableGroup("vi_smoke", "Air Quality")).toBe("AIR QUALITY");
    expect(options.find((option) => option.value === "vi_smoke")?.label).toBe(
      "Vertically Integrated Smoke",
    );
  });

  it("puts near-surface smoke in the AIR QUALITY group after vi_smoke", () => {
    const options = makeVariableOptions(normalizeCapabilityVarRows(HRRR_AIR_QUALITY_MODEL), "hrrr");
    expect(viewerVariableGroup("smoke_sfc", "Air Quality")).toBe("AIR QUALITY");
    expect(options.find((option) => option.value === "smoke_sfc")?.label).toBe(
      "Near-Surface Smoke",
    );
  });
});

describe("World camera preset gating (design §4 / decision U3)", () => {
  it("is absent for a canonical (na) selection", () => {
    const options = filterRegionOptionsForDataDomain(REGION_PRESETS, "na", null);
    expect(options.map((option) => option.value)).not.toContain("world");
  });

  it("is absent for a canonical (conus) selection", () => {
    const options = filterRegionOptionsForDataDomain(REGION_PRESETS, "conus", null);
    expect(options.map((option) => option.value)).not.toContain("world");
  });

  it("appears when a non-canonical data domain is active", () => {
    const options = filterRegionOptionsForDataDomain(REGION_PRESETS, "na", "global");
    expect(options.map((option) => option.value)).toContain("world");
    expect(options.find((option) => option.value === "world")?.label).toBe("World");
  });

  it("stays absent while a requested domain is degraded (dataDomain is null)", () => {
    const resolved = resolveDataDomain("global", GLOBAL_MODEL, GLOBAL_MODEL.variables!.precip_7d_anom);
    expect(filterRegionOptionsForDataDomain(REGION_PRESETS, "na", resolved).map((o) => o.value))
      .not.toContain("world");
  });
});

describe("analytics event contract", () => {
  it("allowlists every declared product event in PostHog", () => {
    // R3: capturePostHogProductEvent drops anything not in ALLOWED_EVENT_NAMES,
    // silently. A new AnalyticsEventName must land in both places.
    const missing = ANALYTICS_EVENT_NAMES.filter((name) => !ALLOWED_EVENT_NAMES.has(name));
    expect(missing).toEqual([]);
  });

  it("declares the globe projection toggle event", () => {
    // Globe v1 / Phase G2. Same rule as coverage_selected: an AnalyticsEventName
    // missing from ALLOWED_EVENT_NAMES is dropped silently at capture time.
    expect(ANALYTICS_EVENT_NAMES).toContain("projection_selected");
    expect(ALLOWED_EVENT_NAMES.has("projection_selected")).toBe(true);
  });

  it("declares the coverage toggle event", () => {
    expect(ANALYTICS_EVENT_NAMES).toContain("coverage_selected");
    expect(ALLOWED_EVENT_NAMES.has("coverage_selected")).toBe(true);
  });
});
