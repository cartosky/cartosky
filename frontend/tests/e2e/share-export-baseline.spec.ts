import { expect, test } from "@playwright/test";

import {
  GIF_EXPORT_FIXTURE,
  STILL_EXPORT_FIXTURES,
  type FixtureCorner,
} from "./share-export-baseline.fixtures";

const SCREENSHOT_EXPORT_MODULE = "/src/lib/screenshot_export.ts";
const GIF_WORKER_MODULE = "/src/lib/gif_encode_worker.ts";
const LOGO_ROUTE = "**/assets/new_logo.png*";

function skipOutsidePinnedRenderer(): void {
  test.skip(
    test.info().project.name !== "chromium",
    "The export baseline is pinned to desktop Chromium with SwiftShader.",
  );
}

for (const fixture of STILL_EXPORT_FIXTURES) {
  test(`${fixture.id} still export preserves exact crop-boundary RGBA`, async ({ page }) => {
    skipOutsidePinnedRenderer();
    await page.route(LOGO_ROUTE, (route) => route.abort());
    await page.goto(SCREENSHOT_EXPORT_MODULE);

    const result = await page.evaluate(async ({ fixture, modulePath }) => {
      const screenshotExport = await import(/* @vite-ignore */ modulePath);
      const source = document.createElement("canvas");
      source.width = fixture.frame.width;
      source.height = fixture.frame.height;
      const sourceContext = source.getContext("2d");
      if (!sourceContext) {
        throw new Error("Unable to create source fixture canvas.");
      }

      const image = sourceContext.createImageData(source.width, source.height);
      for (let offset = 0; offset < image.data.length; offset += 4) {
        image.data.set(fixture.frame.fill, offset);
      }
      for (const marker of fixture.frame.markers) {
        const offset = (marker.y * source.width + marker.x) * 4;
        image.data.set(marker.rgba, offset);
      }
      sourceContext.putImageData(image, 0, 0);

      const manifest = fixture.manifest;
      const blob = await screenshotExport.exportViewerScreenshotPng(
        {
          center: [-98.58, 39.83],
          zoom: 4,
          viewportWidth: fixture.frame.width,
          viewportHeight: fixture.frame.height,
          isMobile: false,
          model: manifest.model,
          run: manifest.run,
          variable: manifest.variable,
          fh: manifest.fh,
          region: manifest.region,
          animationEnabled: false,
          capturedMapDataUrl: source.toDataURL("image/png"),
        },
        {
          width: fixture.output.width,
          height: fixture.output.height,
          pixelRatio: 1,
          overlayLines: [],
        },
      );

      const bitmap = await createImageBitmap(blob);
      const decoded = document.createElement("canvas");
      decoded.width = bitmap.width;
      decoded.height = bitmap.height;
      const decodedContext = decoded.getContext("2d", { willReadFrequently: true });
      if (!decodedContext) {
        bitmap.close();
        throw new Error("Unable to decode exported PNG.");
      }
      decodedContext.drawImage(bitmap, 0, 0);
      bitmap.close();

      const pixel = (x: number, y: number) =>
        Array.from(decodedContext.getImageData(x, y, 1, 1).data);

      return {
        width: decoded.width,
        height: decoded.height,
        corners: {
          topLeft: pixel(0, 0),
          topRight: pixel(decoded.width - 1, 0),
          bottomLeft: pixel(0, decoded.height - 1),
          bottomRight: pixel(decoded.width - 1, decoded.height - 1),
        },
      };
    }, { fixture, modulePath: SCREENSHOT_EXPORT_MODULE });

    expect({ width: result.width, height: result.height }).toEqual({
      width: fixture.output.width,
      height: fixture.output.height,
    });
    for (const corner of Object.keys(fixture.output.expectedCorners) as FixtureCorner[]) {
      expect(result.corners[corner], `${fixture.id} ${corner}`).toEqual(
        fixture.output.expectedCorners[corner],
      );
    }
  });
}

test("still export derives 1280x720 from the recorded 16:9 map container", async ({ page }) => {
  skipOutsidePinnedRenderer();
  await page.route(LOGO_ROUTE, (route) => route.abort());
  await page.goto(SCREENSHOT_EXPORT_MODULE);

  const dimensions = await page.evaluate(async (modulePath) => {
    const screenshotExport = await import(/* @vite-ignore */ modulePath);
    const source = document.createElement("canvas");
    source.width = 16;
    source.height = 9;
    const context = source.getContext("2d");
    if (!context) {
      throw new Error("Unable to create aspect fixture canvas.");
    }
    context.fillStyle = "rgb(17, 31, 47)";
    context.fillRect(0, 0, source.width, source.height);

    const blob = await screenshotExport.exportViewerScreenshotPng(
      {
        center: [-98.58, 39.83],
        zoom: 4,
        viewportWidth: 1600,
        viewportHeight: 900,
        isMobile: false,
        model: "GFS",
        run: "20260728_12z",
        variable: { key: "tmp2m", label: "Surface Temperature" },
        fh: 24,
        region: { id: "conus", label: "CONUS" },
        animationEnabled: false,
        capturedMapDataUrl: source.toDataURL("image/png"),
      },
      { pixelRatio: 1, overlayLines: [] },
    );
    const bitmap = await createImageBitmap(blob);
    const result = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return result;
  }, SCREENSHOT_EXPORT_MODULE);

  expect(dimensions).toEqual({ width: 1280, height: 720 });
});

