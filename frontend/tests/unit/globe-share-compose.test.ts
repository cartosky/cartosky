import { describe, expect, it } from "vitest";

import { screenshotUrlForState } from "@/components/share/share-utils";
import { DISC_FRAME_PADDING, globeShareCropRect } from "@/lib/screenshot_export";
import type { ScreenshotExportState } from "@/lib/screenshot_export";

/**
 * Globe share-export composition (globe audit items 8c/8d).
 *
 * 8c: `composeShareFrame` crops the captured map to a centred square around the
 * globe disc so the planet fills the exported frame. Only the crop geometry is
 * unit-testable without a canvas; the draw itself is covered by the
 * render-golden-baseline Playwright suite, which must NOT move for flat frames.
 *
 * 8d (REVERSED): the server-side screenshot render used to stay mercator. It no
 * longer does — a signed-in capture taken from a globe view renders as a globe,
 * so `proj=globe` is forwarded and the camera travels at permalink range.
 */

describe("globeShareCropRect", () => {
  it("returns a centred square around the disc", () => {
    const rect = globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 200 }, 1120, 800);
    const side = 2 * 200 * DISC_FRAME_PADDING;
    expect(rect).not.toBeNull();
    expect(rect!.sWidth).toBeCloseTo(side, 6);
    expect(rect!.sHeight).toBeCloseTo(side, 6);
    // Square, and centred on the disc.
    expect(rect!.sWidth).toBe(rect!.sHeight);
    expect(rect!.sx + rect!.sWidth / 2).toBeCloseTo(560, 6);
    expect(rect!.sy + rect!.sHeight / 2).toBeCloseTo(400, 6);
  });

  it("leaves a little air around the limb", () => {
    const radiusPx = 150;
    const rect = globeShareCropRect({ centerX: 400, centerY: 300, radiusPx }, 800, 600)!;
    expect(rect.sWidth).toBeGreaterThan(2 * radiusPx);
    expect(rect.sWidth / (2 * radiusPx)).toBeCloseTo(DISC_FRAME_PADDING, 6);
  });

  it("clamps the crop into the source bounds for an off-centre disc", () => {
    const rect = globeShareCropRect({ centerX: 40, centerY: 760, radiusPx: 200 }, 1120, 800)!;
    const side = 2 * 200 * DISC_FRAME_PADDING;
    expect(rect.sWidth).toBeCloseTo(side, 6);
    expect(rect.sx).toBe(0);
    expect(rect.sy).toBeCloseTo(800 - side, 6);
    expect(rect.sx).toBeGreaterThanOrEqual(0);
    expect(rect.sy).toBeGreaterThanOrEqual(0);
    expect(rect.sx + rect.sWidth).toBeLessThanOrEqual(1120);
    expect(rect.sy + rect.sHeight).toBeLessThanOrEqual(800);
  });

  it("returns null for a missing or non-finite disc", () => {
    expect(globeShareCropRect(null, 1120, 800)).toBeNull();
    expect(globeShareCropRect(undefined, 1120, 800)).toBeNull();
    expect(globeShareCropRect({ centerX: NaN, centerY: 400, radiusPx: 200 }, 1120, 800)).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: NaN }, 1120, 800)).toBeNull();
    expect(
      globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: Infinity }, 1120, 800),
    ).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 0 }, 1120, 800)).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: -10 }, 1120, 800)).toBeNull();
  });

  it("returns null for a non-finite or empty source frame", () => {
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 200 }, 0, 800)).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 200 }, 1120, 0)).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 200 }, NaN, 800)).toBeNull();
  });

  it("returns null when the disc already fills the frame (nothing to crop)", () => {
    // Padded diameter exactly the short side, and beyond it.
    const exact = 800 / (2 * DISC_FRAME_PADDING);
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: exact }, 1120, 800)).toBeNull();
    expect(globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: 600 }, 1120, 800)).toBeNull();
    // Just under the threshold still crops.
    expect(
      globeShareCropRect({ centerX: 560, centerY: 400, radiusPx: exact * 0.99 }, 1120, 800),
    ).not.toBeNull();
  });
});

/**
 * The server screenshot service is told the camera and the frame, and nothing
 * else. `screenshotUrlForState` copies the permalink it is given and then sets
 * a fixed set of params.
 *
 * The `proj` behaviour is the reversed decision, and it is asymmetric on
 * purpose. App.tsx builds the share permalink with
 * `globeProjection: globeProjectionEnabled`, so the permalink fed to this
 * function carries `proj=globe` exactly when the sharer is on the globe — and
 * that is now what the headless render must reproduce, so it PASSES THROUGH.
 * Any other `proj` value is still stripped: an unrecognized projection is not
 * something to hand a renderer, and a flat permalink has none to begin with, so
 * flat render URLs stay byte-identical (pinned below).
 *
 * The camera clamps follow the projection for the same reason: clamping a globe
 * camera's negative zoom to the flat floor of 0 would render a DIFFERENT picture
 * than the sharer's, which is the whole failure this change exists to fix.
 */
