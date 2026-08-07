import { describe, expect, it } from "vitest";

import { buildMapStyle } from "@/components/map-canvas";
import { NINO_34, NINO_OVERLAY_VARIABLE_IDS, isNinoOverlayVariable } from "@/lib/nino-regions";

/**
 * Niño 3.4 reference-box contract.
 *
 * A static annotation drawn over the SST grid. What has to hold:
 *
 *  - outline + label layers exist in the base style, HIDDEN, so no non-SST
 *    variable is affected (visibility is runtime state, never baked per variable);
 *  - there is NO fill layer on the source — a fill would tint anomaly colours;
 *  - the polygon is the real Niño 3.4 box (5°S–5°N, 170°W–120°W);
 *  - colours are theme-aware and the label carries a contrasting halo;
 *  - the label anchor is its own point feature on the box's north edge, so the
 *    text does not sit over the data in the middle of the box.
 */

const NINO_SOURCE_ID = "twf-nino-regions";
const NINO_LINE_LAYER_ID = "twf-nino-region-line";
const NINO_LABEL_LAYER_ID = "twf-nino-region-label";

type AnyLayer = {
  id: string;
  type: string;
  source?: string;
  filter?: unknown;
  layout?: Record<string, unknown>;
  paint?: Record<string, unknown>;
};

function layerById(style: ReturnType<typeof buildMapStyle>, id: string): AnyLayer {
  const layer = style.layers.find((candidate) => candidate.id === id);
  if (!layer) {
    throw new Error(`${id} missing from style`);
  }
  return layer as unknown as AnyLayer;
}

function ninoSourceData(style: ReturnType<typeof buildMapStyle>) {
  const source = style.sources[NINO_SOURCE_ID] as { type: string; data: unknown };
  expect(source?.type).toBe("geojson");
  return source.data as {
    type: string;
    features: Array<{
      properties: Record<string, unknown>;
      geometry: { type: string; coordinates: unknown };
    }>;
  };
}

