import { describe, expect, it } from "vitest";

import { buildComparePermalinkSearch } from "@/lib/compare-permalink";
import { buildPermalinkSearch, viewerPermalinkStateFromSelection } from "@/lib/permalink";

/**
 * Pins that the three App.tsx permalink call sites (share modal, feedback page
 * context, compare handoff) all derive their core params from the same
 * `viewerPermalinkStateFromSelection` shape, so a new param can never be
 * silently omitted from one of them the way `domain` was on 2026-07-30.
 */

const SELECTION = {
  model: "gfs",
  run: "2026072700",
  variable: "tmp2m",
  ensembleView: undefined,
  product: undefined,
  region: "conus",
  dataDomain: "global",
  fh: 24,
  lat: 39.83,
  lon: -98.58,
  z: 4,
};

describe("shared viewer permalink selection", () => {
  it("agrees on core params across the share/feedback and compare surfaces", () => {
    // Share modal + feedback page context both use exactly this shape.
    const shareSearch = buildPermalinkSearch(viewerPermalinkStateFromSelection(SELECTION));

    // Mirrors the compareHref memo in App.tsx.
    const core = viewerPermalinkStateFromSelection(SELECTION);
    const compareSearch = buildComparePermalinkSearch({
      lm: core.model,
      lv: core.var,
      lr: core.run,
      rm: core.model,
      rv: core.var,
      rr: "latest",
      fh: core.fh,
      domain: core.domain,
      lat: core.lat,
      lon: core.lon,
      z: core.z,
    });

    const shareParams = new URLSearchParams(shareSearch);
    const compareParams = new URLSearchParams(compareSearch);

    expect(compareParams.get("lm")).toBe(shareParams.get("m"));
    expect(compareParams.get("lv")).toBe(shareParams.get("v"));
    expect(compareParams.get("lr")).toBe(shareParams.get("r"));
    expect(compareParams.get("fh")).toBe(shareParams.get("fh"));
    expect(compareParams.get("domain")).toBe(shareParams.get("domain"));
    // lat/lon/z formatting precision differs between the two search builders,
    // but the underlying values passed through the shared core must agree.
    expect(Number(compareParams.get("lat"))).toBe(Number(shareParams.get("lat")));
    expect(Number(compareParams.get("lon"))).toBe(Number(shareParams.get("lon")));
    expect(Number(compareParams.get("z"))).toBe(Number(shareParams.get("z")));
  });

  it("drops non-finite fh, null domain, and empty-string model", () => {
    const state = viewerPermalinkStateFromSelection({
      ...SELECTION,
      model: "",
      dataDomain: null,
      fh: Number.NaN,
    });
    const search = buildPermalinkSearch(state);
    const params = new URLSearchParams(search);
    expect(params.has("fh")).toBe(false);
    expect(params.has("domain")).toBe(false);
    expect(params.has("m")).toBe(false);
  });
});
