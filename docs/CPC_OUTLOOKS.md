# CPC Outlooks

CartoSky publishes the four initial NOAA Climate Prediction Center outlook overlays from the official NWS ArcGIS vector services:

- `cpc_610_temp`: CPC 6-10 Day Temperature Outlook, layer `0` from `outlooks/cpc_6_10_day_outlk`
- `cpc_610_precip`: CPC 6-10 Day Precipitation Outlook, layer `1` from `outlooks/cpc_6_10_day_outlk`
- `cpc_814_temp`: CPC 8-14 Day Temperature Outlook, layer `0` from `outlooks/cpc_8_14_day_outlk`
- `cpc_814_precip`: CPC 8-14 Day Precipitation Outlook, layer `1` from `outlooks/cpc_8_14_day_outlk`

The services support GeoJSON query output and are updated daily around 1500 Eastern Time, so they are preferred over CPC static images or image scraping. CPC shapefile pages remain the documented fallback source if the ArcGIS vector service changes.

The publish path normalizes CPC fields into CartoSky vector sidecars:

- `cat` -> `category`, `label`, `displayLabel`
- `prob` -> `probability`
- `start_date` / `end_date` -> `valid_start` / `valid_end`
- `idp_filedate` or `fcst_date` -> `issued_at`

CPC outlooks are official outlook products with one valid-time frame per product. They use `time_axis_mode: valid`, `latest_only: true`, and vector rendering; no forecast-hour model stepping or fake forecast hours are introduced.

Refresh command:

```bash
python -m app.services.cpc_poller --once --data-root /opt/cartosky/data
```

If an upstream refresh fails, the poller logs the failure and leaves the existing `published/cpc/LATEST.json` untouched, so the API continues serving the last known good bundle.
