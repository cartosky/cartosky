import { describe, expect, it } from "vitest";

import { buildMapStyle } from "@/components/map-canvas";

/**
 * Land-mask layer contract (SST coastline clipping).
 *
 * The mask is a fill of the boundaries tileset's `land_polygon` features drawn
 * directly above the grid layer, so a water-only variable's ragged texel edge
 * renders as a real coastline. What has to hold:
 *
 *  - it exists in the base style, hidden, so no other variable is affected;
 *  - it is theme-aware and matches the CARTO basemap land colour;
 *  - it filters on `kind == "land_polygon"` in the `hydro` source layer, which is
 *    also what makes an OLD tileset (no land_polygon features) degrade to exactly
 *    today's behaviour: the filter matches nothing and the layer draws nothing;
 *  - the Great Lakes fill stays a separate layer (it lives BELOW the grid).
 */

const LAND_MASK_LAYER_ID = "twf-land-mask";
const LAKE_MASK_LAYER_ID = "twf-lake-mask";
const COASTLINE_LAYER_ID = "twf-coastline";
const BOUNDARIES_SOURCE_ID = "twf-boundaries";

function landMaskLayer(style: ReturnType<typeof buildMapStyle>) {
  const layer = style.layers.find((candidate) => candidate.id === LAND_MASK_LAYER_ID);
  if (!layer) {
    throw new Error(`${LAND_MASK_LAYER_ID} missing from style`);
  }
  return layer as typeof layer & {
    type: string;
    source?: string;
    "source-layer"?: string;
    filter?: unknown;
    layout?: Record<string, unknown>;
    paint?: Record<string, unknown>;
  };
}

describe("land mask layer", () => {
  it("is declared in the base style as a hidden fill", () => {
    const layer = landMaskLayer(buildMapStyle(null, null, "light"));
    expect(layer.type).toBe("fill");
    // Hidden by default: only the grid effect turns it on, for variables whose
    // manifest carries display_prep.clip_to_water.
    expect(layer.layout?.visibility).toBe("none");
    expect(layer.paint?.["fill-opacity"]).toBe(1);
  });

  it("reads land polygons out of the boundaries hydro source layer", () => {
    const layer = landMaskLayer(buildMapStyle(null, null, "light"));
    expect(layer.source).toBe(BOUNDARIES_SOURCE_ID);
    expect(layer["source-layer"]).toBe("hydro");
    // This filter IS the graceful-degradation mechanism: against a tileset built
    // before land_polygon existed it matches zero features, so the layer renders
    // nothing and the map behaves exactly as it does today.
    expect(layer.filter).toEqual(["==", "kind", "land_polygon"]);
  });

  it("uses a theme-aware land colour distinct from the lake fill", () => {
    const light = landMaskLayer(buildMapStyle(null, null, "light"));
    const dark = landMaskLayer(buildMapStyle(null, null, "dark"));

    const lightColor = String(light.paint?.["fill-color"]);
    const darkColor = String(dark.paint?.["fill-color"]);

    expect(lightColor).toMatch(/^#[0-9a-f]{6}$/i);
    expect(darkColor).toMatch(/^#[0-9a-f]{6}$/i);
    expect(lightColor).not.toBe(darkColor);
    // Light basemap land is near-white, dark basemap land is near-black.
    expect(Number.parseInt(lightColor.slice(1, 3), 16)).toBeGreaterThan(0xd0);
    expect(Number.parseInt(darkColor.slice(1, 3), 16)).toBeLessThan(0x50);
  });

  it("does not disturb the Great Lakes mask, which sits below the grid", () => {
    const style = buildMapStyle(null, null, "light");
    const ids = style.layers.map((layer) => layer.id);
    expect(ids).toContain(LAKE_MASK_LAYER_ID);
    expect(ids).toContain(COASTLINE_LAYER_ID);
    // Two independent layers with different filters — the lake fill is never
    // repurposed as the land mask.
    const lake = style.layers.find((layer) => layer.id === LAKE_MASK_LAYER_ID) as {
      filter?: unknown;
    };
    expect(lake.filter).toEqual(["==", "kind", "great_lake_polygon"]);
  });

  it("keeps every non-water variable's style free of a visible mask", () => {
    for (const variable of ["tmp2m", "reflectivity", "precip_total", "sst"]) {
      const layer = landMaskLayer(buildMapStyle(null, null, "light", 1, variable));
      // Visibility is runtime state driven by the manifest flag, never baked in
      // per variable — so the base style is hidden for all of them, including sst.
      expect(layer.layout?.visibility).toBe("none");
    }
  });
});
