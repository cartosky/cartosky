import { describe, expect, it } from "vitest";

import { buildShareOverlayLines, type ScreenshotExportState } from "@/lib/screenshot_export";

/**
 * Observed-product share overlays append the viewer's freshness tag
 * ("Live"/"Delayed"/"Stale") to title line 1 — except for SST, a daily product
 * whose newest frame is legitimately ~30-54h behind real time, where "Live"
 * would read as a lie in an image that no longer carries the viewer's context.
 */
function observedState(overrides: Partial<ScreenshotExportState>): ScreenshotExportState {
  return {
    center: [-95, 38],
    zoom: 4,
    isMobile: false,
    model: "MRMS Radar",
    modelId: "mrms",
    run: "12Z 8/07",
    variable: { key: "reflectivity", label: "Reflectivity" },
    fh: 0,
    timeAxisMode: "observed",
    validTimeISO: "2026-08-07T12:00:00Z",
    sourceStatusLabel: "Live",
    animationEnabled: false,
    ...overrides,
  };
}

describe("buildShareOverlayLines — observed freshness tag", () => {
  it("keeps the status suffix for non-SST observed products", () => {
    const [title, variableLine] = buildShareOverlayLines(observedState({}));
    expect(title).toContain("MRMS Radar • ");
    expect(title).toContain(" • Live");
    expect(variableLine).toBe("Reflectivity");
  });

  it("omits the status suffix for the sst model", () => {
    const [title, variableLine] = buildShareOverlayLines(
      observedState({ model: "SST", modelId: "sst", variable: { key: "sst", label: "Sea Surface Temp" } }),
    );
    expect(title).toContain("SST • ");
    expect(title).not.toContain("Live");
    expect(variableLine).toBe("Sea Surface Temp");
  });

  it("omits the status suffix for the sst model on the anomaly variable too", () => {
    const [title] = buildShareOverlayLines(
      observedState({
        model: "SST",
        modelId: "sst",
        variable: { key: "sst_anom", label: "SST Anomaly" },
        sourceStatusLabel: "Delayed",
      }),
    );
    expect(title).not.toContain("Delayed");
  });

  it("renders no dangling separator when the status is absent", () => {
    const [title] = buildShareOverlayLines(observedState({ sourceStatusLabel: null }));
    expect(title.endsWith(" • ")).toBe(false);
    expect(title).not.toContain("Live");
    // Exactly one separator: "{model} • {observed time}".
    expect(title.split(" • ")).toHaveLength(2);
  });

  it("suppression keys on the model id, not the display label", () => {
    const [title] = buildShareOverlayLines(observedState({ model: "SST", modelId: "mrms" }));
    expect(title).toContain(" • Live");
  });
});
