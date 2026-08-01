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
 * 8d: the server-side screenshot render stays mercator — nothing about the
 * client's projection is forwarded to it.
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
 * The added-keys assertion below is necessary but WAS NOT SUFFICIENT, and this
 * is the correction: an earlier revision reasoned that a permalink handed in
 * with `proj=` already set keeps it, and called that "a permalink-side concern,
 * not this function's". That was wrong. App.tsx builds the share permalink with
 * `globeProjection: globeProjectionEnabled`, so the permalink fed to this
 * function DOES carry `proj=globe` whenever the sharer is on the globe — and
 * the server render, which is flat by design, was silently being asked for a
 * globe. So the pin is now on the OUTPUT: no projection param, whatever came in.
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

  it("adds only camera + frame params — never a projection param", () => {
    const permalink = "/viewer?model=gfs&var=tmp2m";
    const before = new URL(permalink, "https://cartosky.com");
    const after = new URL(screenshotUrlForState(permalink, state));

    const added = [...after.searchParams.keys()].filter((key) => !before.searchParams.has(key));
    expect(added.sort()).toEqual(["fh", "lat", "lon", "z"]);
    expect(added).not.toContain("proj");
    expect(added).not.toContain("projection");
    expect(added).not.toContain("globe");
  });

  it("strips a projection param the permalink brought with it", () => {
    // The real input shape: the viewer's own share permalink, on the globe.
    const after = new URL(
      screenshotUrlForState("/viewer?model=gfs&var=tmp2m&proj=globe", state),
    );
    expect(after.searchParams.has("proj")).toBe(false);
    expect(after.toString()).not.toContain("proj");
  });

  it("clamps a globe camera into the flat-legal range", () => {
    // The server render is flat, so a latitude-adjusted globe zoom (negative)
    // and a polar centre are not cameras it can hold. Both measured values.
    const polar = new URL(screenshotUrlForState("/viewer?m=gfs&proj=globe", {
      ...state, center: [10, 89.9], zoom: -6.8404,
    }));
    expect(Number(polar.searchParams.get("z"))).toBeGreaterThanOrEqual(0);
    expect(polar.searchParams.get("z")).toBe("0.00");
    expect(Number(polar.searchParams.get("lat"))).toBeLessThanOrEqual(85.05112877980659);
    expect(polar.searchParams.has("proj")).toBe(false);

    const wholeDisc = new URL(screenshotUrlForState("/viewer?m=gfs", {
      ...state, center: [-40, 20], zoom: -0.0897,
    }));
    expect(wholeDisc.searchParams.get("z")).toBe("0.00");
    expect(wholeDisc.searchParams.get("lat")).toBe("20.00000");
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
