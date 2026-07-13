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
  test("lands on Current and moves current conditions out of Hourly", async ({ page }) => {
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
    await expect(tabs.first()).toHaveText("Current");
    await expect(page.getByRole("button", { name: "Current" })).toHaveAttribute("aria-selected", "true");

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
    await expect(page.getByText("PM2.5")).toBeVisible();
    await expect(page.getByText("11.2 μg/m³")).toBeVisible();
    const pollenCard = page.locator("section").filter({ has: page.getByRole("heading", { name: "Pollen" }) });
    await expect(pollenCard.getByText("4", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("Tree Pollen", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("High", { exact: true }).first()).toBeVisible();
    await expect(pollenCard.getByText("Grass Pollen", { exact: true })).toBeVisible();
    await expect(pollenCard.getByText("Moderate", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Heat Advisory", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Hourly" }).click();

    await expect(page.getByRole("button", { name: "Hourly" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: "Current Conditions" })).toBeHidden();
    await expect(page.getByRole("heading", { name: "Live Radar" })).toBeHidden();
    await expect(page.getByText("Temperature · Next 24 Hours")).toBeVisible();
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
});
