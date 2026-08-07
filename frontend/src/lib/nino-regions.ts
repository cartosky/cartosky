/**
 * ENSO monitoring boxes drawn as a static reference overlay on the SST maps.
 *
 * Only Niño 3.4 is enabled — it is the box the ENSO indices are actually defined
 * on, and it is the one forecasters look for. The remaining regions are kept
 * commented out below with their exact coordinates so enabling one later is a
 * copy of the block, not a re-derivation.
 */

export type NinoRegion = {
  id: string;
  label: string;
  /** [west, south, east, north] in degrees. */
  bounds: [number, number, number, number];
};

/** Niño 3.4: 5°S–5°N, 170°W–120°W. */
export const NINO_34: NinoRegion = {
  id: "nino34",
  label: "Niño 3.4",
  bounds: [-170, -5, -120, 5],
};

// Regions kept for future enablement — exact coordinates, do not re-derive.
//
// /** Niño 1+2: 0°–10°S, 90°W–80°W. */
// export const NINO_12: NinoRegion = { id: "nino12", label: "Niño 1+2", bounds: [-90, -10, -80, 0] };
//
// /** Niño 3: 5°N–5°S, 150°W–90°W. */
// export const NINO_3: NinoRegion = { id: "nino3", label: "Niño 3", bounds: [-150, -5, -90, 5] };
//
// /** Niño 4: 5°N–5°S, 160°E–150°W — CROSSES THE ANTIMERIDIAN: when enabling, split
//  * into two polygons ([160,-5]..[180,5] and [-180,-5]..[-150,5]) or the box will
//  * wrap the wrong way around the globe. */
// export const NINO_4: NinoRegion = { id: "nino4", label: "Niño 4", bounds: [160, -5, -150, 5] };

/** Ring for a bounds box, closed, counter-clockwise from the south-west corner. */
function boundsRing(bounds: [number, number, number, number]): [number, number][] {
  const [west, south, east, north] = bounds;
  return [
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ];
}

/**
 * Outline polygon plus a separate label anchor point, in one FeatureCollection.
 * The style splits them with a `kind` filter: `outline` feeds the dashed line
 * layer, `label` feeds the symbol layer. The anchor sits at the middle of the
 * box's NORTH edge so the text rides above the box instead of sitting over the
 * data in the middle of it.
 */
export function buildNinoRegionsGeoJson(regions: readonly NinoRegion[] = [NINO_34]) {
  const features = regions.flatMap((region) => {
    const [west, , east, north] = region.bounds;
    return [
      {
        type: "Feature" as const,
        properties: { kind: "outline", id: region.id, label: region.label },
        geometry: {
          type: "Polygon" as const,
          coordinates: [boundsRing(region.bounds)],
        },
      },
      {
        type: "Feature" as const,
        properties: { kind: "label", id: region.id, label: region.label },
        geometry: {
          type: "Point" as const,
          coordinates: [(west + east) / 2, north],
        },
      },
    ];
  });

  return { type: "FeatureCollection" as const, features };
}

/**
 * Variables the box is shown for. Deliberately an explicit id list and NOT the
 * manifest's `clip_to_water` flag: the box is SST semantics (ENSO reference),
 * not ocean-mask semantics, so a future water-only variable does not inherit it.
 */
export const NINO_OVERLAY_VARIABLE_IDS = ["sst", "sst_anom"] as const;

export function isNinoOverlayVariable(variable?: string | null): boolean {
  return Boolean(variable && (NINO_OVERLAY_VARIABLE_IDS as readonly string[]).includes(variable));
}
