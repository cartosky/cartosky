#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "(CartoSky hazards-zone-builder, admin@cartosky.com)"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_CONCURRENCY = 24
MAX_RETRIES = 1
RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

LOGGER = logging.getLogger("build_hazard_zone_reference")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_output_path() -> Path:
    return _repo_root() / "data" / "hazards" / "zone_reference.geojson"


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            await asyncio.sleep(1.0)
        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            last_error = exc
            continue
        except httpx.RequestError as exc:
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc

        if response.status_code == 200:
            return response.json()
        if response.status_code in RETRYABLE_STATUS_CODES:
            last_error = RuntimeError(f"Transient HTTP {response.status_code} for {url}")
            continue
        raise RuntimeError(f"HTTP {response.status_code} for {url}")
    raise RuntimeError(f"Request failed after retries for {url}: {last_error}")


async def _fetch_zone_detail(
    client: httpx.AsyncClient,
    zone_code: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    url = f"{NWS_API_BASE}/zones/forecast/{zone_code}"
    async with semaphore:
        try:
            payload = await _get_json(client, url)
        except RuntimeError as exc:
            LOGGER.warning("Skipping zone %s: %s", zone_code, exc)
            return None

    geometry = payload.get("geometry")
    props = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(geometry, dict) or not isinstance(props, dict):
        return None
    return {
        "type": "Feature",
        "properties": {
            "zone_code": str(props.get("id") or zone_code).strip().upper(),
            "name": str(props.get("name") or zone_code).strip(),
            "state": str(props.get("state") or "").strip(),
            "zone_type": str(props.get("type") or "").strip(),
            "cwa": str(props.get("cwa") or "").strip(),
            "time_zone": str(props.get("timeZone") or "").strip(),
        },
        "geometry": geometry,
    }


async def _build_zone_reference(*, timeout_seconds: float, concurrency: int) -> dict[str, Any]:
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
    }
    timeout = httpx.Timeout(timeout_seconds)
    limits = httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True, limits=limits) as client:
        listing = await _get_json(client, f"{NWS_API_BASE}/zones/forecast")
        features = listing.get("features") if isinstance(listing, dict) else None
        if not isinstance(features, list):
            raise RuntimeError("NWS zones listing did not include features")
        zone_codes = sorted(
            {
                str((feature.get("properties") or {}).get("id") or "").strip().upper()
                for feature in features
                if isinstance(feature, dict)
            }
            - {""}
        )
        LOGGER.info("Enumerated %d forecast zones", len(zone_codes))

        semaphore = asyncio.Semaphore(max(1, concurrency))
        tasks = [_fetch_zone_detail(client, zone_code, semaphore) for zone_code in zone_codes]
        results = await asyncio.gather(*tasks)

    output_features = [feature for feature in results if feature is not None]
    output_features.sort(key=lambda feature: str(feature["properties"].get("zone_code") or ""))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "api.weather.gov/zones/forecast",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "feature_count": len(output_features),
        },
        "features": output_features,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local NWS hazard zone geometry reference artifact.")
    parser.add_argument("--out", type=Path, default=_default_output_path(), help="Output GeoJSON path.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Per-request timeout in seconds.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Maximum concurrent zone detail requests.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    payload = asyncio.run(
        _build_zone_reference(
            timeout_seconds=max(5.0, float(args.timeout)),
            concurrency=max(1, int(args.concurrency)),
        )
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")))
    LOGGER.info("Wrote %d zone geometries to %s", len(payload["features"]), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())