describe("nino region layer", () => {
  it("declares a hidden dashed outline and a hidden label", () => {
    const style = buildMapStyle(null, null, "light");
    const line = layerById(style, NINO_LINE_LAYER_ID);
    const label = layerById(style, NINO_LABEL_LAYER_ID);

    expect(line.type).toBe("line");
    expect(label.type).toBe("symbol");
    // Hidden by default: only the grid effect turns these on, for SST variables.
    expect(line.layout?.visibility).toBe("none");
    expect(label.layout?.visibility).toBe("none");

    expect(line.paint?.["line-dasharray"]).toEqual([4, 3]);
    expect(label.layout?.["text-field"]).toEqual(["get", "label"]);
    expect(label.layout?.["text-font"]).toEqual(["Noto Sans Regular"]);
    // Thin: 1–1.5px across the zoom range (zoom/width pairs after the head).
    const width = line.paint?.["line-width"] as unknown[];
    expect(width.slice(0, 3)).toEqual(["interpolate", ["linear"], ["zoom"]]);
    const pairs = width.slice(3) as number[];
    for (let index = 1; index < pairs.length; index += 2) {
      expect(pairs[index]).toBeGreaterThanOrEqual(1);
      expect(pairs[index]).toBeLessThanOrEqual(1.5);
    }
  });

  it("never declares a fill on the nino source, so anomaly colours stay untinted", () => {
    const style = buildMapStyle(null, null, "light");
    const fills = style.layers.filter((layer) => {
      const candidate = layer as unknown as AnyLayer;
      return candidate.source === NINO_SOURCE_ID && candidate.type === "fill";
    });
    expect(fills).toHaveLength(0);
    // Only the two annotation layers read the source.
    const ids = style.layers
      .filter((layer) => (layer as unknown as AnyLayer).source === NINO_SOURCE_ID)
      .map((layer) => layer.id);
    expect(ids.sort()).toEqual([NINO_LABEL_LAYER_ID, NINO_LINE_LAYER_ID].sort());
  });

  it("carries the Niño 3.4 polygon and a north-edge label anchor", () => {
    const data = ninoSourceData(buildMapStyle(null, null, "light"));
    expect(data.type).toBe("FeatureCollection");

    const outline = data.features.find((feature) => feature.properties.kind === "outline");
    const label = data.features.find((feature) => feature.properties.kind === "label");
    if (!outline || !label) {
      throw new Error("expected both an outline and a label feature");
    }

    expect(outline.geometry.type).toBe("Polygon");
    // 5°S–5°N, 170°W–120°W, closed ring.
    expect(outline.geometry.coordinates).toEqual([
      [
        [-170, -5],
        [-120, -5],
        [-120, 5],
        [-170, 5],
        [-170, -5],
      ],
    ]);
    expect(NINO_34.bounds).toEqual([-170, -5, -120, 5]);

    // Label anchor is a separate point on the NORTH edge, not the box centroid.
    expect(label.geometry.type).toBe("Point");
    expect(label.geometry.coordinates).toEqual([-145, 5]);
    expect(label.properties.label).toBe("Niño 3.4");
  });

  it("uses theme-aware ink with a contrasting halo", () => {
    const light = layerById(buildMapStyle(null, null, "light"), NINO_LINE_LAYER_ID);
    const dark = layerById(buildMapStyle(null, null, "dark"), NINO_LINE_LAYER_ID);
    const lightLabel = layerById(buildMapStyle(null, null, "light"), NINO_LABEL_LAYER_ID);
    const darkLabel = layerById(buildMapStyle(null, null, "dark"), NINO_LABEL_LAYER_ID);

    const lightColor = String(light.paint?.["line-color"]);
    const darkColor = String(dark.paint?.["line-color"]);
    expect(lightColor).toMatch(/^#[0-9a-f]{6}$/i);
    expect(darkColor).toMatch(/^#[0-9a-f]{6}$/i);
    expect(lightColor).not.toBe(darkColor);
    // Dark slate on light theme, light grey on dark theme.
    expect(Number.parseInt(lightColor.slice(1, 3), 16)).toBeLessThan(0x80);
    expect(Number.parseInt(darkColor.slice(1, 3), 16)).toBeGreaterThan(0x80);

    // Label ink tracks the outline; halo goes the other way for contrast.
    expect(lightLabel.paint?.["text-color"]).toBe(lightColor);
    expect(darkLabel.paint?.["text-color"]).toBe(darkColor);
    expect(String(lightLabel.paint?.["text-halo-color"])).not.toBe(
      String(darkLabel.paint?.["text-halo-color"]),
    );
    expect(Number(lightLabel.paint?.["text-halo-width"])).toBeGreaterThan(0);
  });

  it("stays hidden in the base style for every variable, SST included", () => {
    for (const variable of ["tmp2m", "reflectivity", "precip_total", "sst", "sst_anom", "sst_trend_7d"]) {
      const style = buildMapStyle(null, null, "light", 1, variable);
      expect(layerById(style, NINO_LINE_LAYER_ID).layout?.visibility).toBe("none");
      expect(layerById(style, NINO_LABEL_LAYER_ID).layout?.visibility).toBe("none");
    }
  });

  it("gates visibility on an explicit SST id allowlist", () => {
    expect([...NINO_OVERLAY_VARIABLE_IDS]).toEqual(["sst", "sst_anom", "sst_trend_7d"]);
    expect(isNinoOverlayVariable("sst")).toBe(true);
    expect(isNinoOverlayVariable("sst_anom")).toBe(true);
    expect(isNinoOverlayVariable("sst_trend_7d")).toBe(true);
    for (const variable of ["tmp2m", "tmp850_anom", "reflectivity", "sst_foo", "", undefined, null]) {
      expect(isNinoOverlayVariable(variable)).toBe(false);
    }
  });

  it("is declared above the land mask, label above outline", () => {
    // The final stacking (above grid + mask, below coastline/roads/labels) is
    // established at runtime by enforceLayerOrder, which moves grid, mask and
    // then these two below the coastline in that order. What the base style has
    // to guarantee is the relative order it preserves: mask, outline, label.
    const style = buildMapStyle(null, null, "light");
    const ids = style.layers.map((layer) => layer.id);
    expect(ids.indexOf(NINO_LINE_LAYER_ID)).toBeGreaterThan(ids.indexOf("twf-land-mask"));
    expect(ids.indexOf(NINO_LABEL_LAYER_ID)).toBeGreaterThan(ids.indexOf(NINO_LINE_LAYER_ID));
  });
});
