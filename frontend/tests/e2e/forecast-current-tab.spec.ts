import { expect, test } from "@playwright/test";

const FORECAST_PAYLOAD = {
  location: {
    display_name: "Sioux Falls, SD",
    latitude: 43.55,
    longitude: -96.73,
    timezone: "America/Chicago",
    country_code: "US",
    admin1: "South Dakota",
    resolved_by: "test",
  },
  source_status: { primary_region_mode: "nws", nws: "ready", open_meteo: "ready" },
  current: {
    source: "NWS",
    observed_at: "2026-06-29T14:38:00-05:00",
    station: { id: "KFSD", name: "Sioux Falls, Foss Field", distance_km: 4.2 },
    temperature_f: 91,
    dewpoint_f: 73,
    humidity_pct: 56,
    wind_dir_deg: 180,
    wind_speed_mph: 16,
    wind_gust_mph: 24,
    pressure_mb: 1000,
    visibility_mi: 10,
    icon: "partly-cloudy-day",
    short_text: "Partly Cloudy",
    quality: { is_fallback: false, is_stale: false, freshness: "fresh", age_minutes: 22 },
  },
  hourly: Array.from({ length: 24 }, (_, hour) => ({
    time: `2026-06-29T${String(hour).padStart(2, "0")}:00:00-05:00`,
    temperature_f: 88 + (hour % 8),
    pop_pct: hour % 4 === 0 ? 20 : 10,
    weather_code: "partly-cloudy-day",
    short_text: "Partly Cloudy",
    wind_speed_mph: 16,
    wind_dir_deg: 180,
  })),
  daily: [
    {
      date: "2026-06-29",
      high_f: 94,
      low_f: 73,
      pop_pct: 20,
      qpf_in: 0.02,
      snow_in: 0,
      wind_speed_mph: 16,
      wind_gust_mph: 24,
      sunrise: "2026-06-29T05:46:00-05:00",
      sunset: "2026-06-29T21:08:00-05:00",
      icon: "partly-cloudy-day",
      short_text: "Hot",
    },
  ],
  air_quality: {
    source: "open_meteo",
    observed_at: "2026-06-29T14:30:00-05:00",
    us_aqi: 42,
    category: "Good",
    color: "#3ecf6a",
    driver: {
      code: "pm2_5",
      label: "PM2.5",
      value: 11.2,
      unit: "μg/m³",
      aqi: 42,
    },
    pollutants: {
      pm2_5: 11.2,
      pm10: 18.7,
      ozone: 31.4,
      nitrogen_dioxide: 7.8,
    },
  },
  pollen: {
    source: "google_pollen",
    date: "2026-06-29",
    index: 4,
    category: "High",
    color: "#ffb423",
    dominant_type: "Tree",
    dominant_plant: "Oak",
    summary: "High tree pollen, moderate grass pollen.",
    types: [
      { code: "TREE", label: "Tree", category: "High", index: 4, in_season: true },
      { code: "GRASS", label: "Grass", category: "Moderate", index: 3, in_season: true },
      { code: "WEED", label: "Weed", category: "Very Low", index: 1, in_season: false },
    ],
  },
  official_text_forecast: null,
  afd: null,
  alerts: [
    {
      id: "heat-advisory",
      event: "Heat Advisory",
      severity: "Moderate",
      urgency: "Expected",
      effective: "2026-06-29T07:38:00-05:00",
      expires: "2026-06-29T21:00:00-05:00",
      headline: "Heat Advisory issued June 29 until 9:00 PM CDT by NWS Sioux Falls SD",
      areas: ["Lyon", "Osceola", "Dickinson"],
      description: "Heat index values up to 100 expected.",
    },
  ],
  attribution: {
    current: "NWS",
    hourly: "Open-Meteo",
    daily: "Open-Meteo",
    air_quality: "Open-Meteo",
    pollen: "Google Pollen API",
  },
  freshness: {
    current: { state: "fresh", observed_at: "2026-06-29T14:38:00-05:00", age_minutes: 22 },
    afd: { state: "unavailable", issued_at: null, age_hours: null },
  },
};