test("fixed two-frame GIF export matches the pinned binary baseline", async ({ page }) => {
  skipOutsidePinnedRenderer();
  await page.route(LOGO_ROUTE, (route) => route.abort());
  await page.goto(SCREENSHOT_EXPORT_MODULE);

  const result = await page.evaluate(async ({ fixture, screenshotModule, workerModule }) => {
    const screenshotExport = await import(/* @vite-ignore */ screenshotModule);

    const composedFrames: ArrayBuffer[] = [];
    for (const frame of fixture.frames) {
      const source = document.createElement("canvas");
      source.width = fixture.width;
      source.height = fixture.height;
      const sourceContext = source.getContext("2d");
      if (!sourceContext) {
        throw new Error("Unable to create GIF fixture canvas.");
      }
      const sourcePixels = sourceContext.createImageData(source.width, source.height);
      for (let y = 0; y < source.height; y += 1) {
        for (let x = 0; x < source.width; x += 1) {
          const rgba = x < source.width / 2 ? frame.left : frame.right;
          sourcePixels.data.set(rgba, (y * source.width + x) * 4);
        }
      }
      sourceContext.putImageData(sourcePixels, 0, 0);

      const output = document.createElement("canvas");
      await screenshotExport.composeShareFrame(output, source, {
        width: fixture.width,
        height: fixture.height,
        pixelRatio: 1,
        overlayLines: [],
        isMobile: false,
        chromeShadows: false,
      });
      const outputContext = output.getContext("2d");
      if (!outputContext) {
        throw new Error("Unable to read composed GIF fixture frame.");
      }
      composedFrames.push(
        outputContext.getImageData(0, 0, fixture.width, fixture.height).data.buffer,
      );
    }

    const worker = new Worker(
      new URL(workerModule, window.location.origin),
      { type: "module" },
    );
    const encoded = await new Promise<{ buffer: ArrayBuffer; acknowledgements: number[] }>(
      (resolve, reject) => {
        const acknowledgements: number[] = [];
        worker.onerror = () => reject(new Error("GIF worker failed."));
        worker.onmessage = (event) => {
          const message = event.data;
          if (message.type === "frame-encoded") {
            acknowledgements.push(message.index);
          } else if (message.type === "error") {
            reject(new Error(message.message));
          } else if (message.type === "done") {
            resolve({ buffer: message.buffer, acknowledgements });
          }
        };

        worker.postMessage({ type: "start", width: fixture.width, height: fixture.height });
        composedFrames.forEach((buffer, index) => {
          worker.postMessage(
            { type: "frame", buffer, delay: fixture.delayMs, index },
            [buffer],
          );
        });
        worker.postMessage({ type: "finish" });
      },
    );
    worker.terminate();

    const bytes = new Uint8Array(encoded.buffer);
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const sha256 = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
    const header = String.fromCharCode(...bytes.slice(0, 6));

    return {
      acknowledgements: encoded.acknowledgements,
      header,
      width: bytes[6] | (bytes[7] << 8),
      height: bytes[8] | (bytes[9] << 8),
      trailer: bytes.at(-1),
      byteLength: bytes.byteLength,
      sha256,
    };
  }, {
    fixture: GIF_EXPORT_FIXTURE,
    screenshotModule: SCREENSHOT_EXPORT_MODULE,
    workerModule: GIF_WORKER_MODULE,
  });

  expect(result.acknowledgements).toHaveLength(GIF_EXPORT_FIXTURE.expectedFrameCount);
  expect(result.acknowledgements).toEqual(
    GIF_EXPORT_FIXTURE.frames.map((_frame, index) => index),
  );
  expect(result.header).toBe("GIF89a");
  expect({ width: result.width, height: result.height }).toEqual({
    width: GIF_EXPORT_FIXTURE.width,
    height: GIF_EXPORT_FIXTURE.height,
  });
  expect(result.trailer).toBe(0x3b);
  expect(result.byteLength).toBeGreaterThan(40);
  expect(result.sha256).toBe(GIF_EXPORT_FIXTURE.expectedSha256);
});
