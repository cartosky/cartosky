#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.nws import NWS_API_BASE, NWS_REQUEST_TIMEOUT
from app.services.nws_hazards import (
    default_zone_reference_path,
    fetch_active_alerts_geojson,
    sync_active_zone_reference,
)

LOGGER = logging.getLogger("build_hazard_zone_reference")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_output_path() -> Path:
    return _repo_root() / "data" / "hazards" / "zone_reference.geojson"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or refresh the local NWS hazard zone geometry artifact for currently active non-county zones.")
    parser.add_argument("--out", type=Path, default=_default_output_path(), help="Output GeoJSON path.")
    parser.add_argument("--timeout", type=float, default=NWS_REQUEST_TIMEOUT, help="Per-request timeout in seconds.")
    parser.add_argument("--api-base", type=str, default=NWS_API_BASE, help="NWS API base URL.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    payload = fetch_active_alerts_geojson(
        timeout_seconds=max(5.0, float(args.timeout)),
        api_base=args.api_base,
    )
    result = sync_active_zone_reference(
        payload=payload,
        zone_reference_path=args.out or default_zone_reference_path(_repo_root() / "data"),
        timeout_seconds=max(5.0, float(args.timeout)),
        api_base=args.api_base,
    )
    LOGGER.info(
        "Zone reference sync complete needed=%d resolved=%d updated=%s path=%s",
        len(result.needed_zone_codes),
        len(result.resolved_zone_codes),
        result.updated,
        result.path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())