const OPEN_METEO_CORE_PAYLOAD = {
  ...FORECAST_PAYLOAD,
  source_status: { primary_region_mode: "us_hybrid", nws: "pending", open_meteo: "ok" },
  current: {
    ...FORECAST_PAYLOAD.current,
    source: "open_meteo",
    station: null,
    temperature_f: 89,
    short_text: "Mostly Sunny",
  },
  official_text_forecast: null,
  afd: null,
  alerts: [],
  attribution: {
    ...FORECAST_PAYLOAD.attribution,
    current: "Open-Meteo",
  },
};

const NWS_ENRICHED_PAYLOAD = {
  ...FORECAST_PAYLOAD,
  source_status: { primary_region_mode: "us_hybrid", nws: "ok", open_meteo: "ok" },
};

test.describe("Forecast current tab", () => {
  test("lands on Today and moves current conditions out of Hourly", async ({ page }) => {
    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      await route.fulfill({ status: 204, body: "" });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto("/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD");

    const tabs = page.locator("[data-forecast-tab]");
    await expect(tabs.first()).toHaveText("Today");
    await expect(page.getByRole("tab", { name: "Today" })).toHaveAttribute("aria-selected", "true");

    await expect(page.getByRole("heading", { name: "Current Conditions" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Live Radar" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sun" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Air Quality" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Pollen" })).toBeVisible();
    await expect(page.getByText("91°")).toBeVisible();
    await expect(page.getByText("Partly Cloudy")).toBeVisible();
    await expect(page.getByText("Dew Point")).toBeVisible();
    await expect(page.getByText("73°")).toBeVisible();
    await expect(page.getByText("5:46 AM")).toBeVisible();
    await expect(page.getByText("9:08 PM")).toBeVisible();
    await expect(page.getByText("15h 22m")).toBeVisible();
    await expect(page.getByText("42")).toBeVisible();
    await expect(page.getByText("Good", { exact: true })).toBeVisible();
    await expect(page.getByText("Air quality is considered satisfactory, and air pollution poses little or no risk.")).toBeVisible();
    const pollenCard = page.locator("section").filter({ has: page.getByRole("heading", { name: "Pollen" }) });
    await expect(pollenCard.getByText("4", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("Tree Pollen", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("High", { exact: true }).first()).toBeVisible();
    await expect(pollenCard.getByText("Grass Pollen", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("Moderate", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Heat Advisory", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "Hourly" }).click();

    await expect(page.getByRole("tab", { name: "Hourly" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: "Current Conditions" })).toBeHidden();
    await expect(page.getByRole("heading", { name: "Live Radar" })).toBeHidden();
    await expect(page.getByText("Temperature · Next 24 Hours")).toBeVisible();
  });

  test("mobile forecast tab rail hides native chrome and keeps the active tab visible", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      await route.fulfill({ status: 204, body: "" });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto("/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD");

    for (const target of [
      page.getByRole("button", { name: "Search for another location" }),
      page.getByRole("button", { name: "Save favorite" }),
      page.getByRole("button", { name: "Refresh forecast" }),
    ]) {
      const box = await target.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    const rail = page.getByRole("tablist", { name: "Forecast sections" });
    await expect(rail).toBeVisible();
    await expect(page.locator('[data-forecast-tab-fade="right"]')).toBeVisible();

    const today = rail.getByRole("tab", { name: "Today" });
    const hourly = rail.getByRole("tab", { name: "Hourly" });
    await expect(today).toHaveAttribute("tabindex", "0");
    await expect(hourly).toHaveAttribute("tabindex", "-1");
    await expect(today).toHaveAttribute("aria-controls", "forecast-panel");
    await today.press("ArrowRight");
    await expect(hourly).toHaveAttribute("aria-selected", "true");
    await expect(hourly).toHaveAttribute("tabindex", "0");
    await expect.poll(() => hourly.evaluate((element) => getComputedStyle(element).borderBottomColor)).toBe("rgb(103, 232, 249)");
    await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "forecast-tab-hourly");

    const tabHeights = await rail.getByRole("tab").evaluateAll((tabs) => (
      tabs.map((tab) => Math.round(tab.getBoundingClientRect().height))
    ));
    expect(tabHeights.every((height) => height >= 44)).toBe(true);
    await expect.poll(() => rail.evaluate((element) => getComputedStyle(element).scrollbarWidth)).toBe("none");

    const discussion = rail.getByRole("tab", { name: "Discussion" });
    await discussion.evaluate((element) => (element as HTMLButtonElement).click());
    await expect(discussion).toHaveAttribute("aria-selected", "true");
    await expect.poll(() => discussion.evaluate((element) => {
      const tabRect = element.getBoundingClientRect();
      const railRect = element.parentElement!.getBoundingClientRect();
      return tabRect.left >= railRect.left - 1 && tabRect.right <= railRect.right + 1;
    })).toBe(true);
    await expect(page.locator('[data-forecast-tab-fade="left"]')).toBeVisible();
  });

  test("model detail daily temperatures stay prominent on mobile and desktop", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => {
      const draws: Array<{
        canvasId: number;
        text: string;
        font: string;
        x: number;
        y: number;
        width: number;
        height: number;
        canvasWidth: number;
        canvasHeight: number;
      }> = [];
      const canvasIds = new WeakMap<HTMLCanvasElement, number>();
      let nextCanvasId = 1;
      const originalFillText = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
        if (/^-?\d+°$/.test(String(text))) {
          let canvasId = canvasIds.get(this.canvas);
          if (canvasId === undefined) {
            canvasId = nextCanvasId++;
            canvasIds.set(this.canvas, canvasId);
          }
          const transform = this.getTransform();
          const scaleX = Math.hypot(transform.a, transform.b) || 1;
          const scaleY = Math.hypot(transform.c, transform.d) || 1;
          const fontSize = Number.parseFloat(/(\d+(?:\.\d+)?)px/.exec(this.font)?.[1] ?? "0");
          draws.push({
            canvasId,
            text: String(text),
            font: this.font,
            x: transform.a * x + transform.c * y + transform.e,
            y: transform.b * x + transform.d * y + transform.f,
            width: this.measureText(String(text)).width * scaleX,
            height: fontSize * scaleY,
            canvasWidth: this.canvas.width,
            canvasHeight: this.canvas.height,
          });
        }
        if (maxWidth === undefined) {
          return originalFillText.call(this, text, x, y);
        }
        return originalFillText.call(this, text, x, y, maxWidth);
      };
      Object.defineProperty(window, "__modelDetailTempLabelDraws", {
        configurable: true,
        value: draws,
      });
    });

    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/{ecmwf,gfs,aifs,nbm}/runs", async (route) => {
      await route.fulfill({ json: ["20260720_06z"] });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      const request = route.request().postDataJSON() as {
        models: string[];
        variables: string[];
      };
      const tempPoints = Array.from({ length: 384 }, (_, fh) => {
        const day = Math.floor(fh / 24);
        const base = day === 5 ? 104 : 82 + (day % 4) * 2;
        return {
          fh,
          valid_time: new Date(Date.UTC(2026, 6, 20, 6 + fh)).toISOString(),
          value: base + Math.round(16 * Math.sin((fh / 24) * Math.PI * 2)),
        };
      });
      const variables = Object.fromEntries(
        request.variables.map((variable) => [
          variable,
          {
            units: variable === "tmp2m" ? "F" : variable === "wspd10m" ? "mph" : "in",
            points: variable === "tmp2m" ? tempPoints : [],
          },
        ]),
      );
      await route.fulfill({
        json: {
          location: { lat: 43.55, lon: -96.73 },
          generated_at: "2026-07-20T06:00:00Z",
          run_policy: { type: "latest_per_model" },
          series: Object.fromEntries(
            request.models.map((model) => [
              model,
              {
                status: "ok",
                run_id: "20260720_06z",
                latest_complete_run: "20260720_06z",
                variables,
              },
            ]),
          ),
        },
      });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto(
      "/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD&tab=models&section=detail&detail_model=ecmwf",
    );
    await expect(page.getByText("Daily high / low", { exact: true })).toBeVisible();

    const copyImageButton = page.getByRole("button", { name: "Copy image" });
    const downloadImageButton = page.getByRole("button", { name: "Download image" });
    const cardTitle = page.locator("h3").filter({ hasText: /^(ECMWF|GFS|AIFS|NBM)$/ }).first();
    const cardMetadata = cardTitle.locator("..");
    const chartCard = copyImageButton.locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-xl ')][1]",
    );
    await expect(copyImageButton).toBeVisible();
    await expect(downloadImageButton).toBeVisible();

    const expectExportActionsAtTopRight = async () => {
      const [cardBox, metadataBox, titleBox, copyBox, downloadBox] = await Promise.all([
        chartCard.boundingBox(),
        cardMetadata.boundingBox(),
        cardTitle.boundingBox(),
        copyImageButton.boundingBox(),
        downloadImageButton.boundingBox(),
      ]);
      expect(cardBox).not.toBeNull();
      expect(metadataBox).not.toBeNull();
      expect(titleBox).not.toBeNull();
      expect(copyBox).not.toBeNull();
      expect(downloadBox).not.toBeNull();
      expect(Math.abs(copyBox!.y - titleBox!.y)).toBeLessThanOrEqual(8);
      expect(downloadBox!.x).toBeGreaterThan(copyBox!.x);
      expect(copyBox!.x - (metadataBox!.x + metadataBox!.width)).toBeGreaterThanOrEqual(8);
      const rightInset = cardBox!.x + cardBox!.width - (downloadBox!.x + downloadBox!.width);
      expect(rightInset).toBeGreaterThanOrEqual(12);
      expect(rightInset).toBeLessThanOrEqual(24);
    };
    await expectExportActionsAtTopRight();

    type TempLabelDraw = {
      canvasId: number;
      text: string;
      font: string;
      x: number;
      y: number;
      width: number;
      height: number;
      canvasWidth: number;
      canvasHeight: number;
    };
    const recordedDraws = async () => page.evaluate(() => (
      window as Window & { __modelDetailTempLabelDraws?: TempLabelDraw[] }
    ).__modelDetailTempLabelDraws ?? []);
    const latestLayout = (draws: TempLabelDraw[], fontSize: number) => {
      const matching = draws.filter(({ font }) => font.includes(`${fontSize}px`));
      const latestCanvasId = Math.max(...matching.map(({ canvasId }) => canvasId));
      const unique = new Map<string, TempLabelDraw>();
      for (const draw of matching.filter(({ canvasId }) => canvasId === latestCanvasId)) {
        unique.set(`${draw.text}:${Math.round(draw.x)}:${Math.round(draw.y)}`, draw);
      }
      const labels = [...unique.values()];
      const slots = new Map<number, number>();
      for (const label of labels) {
        const center = Math.round(label.x);
        slots.set(center, Math.max(slots.get(center) ?? 0, label.width));
      }
      const centers = [...slots].sort(([left], [right]) => left - right);
      const outOfBounds = labels.filter((label) => !(
        label.x - label.width / 2 >= 0
        && label.x + label.width / 2 <= label.canvasWidth
        && label.y - label.height >= 0
        && label.y + 2 <= label.canvasHeight
      ));
      const collisions = centers.flatMap(([center, width], index) => {
        const next = centers[index + 1];
        return next !== undefined && next[0] - center < (width + next[1]) / 2 + 4
          ? [{ center, width, nextCenter: next[0], nextWidth: next[1] }]
          : [];
      });
      return {
        labels,
        centers,
        outOfBounds,
        collisions,
      };
    };

    await expect.poll(async () => latestLayout(await recordedDraws(), 20).labels.length).toBeGreaterThan(20);
    const mobileLayout = latestLayout(await recordedDraws(), 20);
    expect(mobileLayout.labels.every(({ font }) => (
      /^(?:bold|700) 20px ui-sans-serif, system-ui, sans-serif$/.test(font)
    ))).toBe(true);
    expect(mobileLayout.outOfBounds).toEqual([]);
    expect(mobileLayout.collisions).toEqual([]);
    const chartScroller = page.locator('[data-model-detail-charts]');
    await expect(chartScroller).toBeVisible();
    expect(await chartScroller.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    expect(await chartScroller.evaluate((element) => element.scrollWidth)).toBeGreaterThanOrEqual(1000);
    expect(await chartScroller.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
      return element.scrollLeft > 0;
    })).toBe(true);

    await page.setViewportSize({ width: 1100, height: 900 });
    await expectExportActionsAtTopRight();
    await expect.poll(async () => latestLayout(await recordedDraws(), 15).labels.length).toBeGreaterThan(20);
    const desktopLayout = latestLayout(await recordedDraws(), 15);
    expect(desktopLayout.labels.every(({ font }) => font.startsWith("600 "))).toBe(true);
    expect(desktopLayout.outOfBounds).toEqual([]);
    expect(desktopLayout.collisions).toEqual([]);
    await expect.poll(() => chartScroller.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    expect(await chartScroller.evaluate((element) => {
      const canvas = element.querySelector("canvas");
      return canvas !== null && Math.abs(canvas.clientWidth - element.clientWidth) <= 1;
    })).toBe(true);
  });

  test("retries transient NWS-unavailable enrichment when a hidden tab becomes visible", async ({ page }) => {
    await page.addInitScript(() => {
      let state: DocumentVisibilityState = "hidden";
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => state,
      });
      Object.defineProperty(document, "hidden", {
        configurable: true,
        get: () => state !== "visible",
      });
      Object.defineProperty(window, "__setForecastVisibility", {
        configurable: true,
        value: (nextState: DocumentVisibilityState) => {
          state = nextState;
          document.dispatchEvent(new Event("visibilitychange"));
        },
      });
    });

    let enrichmentRequests = 0;
    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: OPEN_METEO_CORE_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      enrichmentRequests += 1;
      if (enrichmentRequests === 1) {
        await route.fulfill({
          json: {
            ...OPEN_METEO_CORE_PAYLOAD,
            source_status: {
              ...OPEN_METEO_CORE_PAYLOAD.source_status,
              nws: "unavailable",
            },
          },
        });
        return;
      }
      await route.fulfill({ json: NWS_ENRICHED_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      await route.fulfill({ status: 204, body: "" });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto("/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD");

    await expect(page.getByText("Open-Meteo", { exact: true })).toBeVisible();
    await expect.poll(() => enrichmentRequests).toBe(1);

    await page.evaluate(() => {
      const setVisibility = (window as Window & {
        __setForecastVisibility?: (state: DocumentVisibilityState) => void;
      }).__setForecastVisibility;
      setVisibility?.("visible");
    });

    await expect.poll(() => enrichmentRequests).toBe(2);
    await expect(page.getByText("NWS · Sioux Falls, Foss Field · 4.2 km", { exact: true })).toBeVisible();
  });

  test("retries degraded Open-Meteo current conditions after the degraded cache expires", async ({ page }) => {
    await page.addInitScript(() => {
      let state: DocumentVisibilityState = "hidden";
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => state,
      });
      Object.defineProperty(document, "hidden", {
        configurable: true,
        get: () => state !== "visible",
      });
      Object.defineProperty(window, "__setForecastVisibility", {
        configurable: true,
        value: (nextState: DocumentVisibilityState) => {
          state = nextState;
          document.dispatchEvent(new Event("visibilitychange"));
        },
      });
    });

    let enrichmentRequests = 0;
    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: OPEN_METEO_CORE_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      enrichmentRequests += 1;
      if (enrichmentRequests === 1) {
        await route.fulfill({
          json: {
            ...OPEN_METEO_CORE_PAYLOAD,
            source_status: {
              ...OPEN_METEO_CORE_PAYLOAD.source_status,
              nws: "degraded",
            },
          },
        });
        return;
      }
      await route.fulfill({ json: NWS_ENRICHED_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      await route.fulfill({ status: 204, body: "" });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto("/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD");

    await expect(page.getByText("Open-Meteo", { exact: true })).toBeVisible();
    await expect.poll(() => enrichmentRequests).toBe(1);

    await page.evaluate(() => {
      const resumedAt = Date.now() + 66_000;
      Date.now = () => resumedAt;
      const setVisibility = (window as Window & {
        __setForecastVisibility?: (state: DocumentVisibilityState) => void;
      }).__setForecastVisibility;
      setVisibility?.("visible");
    });

    await expect.poll(() => enrichmentRequests).toBe(2);
    await expect(page.getByText("NWS · Sioux Falls, Foss Field · 4.2 km", { exact: true })).toBeVisible();
  });

  test("temperature member view omits the probability chart and its data requests", async ({ page }) => {
    let temperatureProbabilityRequests = 0;
    let precipitationProbabilityRequests = 0;

    await page.route("**/api/v4/forecast-page/core**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/forecast-page?**", async (route) => {
      await route.fulfill({ json: FORECAST_PAYLOAD });
    });
    await page.route("**/api/v4/capabilities", async (route) => {
      await route.fulfill({ json: { supported_models: [], model_catalog: {}, availability: {} } });
    });
    await page.route("**/api/regions", async (route) => {
      await route.fulfill({ json: { regions: {} } });
    });
    await page.route("**/api/v4/{eps,gefs}/runs", async (route) => {
      await route.fulfill({ json: ["20260720_00z"] });
    });
    await page.route("**/api/v4/forecast/meteogram", async (route) => {
      const request = route.request().postDataJSON() as {
        models: string[];
        variables: string[];
      };
      if (
        request.variables.some((variable) =>
          variable.startsWith("tmp2m__prob_"),
        )
      ) {
        temperatureProbabilityRequests += 1;
      }
      if (
        request.variables.some((variable) =>
          variable.startsWith("precip_total__prob_"),
        )
      ) {
        precipitationProbabilityRequests += 1;
      }

      const variables = Object.fromEntries(
        request.variables.map((variable) => [
          variable,
          {
            units: variable.startsWith("tmp2m") ? "F" : "in",
            points: [
              { fh: 0, valid_time: "2026-07-20T00:00:00Z", value: 50 },
              { fh: 6, valid_time: "2026-07-20T06:00:00Z", value: 55 },
            ],
          },
        ]),
      );
      await route.fulfill({
        json: {
          location: { lat: 43.55, lon: -96.73 },
          generated_at: "2026-07-20T00:00:00Z",
          run_policy: { type: "latest_per_model" },
          series: Object.fromEntries(
            request.models.map((model) => [
              model,
              {
                status: "ok",
                run_id: "20260720_00z",
                latest_complete_run: "20260720_00z",
                variables,
              },
            ]),
          ),
        },
      });
    });
    await page.route("**/api/v4/mrms/latest/reflectivity/**", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });

    await page.goto(
      "/forecast?lat=43.55&lon=-96.73&name=Sioux%20Falls%2C%20SD&tab=ensembles&ensemble_view=gefs",
    );

    await expect(page.getByRole("heading", { name: "GEFS temperature percentiles" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "GEFS temperature probabilities" })).toHaveCount(0);
    await expect.poll(() => temperatureProbabilityRequests).toBe(0);

    await page.getByRole("combobox", { name: "Ensemble view" }).click();
    await page.getByRole("option", { name: "EPS members" }).click();
    await expect(page.getByRole("heading", { name: "EPS temperature percentiles" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "EPS temperature probabilities" })).toHaveCount(0);
    await expect.poll(() => temperatureProbabilityRequests).toBe(0);

    await page.getByRole("combobox", { name: "Ensemble variable" }).click();
    await page.getByRole("option", { name: "Precipitation" }).click();

    await expect(page.getByRole("heading", { name: "EPS precipitation probabilities" })).toBeVisible();
    await expect.poll(() => precipitationProbabilityRequests).toBeGreaterThan(0);
  });
});