describe("screenshotUrlForState", () => {
  const state = {
    center: [-97.5, 39.2] as [number, number],
    zoom: 4.25,
    isMobile: false,
    model: "gfs",
    run: "12z",
    variable: { key: "tmp2m", label: "2 m Temp" },
    fh: 24,
    animationEnabled: false,
  } satisfies Partial<ScreenshotExportState> as ScreenshotExportState;

  it("adds only camera + frame params, and invents no projection param", () => {
    const permalink = "/viewer?model=gfs&var=tmp2m";
    const before = new URL(permalink, "https://cartosky.com");
    const after = new URL(screenshotUrlForState(permalink, state));

    const added = [...after.searchParams.keys()].filter((key) => !before.searchParams.has(key));
    expect(added.sort()).toEqual(["fh", "lat", "lon", "z"]);
    expect(added).not.toContain("proj");
    expect(added).not.toContain("projection");
    expect(added).not.toContain("globe");
  });

  it("passes a globe projection param through", () => {
    // The real input shape: the viewer's own share permalink, on the globe.
    const after = new URL(
      screenshotUrlForState("/viewer?model=gfs&var=tmp2m&proj=globe", state),
    );
    expect(after.searchParams.get("proj")).toBe("globe");
  });

  it("normalizes the projection param's case on the way out", () => {
    // `permalinkIsGlobe` reads case-insensitively, so `proj=GLOBE` is
    // recognized here; without a normalize it would then be forwarded verbatim
    // to a renderer that need not be as forgiving. Unreachable through the
    // viewer's own permalink writer (which only ever writes `globe`) — closed
    // because it is one line, not because it is live.
    for (const raw of ["GLOBE", "Globe", " globe "]) {
      const after = new URL(
        screenshotUrlForState(`/viewer?model=gfs&proj=${encodeURIComponent(raw)}`, state),
      );
      expect(after.searchParams.get("proj"), `proj=${raw}`).toBe("globe");
    }
  });

  it("strips a projection param that is not the globe", () => {
    // Pass-through is for the one projection this app renders. Anything else
    // (a future build's link, a hand-edited URL) degrades to the flat default
    // exactly the way readPermalink degrades it.
    const after = new URL(
      screenshotUrlForState("/viewer?model=gfs&proj=orthographic", state),
    );
    expect(after.searchParams.has("proj")).toBe(false);
  });

  it("keeps a globe camera at permalink range, negative zoom and all", () => {
    // Measured globe cameras. Clamping these into the flat range would frame a
    // different picture than the sharer's, and the whole point of the server
    // globe render is that it reproduces what the sharer saw.
    const polar = new URL(screenshotUrlForState("/viewer?m=gfs&proj=globe", {
      ...state, center: [10, 89.9], zoom: -6.8404,
    }));
    expect(polar.searchParams.get("proj")).toBe("globe");
    expect(polar.searchParams.get("z")).toBe("-6.84");
    expect(polar.searchParams.get("lat")).toBe("89.90000");

    const wholeDisc = new URL(screenshotUrlForState("/viewer?m=gfs&proj=globe", {
      ...state, center: [-40, 20], zoom: -0.0897,
    }));
    expect(wholeDisc.searchParams.get("z")).toBe("-0.09");
    expect(wholeDisc.searchParams.get("lat")).toBe("20.00000");
  });

  it("keeps the globe camera inside the range readPermalink accepts", () => {
    // The reader drops `z` below -10 and `lat` outside +-90 outright, which
    // would silently reframe the capture. Clamp, don't emit-and-lose.
    const extreme = new URL(screenshotUrlForState("/viewer?m=gfs&proj=globe", {
      ...state, center: [10, 120], zoom: -40,
    }));
    expect(extreme.searchParams.get("z")).toBe("-10.00");
    expect(extreme.searchParams.get("lat")).toBe("90.00000");
    expect(Number(extreme.searchParams.get("z"))).toBeGreaterThanOrEqual(-10);
    expect(Number(extreme.searchParams.get("lat"))).toBeLessThanOrEqual(90);
  });

  it("still clamps a flat camera into the flat-legal range", () => {
    // No proj means the flat renderer, which holds neither a negative zoom nor
    // a latitude past the Web Mercator limit.
    const flat = new URL(screenshotUrlForState("/viewer?m=gfs", {
      ...state, center: [10, 89.9], zoom: -6.8404,
    }));
    expect(flat.searchParams.get("z")).toBe("0.00");
    expect(Number(flat.searchParams.get("lat"))).toBeLessThanOrEqual(85.05112877980659);
  });

  it("is byte-identical for a flat camera", () => {
    // Both clamps and the strip must be identity on everything that exists
    // today: flat cameras are already within [0, 24] and +-85.051, and a flat
    // permalink has no proj to remove.
    const url = screenshotUrlForState("/viewer?model=gfs&var=tmp2m&reg=conus", state);
    expect(url).toBe(
      "https://cartosky.com/viewer?model=gfs&var=tmp2m&reg=conus"
      + "&lat=39.20000&lon=-97.50000&z=4.25&fh=24",
    );
  });

  it("preserves the permalink's own params and path", () => {
    const after = new URL(screenshotUrlForState("/viewer?model=gfs&var=tmp2m", state));
    expect(after.pathname).toBe("/viewer");
    expect(after.searchParams.get("model")).toBe("gfs");
    expect(after.searchParams.get("var")).toBe("tmp2m");
    expect(after.searchParams.get("fh")).toBe("24");
    expect(after.searchParams.get("lat")).toBe("39.20000");
    expect(after.searchParams.get("lon")).toBe("-97.50000");
    expect(after.searchParams.get("z")).toBe("4.25");
  });
});
