import { describe, expect, it, afterEach } from "vitest";

import { buildComparePermalinkSearch, readComparePermalink } from "@/lib/compare-permalink";
import { buildPermalinkSearch, viewerPermalinkStateFromSelection } from "@/lib/permalink";
import { readPermalink } from "@/lib/permalink-read";

/**
 * `proj=globe` (Globe v1 / Phase G2) follows the `domain` param's conventions
 * exactly: absent when default, sticky on round-trip, silently degrading on an
 * unknown value. The point of pinning it separately is that projection is a
 * CAMERA concern — a `proj` that ever reached a data request would be a bug.
 */

const originalWindow = (globalThis as { window?: unknown }).window;

function withSearch(search: string): void {
  (globalThis as { window?: unknown }).window = { location: { search } };
}

afterEach(() => {
  if (originalWindow === undefined) {
    delete (globalThis as { window?: unknown }).window;
  } else {
    (globalThis as { window?: unknown }).window = originalWindow;
  }
});

describe("viewer permalink — proj", () => {
  it("serializes the globe projection and round-trips it", () => {
    const search = buildPermalinkSearch({ model: "gfs", var: "tmp2m", projection: "globe" });
    expect(search).toContain("proj=globe");

    withSearch(search);
    expect(readPermalink().projection).toBe("globe");
  });

  it("omits the param entirely for the flat default", () => {
    const flat = buildPermalinkSearch({ model: "gfs", var: "tmp2m", projection: undefined });
    const unset = buildPermalinkSearch({ model: "gfs", var: "tmp2m" });
    expect(flat).not.toContain("proj");
    // Byte-identical: adding the field to the type must not perturb an existing
    // flat permalink, or every shared link in the wild changes shape.
    expect(flat).toBe(unset);
  });

  it("maps the selection flag through the consolidated builder", () => {
    expect(viewerPermalinkStateFromSelection({ globeProjection: true }).projection).toBe("globe");
    expect(viewerPermalinkStateFromSelection({ globeProjection: false }).projection).toBeUndefined();
    expect(viewerPermalinkStateFromSelection({ globeProjection: null }).projection).toBeUndefined();
    expect(viewerPermalinkStateFromSelection({}).projection).toBeUndefined();
  });

  it("degrades an unknown projection to flat rather than stranding the viewer", () => {
    for (const search of ["?proj=cube", "?proj=", "?proj=%20"]) {
      withSearch(search);
      expect(readPermalink().projection).toBeUndefined();
    }
  });

  it("reads case-insensitively", () => {
    withSearch("?proj=GLOBE");
    expect(readPermalink().projection).toBe("globe");
  });
});

describe("permalink zoom range — negative z", () => {
  /**
   * A globe camera's zoom is latitude-adjusted (`zoom + log2(cos lat)` holds
   * the disc's screen size constant), so the two cameras the feature exists for
   * are legitimately negative: a whole-disc view at lat 20 is z -0.0897, and a
   * framed pole at lat 89.5 is z -6.8404. The old `z >= 0` guard dropped the
   * param for exactly those, and the reloaded link fell back to the region
   * default instead of the camera the user shared.
   */
  const WHOLE_DISC_Z = -0.0897;
  const POLAR_Z = -6.8404;

  it("serializes and reads back the two globe cameras that needed it", () => {
    for (const z of [WHOLE_DISC_Z, POLAR_Z]) {
      const search = buildPermalinkSearch({ model: "gfs", projection: "globe", lat: 20, lon: 10, z });
      expect(search, `z=${z} must reach the URL`).toContain("z=");
      withSearch(search);
      // Serialized at 2 dp, so the round-trip is exact to that precision.
      expect(readPermalink().z).toBeCloseTo(z, 2);
    }
  });

  it("keeps the writer and reader ranges identical", () => {
    // A link this app emits must be one it can read back. The floor is derived
    // from the globe constrain's own lowest reachable zoom (log2(cos 89.9) =
    // -9.1623) with margin; 24 is the pre-existing ceiling.
    for (const [z, kept] of [[-9.1623, true], [-10, true], [-10.01, false], [24, true], [24.01, false]] as const) {
      const search = buildPermalinkSearch({ model: "gfs", z });
      expect(search.includes("z="), `writer z=${z}`).toBe(kept);
      withSearch(`?z=${z}`);
      expect(readPermalink().z !== undefined, `reader z=${z}`).toBe(kept);
    }
  });

  it("changes nothing for the flat map, which cannot produce a negative z", () => {
    // Region presets all set minZoom >= 0 and the transform's constrain
    // enforces it, so a negative z on flat can only come from a hand-edited
    // URL — where it is accepted, then clamped on apply rather than erroring.
    const flat = buildPermalinkSearch({ model: "gfs", var: "tmp2m", lat: 39.83, lon: -98.58, z: 4 });
    expect(flat).toBe("?m=gfs&v=tmp2m&lat=39.83&lon=-98.58&z=4");
    withSearch("?m=gfs&z=-1");
    expect(readPermalink().z).toBe(-1);
    expect(readPermalink().projection).toBeUndefined();
  });
});

describe("compare permalink — proj is ignored", () => {
  /**
   * Compare never enters globe (docs/GLOBE_OVERLAY_AUDIT_2026-08-01.md item 5
   * and "Recommended v1 scope"): each pane is ~700 px, so each disc is clipped
   * at the divider into a half-globe, and compare's whole value is
   * pixel-comparable panes. This is a DECISION — the flag already reaches both
   * panes and they sync correctly through a drag — so it is pinned rather than
   * left to look like an oversight. compare.tsx also drops the renderer switch
   * on mount; this pins the URL half.
   */
  it("does not read proj off a compare deep link", () => {
    withSearch("?lm=gfs&lv=tmp2m&rm=gfs&rv=tmp2m&proj=globe");
    const state = readComparePermalink() as Record<string, unknown>;
    expect(state.projection).toBeUndefined();
    expect(state.proj).toBeUndefined();
  });

  it("never writes proj, including from a viewer handoff that is on the globe", () => {
    const core = viewerPermalinkStateFromSelection({
      model: "gfs",
      variable: "tmp2m",
      run: "20260729_12z",
      globeProjection: true,
      lat: 39.83,
      lon: -98.58,
      z: 4,
    });
    // The viewer's own state does carry it...
    expect(core.projection).toBe("globe");
    // ...and the compare handoff drops it on the floor.
    const search = buildComparePermalinkSearch({
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
    expect(search).not.toContain("proj");
  });
});
