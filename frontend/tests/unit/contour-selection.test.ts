import { describe, expect, it } from "vitest";

import { contourSelectionKey, isSameContourSelection } from "@/lib/contour-selection";

const base = "https://api.example.com/api/v4/gfs/2026080700/hgt500";
const domainBase = "https://api.example.com/api/v4/domains/global/gfs/2026080700/hgt500";

function contourUrl(prefix: string, fh: number, key = "default"): string {
  return `${prefix}/${fh}/contours/${key}`;
}

describe("contourSelectionKey", () => {
  it("masks only the forecast-hour segment", () => {
    expect(contourSelectionKey(contourUrl(base, 6))).toBe(
      "https://api.example.com/api/v4/gfs/2026080700/hgt500/*/contours/default"
    );
  });

  it("keeps the query string in the key", () => {
    expect(contourSelectionKey(`${contourUrl(base, 6)}?v=2`)).toBe(
      "https://api.example.com/api/v4/gfs/2026080700/hgt500/*/contours/default?v=2"
    );
  });

  it("falls back to the raw url when the shape is unrecognized", () => {
    expect(contourSelectionKey("/some/other/artifact.json")).toBe("/some/other/artifact.json");
  });

  it("returns an empty key for blank input", () => {
    expect(contourSelectionKey(null)).toBe("");
    expect(contourSelectionKey("   ")).toBe("");
  });
});

describe("isSameContourSelection", () => {
  it("matches across forecast hours of one selection", () => {
    expect(isSameContourSelection(contourUrl(base, 6), contourUrl(base, 12))).toBe(true);
  });

  it("does not match a different variable, run, model, contour key, or domain", () => {
    const cases = [
      "https://api.example.com/api/v4/gfs/2026080700/mslp",
      "https://api.example.com/api/v4/gfs/2026080712/hgt500",
      "https://api.example.com/api/v4/ecmwf/2026080700/hgt500",
      domainBase,
    ];
    for (const prefix of cases) {
      expect(isSameContourSelection(contourUrl(base, 6), contourUrl(prefix, 12))).toBe(false);
    }
    expect(isSameContourSelection(contourUrl(base, 6), contourUrl(base, 12, "fine"))).toBe(false);
  });

  it("never matches when either side is blank", () => {
    expect(isSameContourSelection("", contourUrl(base, 6))).toBe(false);
    expect(isSameContourSelection(contourUrl(base, 6), "")).toBe(false);
    expect(isSameContourSelection("", "")).toBe(false);
  });
});
