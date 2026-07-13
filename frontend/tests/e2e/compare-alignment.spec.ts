import { test, expect } from "@playwright/test";

import {
  alignedMutualGridHours,
  gridManifestMatchesSelection,
  reanchorForecastHourOnSwap,
  runAlignmentOffsetHours,
  shouldExposeCompareDiff,
} from "../../src/lib/compare-alignment";
import {
  ENSEMBLE_MEAN_VARIABLES,
  ENSEMBLES_TAB_VARIABLES,
  ensembleProbabilityRequestVariables,
  resolveEnsembleStatsRun,
} from "../../src/lib/chart-constants";

test("compare swap re-anchors forecast hour across offset runs", () => {
  const leftRun = "20260710_12z";
  const rightRun = "20260710_00z";
  const offset = runAlignmentOffsetHours(leftRun, rightRun);
  expect(offset).toBe(12);

  const fhBeforeSwap = 6;
  const fhAfterSwap = reanchorForecastHourOnSwap(fhBeforeSwap, offset);
  expect(fhAfterSwap).toBe(18);

  const swappedOffset = runAlignmentOffsetHours(rightRun, leftRun);
  expect(swappedOffset).toBe(-12);

  const leftHours = [0, 6, 12, 18, 24];
  const rightHours = [0, 6, 12, 18, 24];
  const mutualBefore = alignedMutualGridHours(leftHours, rightHours, offset);
  expect(mutualBefore).toEqual([0, 6, 12]);
  const mutualAfter = alignedMutualGridHours(rightHours, leftHours, swappedOffset);
  expect(mutualAfter).toEqual([12, 18, 24]);
  expect(mutualAfter).toContain(fhAfterSwap);
});

test("grid manifests must match model, concrete run, and variable", () => {
  const manifest = { model: "gfs", run: "20260710_12z", var: "tmp2m" };
  expect(gridManifestMatchesSelection(manifest, "gfs", "20260710_12z", "tmp2m")).toBe(true);
  expect(gridManifestMatchesSelection(manifest, "eps", "20260710_12z", "tmp2m")).toBe(false);
  expect(gridManifestMatchesSelection(manifest, "gfs", "20260710_00z", "tmp2m")).toBe(false);
  expect(gridManifestMatchesSelection(manifest, "gfs", "latest", "tmp2m")).toBe(false);
  expect(gridManifestMatchesSelection(manifest, "gfs", "20260710_12z", "precip_total")).toBe(false);
});

test("stale diff output is synchronously hidden when inputs are not current", () => {
  const ready = {
    enabled: true,
    leftFrameUrl: "left.bin",
    rightFrameUrl: "right.bin",
    leftGridMeta: {},
    rightGridMeta: {},
    varKey: "tmp2m",
  };
  expect(shouldExposeCompareDiff(ready)).toBe(true);
  expect(shouldExposeCompareDiff({ ...ready, enabled: false })).toBe(false);
  expect(shouldExposeCompareDiff({ ...ready, leftFrameUrl: null })).toBe(false);
});

test("probability requests stay within the six-variable contract", () => {
  const precipGt = ensembleProbabilityRequestVariables("precip_total", "gt");
  expect(precipGt).toHaveLength(6);
  expect(precipGt).not.toContain("precip_total");
});

test("ensemble stats follow the member run actually served, not a stale pin", () => {
  expect(resolveEnsembleStatsRun("20260710_00z", "20260710_06z")).toBe("20260710_06z");
  expect(resolveEnsembleStatsRun("20260710_00z", null)).toBeNull();
});

test("ensemble member variables include 850 mb temperature", () => {
  expect(ENSEMBLES_TAB_VARIABLES).toEqual(["tmp2m", "tmp850", "precip_total"]);
  expect(ENSEMBLE_MEAN_VARIABLES).toEqual(["tmp2m", "precip_total"]);
  expect(ENSEMBLE_MEAN_VARIABLES).not.toContain("tmp850");
});
