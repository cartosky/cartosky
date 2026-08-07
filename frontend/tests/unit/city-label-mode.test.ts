import { describe, expect, it } from "vitest";

import { resolveCityLabelMode, resolveAnchorDisplayRule } from "@/lib/anchor-labels";

/**
 * City label mode contract.
 *
 * `resolveCityLabelMode` is the single source of truth consumed by both the live
 * map and the server-side screenshot path (same `cityLabelMode` prop into
 * MapCanvas). The three outcomes are:
 *
 *  - "value"     → value pills + their name labels
 *  - "name-only" → candidate name labels only, value pills hidden
 *  - "off"       → nothing: value source emptied AND candidate text-opacity 0
 *
 * Note the variable-level `ANCHOR_DISPLAY_RULES` mode "hidden" only downgrades
 * to "name-only" — it hides VALUES, not names. Fully label-free therefore has to
 * come from the model gate, which is what the sst cases below pin down.
 */
describe("resolveCityLabelMode", () => {
  it("returns off for the sst model on every sst variable", () => {
    expect(resolveCityLabelMode({ model: "sst", variable: "sst" })).toBe("off");
    expect(resolveCityLabelMode({ model: "sst", variable: "sst_anom" })).toBe("off");
    expect(resolveCityLabelMode({ model: "SST", variable: "SST_ANOM" })).toBe("off");
  });

  it("leaves unrelated models untouched", () => {
    expect(resolveCityLabelMode({ model: "gfs", variable: "tmp2m" })).toBe("value");
    expect(resolveCityLabelMode({ model: "hrrr", variable: "refc" })).toBe("value");
    expect(resolveCityLabelMode({ model: "mrms", variable: "mrms_radar_ptype" })).toBe("name-only");
    expect(resolveCityLabelMode({ model: "mrms", variable: "mrms_recent_precip_6h" })).toBe("value");
    expect(resolveCityLabelMode({ model: "cpc", variable: "tmp2m" })).toBe("name-only");
    expect(resolveCityLabelMode({ model: "spc", variable: "tmp2m" })).toBe("name-only");
  });

  it("keeps the variable-level hidden rule as a name-only downgrade", () => {
    expect(resolveAnchorDisplayRule("wspd850").mode).toBe("hidden");
    expect(resolveCityLabelMode({ model: "gfs", variable: "wspd850" })).toBe("name-only");
  });

  it("returns off when model or variable is missing", () => {
    expect(resolveCityLabelMode({ model: null, variable: "tmp2m" })).toBe("off");
    expect(resolveCityLabelMode({ model: "gfs", variable: "" })).toBe("off");
  });
});
