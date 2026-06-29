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
      icon: "partly-cloudy-day",
      short_text: "Hot",
    },
  ],
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
  attribution: { current: "NWS", hourly: "Open-Meteo", daily: "Open-Meteo" },
  freshness: {
    current: { state: "fresh", observed_at: "2026-06-29T14:38:00-05:00", age_minutes: 22 },
    afd: { state: "unavailable", issued_at: null, age_hours: null },
  },
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
    await expect(page.getByText("91°")).toBeVisible();
    await expect(page.getByText("Partly Cloudy")).toBeVisible();
    await expect(page.getByText("Dew Point")).toBeVisible();
    await expect(page.getByText("73°")).toBeVisible();
    await expect(page.getByText("Heat Advisory", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Hourly" }).click();

    await expect(page.getByRole("button", { name: "Hourly" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: "Current Conditions" })).toBeHidden();
    await expect(page.getByRole("heading", { name: "Live Radar" })).toBeHidden();
    await expect(page.getByText("Temperature · Next 24 Hours")).toBeVisible();
  });
});
