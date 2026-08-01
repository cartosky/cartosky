import { expect, test, type Locator, type Page } from "@playwright/test";

import { isNewFeature, type NewFeatureId } from "@/lib/new-features";
import {
  SOUNDING_MODEL,
  SOUNDING_VARIABLE,
  stubSoundingRoutes,
  type SoundingRequestLog,
} from "./sounding-panel.fixtures";

const VIEWER_URL = `/viewer?m=${SOUNDING_MODEL}&r=latest&v=${SOUNDING_VARIABLE}&fh=0&reg=conus`;

async function openViewer(page: Page) {
  const requests: SoundingRequestLog = [];
  await page.addInitScript(() => localStorage.setItem("csky_viewer_tour_v1", "completed"));
  await stubSoundingRoutes(page, requests);
  await page.goto(VIEWER_URL);
  await expect(page.getByTestId("viewer-initial-map-scrim")).toBeHidden({ timeout: 60_000 });
}

function rgbChannels(value: string): [number, number, number] {
  const channels = value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  expect(channels, `expected an RGB color, received ${value}`).toHaveLength(3);
  return channels as [number, number, number];
}

async function expectBadgeState(anchor: Locator, feature: NewFeatureId) {
  await anchor.scrollIntoViewIfNeeded();
  await expect(anchor).toBeVisible();
  const badge = anchor.locator(`[data-new-feature="${feature}"]`);
  if (!isNewFeature(feature)) {
    await expect(badge).toHaveCount(0);
    return;
  }

  await expect(badge).toHaveText("New");

  const styles = await badge.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      position: computed.position,
      pointerEvents: computed.pointerEvents,
      color: computed.color,
      backgroundColor: computed.backgroundColor,
    };
  });
  expect(styles.position).toBe("absolute");
  expect(styles.pointerEvents).toBe("none");
  expect(styles.color).not.toBe("rgba(0, 0, 0, 0)");
  expect(styles.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
  const [textRed, textGreen, textBlue] = rgbChannels(styles.color);
  const [backgroundRed, backgroundGreen, backgroundBlue] = rgbChannels(styles.backgroundColor);
  expect(textGreen).toBeGreaterThan(textRed);
  expect(textBlue).toBeGreaterThan(textRed);
  expect(backgroundGreen).toBeGreaterThan(backgroundRed);
  expect(backgroundBlue).toBeGreaterThan(backgroundRed);

  const anchorBox = await anchor.boundingBox();
  const badgeBox = await badge.boundingBox();
  expect(anchorBox).not.toBeNull();
  expect(badgeBox).not.toBeNull();
  expect(badgeBox!.y, `${feature} badge should float above its control`).toBeLessThan(anchorBox!.y);
  expect(badgeBox!.y + badgeBox!.height, `${feature} badge should overlap its control`).toBeGreaterThan(anchorBox!.y);
  expect(badgeBox!.x, `${feature} badge should attach horizontally`).toBeLessThan(anchorBox!.x + anchorBox!.width);
  expect(badgeBox!.x + badgeBox!.width, `${feature} badge should attach horizontally`).toBeGreaterThan(anchorBox!.x);
}

test.describe("globally managed New feature badges", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "Pinned Chromium visual contract.");

  test("follows the global registry over Sounding and Globe view on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openViewer(page);

    await expectBadgeState(page.getByTestId("sounding-toggle"), "sounding");
    await expectBadgeState(
      page.getByTestId("rail-toggle-globe-view").locator("button").first(),
      "globe-view",
    );
  });

  test("follows the global registry over Sounding and Globe view on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openViewer(page);

    await page.getByTestId("mobile-bar-overflow-trigger").click();
    await expectBadgeState(page.getByTestId("sounding-toggle"), "sounding");
    await page.keyboard.press("Escape");

    await page.getByTestId("mobile-sheet-handle").click();
    await expect(page.getByTestId("mobile-sheet")).toHaveAttribute("data-snap", "half");
    await expectBadgeState(
      page.getByTestId("mobile-toggle-globe-view").locator("button").first(),
      "globe-view",
    );
  });
});
