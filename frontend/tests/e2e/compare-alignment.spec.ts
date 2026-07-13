import { test, expect } from "@playwright/test";

import {
  alignedMutualGridHours,
  reanchorForecastHourOnSwap,
  runAlignmentOffsetHours,
} from "../../src/lib/compare-alignment";

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
