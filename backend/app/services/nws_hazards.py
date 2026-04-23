from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import linemerge, unary_union

from app.models.nws_hazards import NWS_HAZARDS_MODEL
from app.services.nws import NWS_API_BASE, NWS_REQUEST_TIMEOUT, NWS_USER_AGENT
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

NWS_HAZARDS_MODEL_ID = "nws_hazards"
NWS_HAZARDS_REGION_ID = "conus"
NWS_ALERTS_ACTIVE_URL = f"{NWS_API_BASE}/alerts/active"
DEFAULT_COUNTY_REFERENCE_RELATIVE_PATH = Path("hazards") / "county_reference.geojson"
DEFAULT_ZONE_REFERENCE_RELATIVE_PATH = Path("hazards") / "zone_reference.geojson"
NWS_HAZARDS_MAX_RETRIES = 1
NWS_HAZARDS_RETRY_BACKOFF_SECONDS = 1.0
NWS_HAZARDS_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

STATE_ABBR_TO_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10",
    "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
    "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35",
    "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "PR": "72", "VI": "78", "AS": "60", "GU": "66", "MP": "69",
}
STATE_FIPS_TO_ABBR: dict[str, str] = {value: key for key, value in STATE_ABBR_TO_FIPS.items()}


@dataclass(frozen=True)
class HazardStyle:
    key: str
    label: str
    fill: str
    stroke: str
    priority: int


@dataclass(frozen=True)
class NormalizedHazardAlert:
    alert_id: str
    event: str
    headline: str
    sent_time: datetime | None
    effective_time: datetime | None
    expires_time: datetime | None
    county_geoids: tuple[str, ...]
    zone_codes: tuple[str, ...]
    geometry: dict[str, Any] | None
    area_description: str
    style: HazardStyle


@dataclass(frozen=True)
class HazardFramePayload:
    fh: int
    valid_time: datetime
    issue_time: datetime
    features: list[dict[str, Any]]


@dataclass(frozen=True)
class HazardPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int
    variable_ids: list[str]
    fingerprint: str


@dataclass(frozen=True)
class ZoneReferenceSyncResult:
    path: Path
    needed_zone_codes: tuple[str, ...]
    resolved_zone_codes: tuple[str, ...]
    signature: str
    updated: bool


HAZARD_COLOR_OVERRIDES: dict[str, str] = {
    "Tsunami Warning": "#FD6347",
    "Tornado Warning": "#FF0000",
    "Extreme Wind Warning": "#FF8C00",
    "Severe Thunderstorm Warning": "#FFA500",
    "Flash Flood Warning": "#8B0000",
    "Flash Flood Statement": "#8B0000",
    "Severe Weather Statement": "#00FFFF",
    "Shelter In Place Warning": "#FA8072",
    "Evacuation Immediate": "#7FFF00",
    "Civil Danger Warning": "#FFB6C1",
    "Nuclear Power Plant Warning": "#4B0082",
    "Radiological Hazard Warning": "#4B0082",
    "Hazardous Materials Warning": "#4B0082",
    "Fire Warning": "#A0522D",
    "Civil Emergency Message": "#FFB6C1",
    "Law Enforcement Warning": "#C0C0C0",
    "Storm Surge Warning": "#B524F7",
    "Hurricane Force Wind Warning": "#CD5C5C",
    "Hurricane Warning": "#DC143C",
    "Typhoon Warning": "#DC143C",
    "Special Marine Warning": "#FFA500",
    "Blizzard Warning": "#FF4500",
    "Snow Squall Warning": "#C71585",
    "Ice Storm Warning": "#8B008B",
    "Winter Storm Warning": "#FF69B4",
    "High Wind Warning": "#DAA520",
    "Tropical Storm Warning": "#B22222",
    "Storm Warning": "#9400D3",
    "Tsunami Advisory": "#D2691E",
    "Tsunami Watch": "#FF00FF",
    "Avalanche Warning": "#1E90FF",
    "Earthquake Warning": "#8B4513",
    "Volcano Warning": "#2F4F4F",
    "Ashfall Warning": "#A9A9A9",
    "Coastal Flood Warning": "#228B22",
    "Lakeshore Flood Warning": "#228B22",
    "Flood Warning": "#00FF00",
    "High Surf Warning": "#228B22",
    "Dust Storm Warning": "#FFE4C4",
    "Blowing Dust Warning": "#FFE4C4",
    "Lake Effect Snow Warning": "#008B8B",
    "Excessive Heat Warning": "#C71585",
    "Tornado Watch": "#FFFF00",
    "Severe Thunderstorm Watch": "#DB7093",
    "Flash Flood Watch": "#2E8B57",
    "Gale Warning": "#DDA0DD",
    "Flood Statement": "#00FF00",
    "Wind Chill Warning": "#B0C4DE",
    "Extreme Cold Warning": "#0000FF",
    "Hard Freeze Warning": "#9400D3",
    "Freeze Warning": "#483D8B",
    "Red Flag Warning": "#FF1493",
    "Storm Surge Watch": "#DB7FF7",
    "Hurricane Watch": "#FF00FF",
    "Hurricane Force Wind Watch": "#9932CC",
    "Typhoon Watch": "#FF00FF",
    "Tropical Storm Watch": "#F08080",
    "Storm Watch": "#FFE4B5",
    "Hurricane Local Statement": "#FFE4B5",
    "Typhoon Local Statement": "#FFE4B5",
    "Tropical Storm Local Statement": "#FFE4B5",
    "Tropical Depression Local Statement": "#FFE4B5",
    "Avalanche Advisory": "#CD853F",
    "Winter Weather Advisory": "#7B68EE",
    "Wind Chill Advisory": "#AFEEEE",
    "Heat Advisory": "#FF7F50",
    "Urban and Small Stream Flood Advisory": "#00FF7F",
    "Small Stream Flood Advisory": "#00FF7F",
    "Arroyo and Small Stream Flood Advisory": "#00FF7F",
    "Flood Advisory": "#00FF7F",
    "Hydrologic Advisory": "#00FF7F",
    "Lakeshore Flood Advisory": "#7CFC00",
    "Coastal Flood Advisory": "#7CFC00",
    "High Surf Advisory": "#BA55D3",
    "Heavy Freezing Spray Warning": "#00BFFF",
    "Dense Fog Advisory": "#708090",
    "Dense Smoke Advisory": "#F0E68C",
    "Small Craft Advisory For Hazardous Seas": "#D8BFD8",
    "Small Craft Advisory for Rough Bar": "#D8BFD8",
    "Small Craft Advisory for Winds": "#D8BFD8",
    "Small Craft Advisory": "#D8BFD8",
    "Brisk Wind Advisory": "#D8BFD8",
    "Hazardous Seas Warning": "#D8BFD8",
    "Dust Advisory": "#BDB76B",
    "Blowing Dust Advisory": "#BDB76B",
    "Lake Wind Advisory": "#D2B48C",
    "Wind Advisory": "#D2B48C",
    "Frost Advisory": "#6495ED",
    "Ashfall Advisory": "#696969",
    "Freezing Fog Advisory": "#008080",
    "Freezing Spray Advisory": "#00BFFF",
    "Low Water Advisory": "#A52A2A",
    "Local Area Emergency": "#C0C0C0",
    "Avalanche Watch": "#F4A460",
    "Blizzard Watch": "#ADFF2F",
    "Rip Current Statement": "#40E0D0",
    "Beach Hazards Statement": "#40E0D0",
    "Gale Watch": "#FFC0CB",
    "Winter Storm Watch": "#4682B4",
    "Hazardous Seas Watch": "#483D8B",
    "Heavy Freezing Spray Watch": "#BC8F8F",
    "Coastal Flood Watch": "#66CDAA",
    "Lakeshore Flood Watch": "#66CDAA",
    "Flood Watch": "#2E8B57",
    "High Wind Watch": "#B8860B",
    "Excessive Heat Watch": "#800000",
    "Extreme Cold Watch": "#0000FF",
    "Wind Chill Watch": "#5F9EA0",
    "Lake Effect Snow Watch": "#87CEFA",
    "Hard Freeze Watch": "#4169E1",
    "Freeze Watch": "#00FFFF",
    "Fire Weather Watch": "#FFDEAD",
    "Extreme Fire Danger": "#E9967A",
    "911 Telephone Outage": "#C0C0C0",
    "Coastal Flood Statement": "#6B8E23",
    "Lakeshore Flood Statement": "#6B8E23",
    "Special Weather Statement": "#FFE4B5",
    "Marine Weather Statement": "#FFDAB9",
    "Air Quality Alert": "#808080",
    "Air Stagnation Advisory": "#808080",
    "Hazardous Weather Outlook": "#EEE8AA",
    "Hydrologic Outlook": "#90EE90",
    "Short Term Forecast": "#98FB98",
    "Administrative Message": "#C0C0C0",
    "Test": "#F0FFFF",
    "Child Abduction Emergency": "#FFFFFF",
    "Blue Alert": "#FFFFFF",
}

SPECIAL_EVENT_PRIORITIES: dict[str, int] = {
    "tornado warning": 390,
    "extreme wind warning": 380,
    "severe thunderstorm warning": 370,
    "flash flood warning": 365,
    "flash flood statement": 365,
    "blizzard warning": 360,
    "red flag warning": 358,
    "winter storm warning": 355,
    "ice storm warning": 352,
    "flood warning": 350,
    "high wind warning": 345,
    "storm surge warning": 340,
    "hurricane warning": 338,
    "typhoon warning": 338,
    "hurricane force wind warning": 336,
    "tropical storm warning": 334,
    "special marine warning": 325,
    "storm warning": 320,
    "hazardous seas warning": 318,
    "tornado watch": 285,
    "severe thunderstorm watch": 275,
    "flash flood watch": 265,
    "flood watch": 265,
    "winter storm watch": 255,
    "red flag watch": 250,
    "small craft advisory": 135,
}


def _darken_hex_color(color: str, factor: float = 0.58) -> str:
    normalized = str(color or "").strip()
    if len(normalized) != 7 or not normalized.startswith("#"):
        return "#000000"
    try:
        red = int(normalized[1:3], 16)
        green = int(normalized[3:5], 16)
        blue = int(normalized[5:7], 16)
    except ValueError:
        return "#000000"
    clamped = max(0.0, min(1.0, factor))
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(red * clamped))),
        max(0, min(255, round(green * clamped))),
        max(0, min(255, round(blue * clamped))),
    )


def _priority_for_event(event_label: str) -> int:
    normalized = str(event_label or "").strip().lower()
    explicit = SPECIAL_EVENT_PRIORITIES.get(normalized)
    if explicit is not None:
        return explicit
    if "warning" in normalized:
        return 300
    if "watch" in normalized:
        return 200
    if "advisory" in normalized:
        return 120
    if "statement" in normalized:
        return 60
    if "outlook" in normalized or "forecast" in normalized:
        return 55
    if "message" in normalized:
        return 50
    return 60


def _build_event_style_overrides() -> dict[str, HazardStyle]:
    overrides: dict[str, HazardStyle] = {}
    for label, fill in HAZARD_COLOR_OVERRIDES.items():
        normalized = label.strip().lower()
        overrides[normalized] = HazardStyle(
            key=normalized.replace(" ", "_"),
            label=label,
            fill=fill,
            stroke=_darken_hex_color(fill),
            priority=_priority_for_event(label),
        )
    return overrides


EVENT_STYLE_OVERRIDES: dict[str, HazardStyle] = _build_event_style_overrides()

SIGNIFICANCE_FALLBACKS: dict[str, HazardStyle] = {
    "warning": HazardStyle("warning", "Warning", "#FF0000", _darken_hex_color("#FF0000"), 300),
    "watch": HazardStyle("watch", "Watch", "#FFFF00", _darken_hex_color("#FFFF00"), 200),
    "advisory": HazardStyle("advisory", "Advisory", "#00FF7F", _darken_hex_color("#00FF7F"), 120),
    "statement": HazardStyle("statement", "Statement", "#FFE4B5", _darken_hex_color("#FFE4B5"), 60),
}


class NWSHazardsError(RuntimeError):
    pass


def _coerce_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for entry in value:
        text = str(entry or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _significance_from_event(event: str) -> str:
    lowered = event.strip().lower()
    for token in ("warning", "watch", "advisory", "statement"):
        if token in lowered:
            return token
    return "statement"


def _event_style(event: str) -> HazardStyle:
    normalized_event = event.strip().lower()
    if normalized_event in EVENT_STYLE_OVERRIDES:
        return EVENT_STYLE_OVERRIDES[normalized_event]
    significance = _significance_from_event(normalized_event)
    fallback = SIGNIFICANCE_FALLBACKS[significance]
    return HazardStyle(
        key=normalized_event.replace(" ", "_") or fallback.key,
        label=event.strip() or fallback.label,
        fill=fallback.fill,
        stroke=fallback.stroke,
        priority=fallback.priority,
    )


def _same_to_geoid(code: str) -> str | None:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) != 6:
        return None
    return digits[1:]


def _ugc_to_geoid(code: str) -> str | None:
    normalized = str(code or "").strip().upper()
    if len(normalized) != 6 or normalized[2] != "C":
        return None
    state_fips = STATE_ABBR_TO_FIPS.get(normalized[:2])
    county_code = normalized[3:]
    if not state_fips or not county_code.isdigit():
        return None
    return f"{state_fips}{county_code}"


def _zone_code_from_ugc(code: str) -> str | None:
    normalized = str(code or "").strip().upper()
    if len(normalized) != 6 or not normalized[:2].isalpha() or not normalized[3:].isdigit():
        return None
    if normalized[2] == "C":
        return None
    return normalized


def _build_alert_fingerprint(payload: dict[str, Any]) -> str:
    features = payload.get("features") if isinstance(payload, dict) else None
    normalized_rows: list[dict[str, Any]] = []
    if isinstance(features, list):
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties")
            if not isinstance(props, dict):
                continue
            normalized_rows.append(
                {
                    "id": str(props.get("id") or feature.get("id") or "").strip(),
                    "event": str(props.get("event") or "").strip(),
                    "sent": str(props.get("sent") or "").strip(),
                    "effective": str(props.get("effective") or "").strip(),
                    "expires": str(props.get("expires") or "").strip(),
                    "status": str(props.get("status") or "").strip(),
                    "messageType": str(props.get("messageType") or "").strip(),
                }
            )
    body = json.dumps(sorted(normalized_rows, key=lambda item: (item["id"], item["event"], item["expires"])), separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def fetch_active_alerts_geojson(
    *,
    timeout_seconds: float = NWS_REQUEST_TIMEOUT,
    api_base: str = NWS_API_BASE,
) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/alerts/active"
    return _fetch_geojson_with_retry(url=url, timeout_seconds=timeout_seconds, log_retries=True)


def _fetch_geojson_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    log_retries: bool,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    managed_client = client is None
    session = client or httpx.Client(timeout=float(timeout_seconds), follow_redirects=True, headers={
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
    })
    try:
        for attempt in range(1 + NWS_HAZARDS_MAX_RETRIES):
            if attempt > 0:
                if log_retries:
                    logger.info("NWS Hazards retry attempt %d for %s", attempt, url)
                time.sleep(NWS_HAZARDS_RETRY_BACKOFF_SECONDS)
            try:
                response = session.get(url)
            except httpx.TimeoutException as exc:
                if log_retries:
                    logger.warning("NWS Hazards request timeout: %s (attempt %d)", url, attempt + 1)
                last_error = exc
                continue
            except httpx.RequestError as exc:
                raise NWSHazardsError(f"NWS Hazards request failed: {exc}") from exc

            if response.status_code == 200:
                return response.json()

            if response.status_code in NWS_HAZARDS_RETRYABLE_STATUS_CODES:
                if log_retries:
                    logger.warning("NWS Hazards HTTP %d from %s (attempt %d)", response.status_code, url, attempt + 1)
                last_error = NWSHazardsError(f"NWS Hazards upstream HTTP {response.status_code}")
                continue

            raise NWSHazardsError(f"NWS Hazards upstream returned HTTP {response.status_code}")
    finally:
        if managed_client:
            session.close()

    if isinstance(last_error, NWSHazardsError):
        raise NWSHazardsError(str(last_error)) from last_error
    if isinstance(last_error, httpx.TimeoutException):
        raise NWSHazardsError("NWS Hazards request timed out after retries") from last_error
    raise NWSHazardsError(f"NWS Hazards request failed after retries for {url}") from last_error


def default_zone_reference_path(data_root: Path) -> Path:
    return data_root / DEFAULT_ZONE_REFERENCE_RELATIVE_PATH


def default_county_reference_path(data_root: Path) -> Path:
    return data_root / DEFAULT_COUNTY_REFERENCE_RELATIVE_PATH


@lru_cache(maxsize=8)
def _load_zone_reference_file_cached(path_key: str) -> dict[str, dict[str, Any]]:
    path = Path(path_key)
    payload = json.loads(path.read_text())
    features = payload.get("features")
    if not isinstance(features, list):
        raise NWSHazardsError(f"Zone reference at {path} is missing features")

    zones: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        props = feature.get("properties")
        if not isinstance(geometry, dict) or not isinstance(props, dict):
            continue
        zone_code = str(props.get("zone_code") or props.get("id") or "").strip().upper()
        if len(zone_code) != 6:
            continue
        zones[zone_code] = {
            "zone_code": zone_code,
            "name": str(props.get("name") or zone_code).strip(),
            "state": str(props.get("state") or "").strip(),
            "zone_type": str(props.get("zone_type") or props.get("type") or "").strip(),
            "geometry": geometry,
        }
    if not zones:
        raise NWSHazardsError(f"Zone reference at {path} produced no zones")
    return zones


def load_zone_reference(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        logger.warning("NWS Hazards zone reference file not found: %s", path)
        return {}
    return _load_zone_reference_file_cached(str(path.resolve()))


def _zone_feature_from_record(zone: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "zone_code": str(zone.get("zone_code") or "").strip().upper(),
            "name": str(zone.get("name") or "").strip(),
            "state": str(zone.get("state") or "").strip(),
            "zone_type": str(zone.get("zone_type") or "").strip(),
        },
        "geometry": zone.get("geometry"),
    }


def _zone_reference_payload(zones: dict[str, dict[str, Any]]) -> dict[str, Any]:
    features = [_zone_feature_from_record(zone) for _, zone in sorted(zones.items())]
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "nws_hazards_active_zone_reference",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feature_count": len(features),
        },
        "features": features,
    }


def _zone_reference_signature(zones: dict[str, dict[str, Any]]) -> str:
    features = [_zone_feature_from_record(zone) for _, zone in sorted(zones.items())]
    body = json.dumps(features, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def zone_reference_signature_for_path(path: Path) -> str:
    zones = _load_zone_reference_file_cached(str(path.resolve())) if path.is_file() else {}
    return _zone_reference_signature(zones)


def _zone_lookup_hints_for_properties(props: dict[str, Any]) -> dict[str, str]:
    affected_zones = props.get("affectedZones")
    if not isinstance(affected_zones, list):
        return {}
    hints: dict[str, str] = {}
    for entry in affected_zones:
        text = str(entry or "").strip().rstrip("/")
        if not text:
            continue
        parts = text.split("/")
        if len(parts) < 2:
            continue
        zone_code = parts[-1].strip().upper()
        zone_type = parts[-2].strip().lower()
        if zone_code and zone_type:
            hints[zone_code] = zone_type
    return hints


def _zone_lookup_hints_for_payload(payload: dict[str, Any]) -> dict[str, str]:
    features_raw = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features_raw, list):
        return {}
    zone_hints: dict[str, str] = {}
    for feature in features_raw:
        if not isinstance(feature, dict):
            continue
        alert = _normalize_alert(feature)
        if alert is None:
            continue
        props = feature.get("properties")
        property_hints = _zone_lookup_hints_for_properties(props) if isinstance(props, dict) else {}
        for zone_code in alert.zone_codes:
            zone_hints[zone_code] = property_hints.get(zone_code, zone_hints.get(zone_code, ""))
    return zone_hints


def _fetch_zone_record(
    zone_code: str,
    *,
    timeout_seconds: float,
    api_base: str,
    client: httpx.Client,
    zone_type_hint: str | None = None,
) -> dict[str, Any] | None:
    normalized_zone = str(zone_code or "").strip().upper()
    if not normalized_zone:
        return None
    ordered_zone_types = [
        *( [str(zone_type_hint).strip().lower()] if str(zone_type_hint or "").strip() else [] ),
        "forecast",
        "public",
        "fire",
        "marine",
    ]
    seen_zone_types: set[str] = set()
    for zone_type in ordered_zone_types:
        normalized_zone_type = str(zone_type or "").strip().lower()
        if not normalized_zone_type or normalized_zone_type in seen_zone_types:
            continue
        seen_zone_types.add(normalized_zone_type)
        url = f"{api_base.rstrip('/')}/zones/{normalized_zone_type}/{normalized_zone}"
        try:
            payload = _fetch_geojson_with_retry(
                url=url,
                timeout_seconds=timeout_seconds,
                log_retries=False,
                client=client,
            )
        except NWSHazardsError:
            continue
        geometry = payload.get("geometry")
        props = payload.get("properties") if isinstance(payload, dict) else None
        if not isinstance(geometry, dict) or not isinstance(props, dict):
            continue
        return {
            "zone_code": normalized_zone,
            "name": str(props.get("name") or normalized_zone).strip(),
            "state": str(props.get("state") or "").strip(),
            "zone_type": str(props.get("type") or normalized_zone_type).strip(),
            "geometry": geometry,
        }
    logger.warning("NWS Hazards active zone lookup failed for %s: unable to resolve zone geometry from NWS zones endpoints", normalized_zone)
    return None


def sync_active_zone_reference(
    *,
    payload: dict[str, Any],
    zone_reference_path: Path,
    timeout_seconds: float = NWS_REQUEST_TIMEOUT,
    api_base: str = NWS_API_BASE,
) -> ZoneReferenceSyncResult:
    existing_zones = _load_zone_reference_file_cached(str(zone_reference_path.resolve())) if zone_reference_path.is_file() else {}
    zone_lookup_hints = _zone_lookup_hints_for_payload(payload)
    needed_zone_codes = set(sorted(zone_lookup_hints.keys()))
    active_zones: dict[str, dict[str, Any]] = {
        zone_code: existing_zones[zone_code]
        for zone_code in sorted(needed_zone_codes)
        if zone_code in existing_zones
    }
    missing_zone_codes = [zone_code for zone_code in sorted(needed_zone_codes) if zone_code not in active_zones]

    if missing_zone_codes:
        with httpx.Client(timeout=float(timeout_seconds), follow_redirects=True, headers={
            "User-Agent": NWS_USER_AGENT,
            "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
        }) as client:
            for zone_code in missing_zone_codes:
                zone = _fetch_zone_record(
                    zone_code,
                    timeout_seconds=timeout_seconds,
                    api_base=api_base,
                    client=client,
                    zone_type_hint=zone_lookup_hints.get(zone_code),
                )
                if zone is not None:
                    active_zones[zone_code] = zone

    new_signature = _zone_reference_signature(active_zones)
    previous_signature = _zone_reference_signature(existing_zones)
    write_required = (new_signature != previous_signature) or (not zone_reference_path.is_file())
    if write_required:
        zone_reference_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(zone_reference_path, _zone_reference_payload(active_zones))
        _load_zone_reference_file_cached.cache_clear()

    return ZoneReferenceSyncResult(
        path=zone_reference_path,
        needed_zone_codes=tuple(sorted(needed_zone_codes)),
        resolved_zone_codes=tuple(sorted(active_zones.keys())),
        signature=new_signature,
        updated=write_required,
    )


def _resolve_zone_references(
    zone_codes: set[str],
    *,
    zone_reference: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for zone_code in sorted(zone_codes):
        zone = zone_reference.get(zone_code)
        if zone is not None:
            resolved[zone_code] = zone
    return resolved


def _prefers_zone_geometry(alert: NormalizedHazardAlert, zone_references: dict[str, dict[str, Any]]) -> bool:
    if alert.geometry is not None or not alert.zone_codes:
        return False
    return any(zone_code in zone_references for zone_code in alert.zone_codes)


@lru_cache(maxsize=8)
def _load_county_reference_cached(path_key: str) -> dict[str, dict[str, Any]]:
    path = Path(path_key)
    payload = json.loads(path.read_text())
    features = payload.get("features")
    if not isinstance(features, list):
        raise NWSHazardsError(f"County reference at {path} is missing features")

    counties: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        props = feature.get("properties")
        if not isinstance(geometry, dict) or not isinstance(props, dict):
            continue
        geoid = str(props.get("GEOID") or props.get("geoid") or "").strip()
        if len(geoid) != 5 or not geoid.isdigit():
            continue
        counties[geoid] = {
            "name": str(props.get("NAME") or props.get("name") or geoid).strip(),
            "state": str(
                props.get("STUSPS")
                or props.get("state")
                or STATE_FIPS_TO_ABBR.get(str(props.get("STATEFP") or "").strip(), "")
            ).strip(),
            "geometry": geometry,
        }
    if not counties:
        raise NWSHazardsError(f"County reference at {path} produced no counties")
    return counties


def load_county_reference(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise NWSHazardsError(f"County reference file not found: {path}")
    return _load_county_reference_cached(str(path.resolve()))


def _county_geoids_from_properties(props: dict[str, Any]) -> tuple[str, ...]:
    geocode = props.get("geocode")
    if not isinstance(geocode, dict):
        return ()
    geoids: set[str] = set()
    for same_code in _string_list(geocode.get("SAME")):
        geoid = _same_to_geoid(same_code)
        if geoid:
            geoids.add(geoid)
    for ugc_code in _string_list(geocode.get("UGC")):
        geoid = _ugc_to_geoid(ugc_code)
        if geoid:
            geoids.add(geoid)
    return tuple(sorted(geoids))


def _zone_codes_from_properties(props: dict[str, Any]) -> tuple[str, ...]:
    geocode = props.get("geocode")
    if not isinstance(geocode, dict):
        return ()
    zone_codes: set[str] = set()
    for ugc_code in _string_list(geocode.get("UGC")):
        zone_code = _zone_code_from_ugc(ugc_code)
        if zone_code:
            zone_codes.add(zone_code)
    return tuple(sorted(zone_codes))


GEOMETRY_PREFERRED_EVENTS = frozenset({
    "flood warning",
    "flood watch",
    "flood statement",
    "flood advisory",
    "hydrologic advisory",
    "hydrologic outlook",
    "coastal flood warning",
    "coastal flood watch",
    "coastal flood advisory",
    "coastal flood statement",
    "lakeshore flood warning",
    "lakeshore flood watch",
    "lakeshore flood advisory",
    "lakeshore flood statement",
    "high surf warning",
    "high surf advisory",
    "rip current statement",
    "beach hazards statement",
})


def _prefers_alert_geometry(alert: NormalizedHazardAlert) -> bool:
    normalized_event = alert.event.strip().lower()
    if normalized_event.startswith("flash flood"):
        return False
    return normalized_event in GEOMETRY_PREFERRED_EVENTS


def _build_geometry_feature(
    *,
    geometry: dict[str, Any],
    primary: NormalizedHazardAlert,
    alerts: list[NormalizedHazardAlert],
    hover_name: str,
    area_description: str,
    fill_opacity: float,
    stroke_width: float,
    extra_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_hazard_labels = _unique_hazard_labels(alerts)
    props: dict[str, Any] = {
        "risk_code": primary.style.key,
        "risk_label": primary.style.label,
        "hover_label": _build_hover_label(hover_name, alerts),
        "fill": primary.style.fill,
        "fill_opacity": fill_opacity,
        "stroke": primary.style.stroke,
        "stroke_width": stroke_width,
        "sort_rank": int(primary.style.priority),
        "alert_count": len(alerts),
        "alert_ids": [alert.alert_id for alert in alerts],
        "active_hazards": active_hazard_labels,
        "area_description": area_description,
        "expires_time": primary.expires_time.strftime("%Y-%m-%dT%H:%M:%SZ") if primary.expires_time else None,
    }
    if extra_properties:
        props.update(extra_properties)
    return {
        "type": "Feature",
        "properties": props,
        "geometry": geometry,
    }


def _normalize_alert(feature: dict[str, Any]) -> NormalizedHazardAlert | None:
    props = feature.get("properties")
    if not isinstance(props, dict):
        return None
    if str(props.get("status") or "").strip().lower() not in {"", "actual"}:
        return None
    event = str(props.get("event") or "").strip()
    if not event:
        return None
    alert_id = str(props.get("id") or feature.get("id") or "").strip()
    if not alert_id:
        return None
    return NormalizedHazardAlert(
        alert_id=alert_id,
        event=event,
        headline=str(props.get("headline") or event).strip() or event,
        sent_time=_coerce_datetime(props.get("sent")),
        effective_time=_coerce_datetime(props.get("effective")),
        expires_time=_coerce_datetime(props.get("expires")),
        county_geoids=_county_geoids_from_properties(props),
        zone_codes=_zone_codes_from_properties(props),
        geometry=feature.get("geometry") if isinstance(feature.get("geometry"), dict) else None,
        area_description=str(props.get("areaDesc") or "").strip(),
        style=_event_style(event),
    )


def _sort_alerts_by_priority(alerts: list[NormalizedHazardAlert]) -> list[NormalizedHazardAlert]:
    return sorted(
        alerts,
        key=lambda alert: (
            -int(alert.style.priority),
            alert.expires_time or datetime.max.replace(tzinfo=timezone.utc),
            alert.event,
            alert.alert_id,
        ),
    )


def _build_hover_label(name: str, alerts: list[NormalizedHazardAlert]) -> str:
    if not alerts:
        return name
    unique_labels = list(dict.fromkeys(alert.style.label for alert in alerts if alert.style.label))
    primary_label = unique_labels[0] if unique_labels else alerts[0].style.label
    if len(unique_labels) <= 1:
        return f"{name}: {primary_label}"
    return f"{name}: {primary_label} +{len(unique_labels) - 1} more"


def _unique_hazard_labels(alerts: list[NormalizedHazardAlert]) -> list[str]:
    return list(dict.fromkeys(alert.style.label for alert in alerts if alert.style.label))


def _legend_entries_for_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, int]] = set()
    entries: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties") or {}
        label = str(props.get("risk_label") or "").strip()
        color = str(props.get("fill") or "").strip()
        sort_rank = int(props.get("sort_rank") or 0)
        key = (label, color, sort_rank)
        if not label or not color or key in seen:
            continue
        seen.add(key)
        entries.append({"value": sort_rank, "color": color, "label": label})
    entries.sort(key=lambda item: (-int(item["value"]), str(item["label"])))
    return entries


def _geometry_is_area(geometry: dict[str, Any] | None) -> bool:
    if not isinstance(geometry, dict):
        return False
    return str(geometry.get("type") or "") in {"Polygon", "MultiPolygon"}


def _geometry_is_line(geometry: dict[str, Any] | None) -> bool:
    if not isinstance(geometry, dict):
        return False
    return str(geometry.get("type") or "") in {"LineString", "MultiLineString"}


def _dissolve_group_key(properties: dict[str, Any]) -> tuple[Any, ...]:
    return (
        properties.get("risk_code"),
        properties.get("risk_label"),
        properties.get("fill"),
        properties.get("fill_opacity"),
        properties.get("stroke"),
        properties.get("stroke_width"),
        properties.get("sort_rank"),
    )


def _dissolve_hover_label(properties_list: list[dict[str, Any]]) -> str:
    area_names = [str(props.get("area_description") or "").strip() for props in properties_list]
    area_names = list(dict.fromkeys(name for name in area_names if name))
    risk_label = str(properties_list[0].get("risk_label") or "").strip()
    if not area_names:
        return risk_label
    if len(area_names) == 1:
        return f"{area_names[0]}: {risk_label}" if risk_label else area_names[0]
    return f"{risk_label} ({len(area_names)} areas)" if risk_label else f"{len(area_names)} areas"


def _dissolve_area_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dissolve_candidates: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for feature in features:
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not _geometry_is_area(geometry):
            passthrough.append(feature)
            continue
        dissolve_candidates.setdefault(_dissolve_group_key(properties), []).append(feature)

    dissolved: list[dict[str, Any]] = []
    for group in dissolve_candidates.values():
        if len(group) == 1:
            dissolved.append(group[0])
            continue

        source_geometries = [shape(feature["geometry"]) for feature in group]
        merged_geometry = unary_union(source_geometries)
        if merged_geometry.is_empty:
            dissolved.extend(group)
            continue

        merged_boundaries = linemerge(unary_union([geometry.boundary for geometry in source_geometries]))

        geometries = list(merged_geometry.geoms) if isinstance(merged_geometry, GeometryCollection) else [merged_geometry]
        polygon_geometries = [geom for geom in geometries if geom.geom_type in {"Polygon", "MultiPolygon"} and not geom.is_empty]
        if not polygon_geometries:
            dissolved.extend(group)
            continue

        properties_list = [feature["properties"] for feature in group]
        template = dict(properties_list[0])
        alert_ids = list(dict.fromkeys(
            str(alert_id)
            for props in properties_list
            for alert_id in (props.get("alert_ids") or [])
            if str(alert_id).strip()
        ))
        active_hazards = list(dict.fromkeys(
            str(label)
            for props in properties_list
            for label in (props.get("active_hazards") or [])
            if str(label).strip()
        ))
        area_names = list(dict.fromkeys(
            str(props.get("area_description") or "").strip()
            for props in properties_list
            if str(props.get("area_description") or "").strip()
        ))
        states = list(dict.fromkeys(
            str(props.get("state") or "").strip()
            for props in properties_list
            if str(props.get("state") or "").strip()
        ))
        zone_codes = list(dict.fromkeys(
            str(props.get("zone_code") or "").strip()
            for props in properties_list
            if str(props.get("zone_code") or "").strip()
        ))
        county_geoids = list(dict.fromkeys(
            str(props.get("county_geoid") or "").strip()
            for props in properties_list
            if str(props.get("county_geoid") or "").strip()
        ))
        expires_times = [str(props.get("expires_time") or "").strip() for props in properties_list if str(props.get("expires_time") or "").strip()]

        template["alert_ids"] = alert_ids
        template["alert_count"] = len(alert_ids)
        template["active_hazards"] = active_hazards
        template["hover_label"] = _dissolve_hover_label(properties_list)
        template["area_description"] = area_names[0] if len(area_names) == 1 else ", ".join(area_names)
        template["state"] = states[0] if len(states) == 1 else ",".join(states)
        template["expires_time"] = min(expires_times) if expires_times else template.get("expires_time")
        if zone_codes:
            template["zone_codes"] = zone_codes
            template.pop("zone_code", None)
            template.pop("zone_name", None)
        if county_geoids:
            template["county_geoids"] = county_geoids
            template.pop("county_geoid", None)
            template.pop("county_name", None)

        fill_template = dict(template)
        fill_template["geometry_role"] = "fill"
        fill_template["stroke_width"] = 0.0

        for polygon_geometry in polygon_geometries:
            dissolved.append(
                {
                    "type": "Feature",
                    "properties": dict(fill_template),
                    "geometry": mapping(polygon_geometry),
                }
            )

        boundary_geometries = list(merged_boundaries.geoms) if isinstance(merged_boundaries, GeometryCollection) else [merged_boundaries]
        line_geometries = [geom for geom in boundary_geometries if geom.geom_type in {"LineString", "MultiLineString"} and not geom.is_empty]
        if line_geometries:
            outline_template = dict(template)
            outline_template["fill_opacity"] = 0.0
            outline_template["geometry_role"] = "outline"
            for line_geometry in line_geometries:
                dissolved.append(
                    {
                        "type": "Feature",
                        "properties": dict(outline_template),
                        "geometry": mapping(line_geometry),
                    }
                )

    dissolved.extend(passthrough)
    return dissolved


def build_active_hazards_frame(
    payload: dict[str, Any],
    *,
    county_reference_path: Path,
    zone_reference_path: Path | None = None,
    fh: int = 0,
) -> HazardFramePayload:
    counties = load_county_reference(county_reference_path)
    zone_references = load_zone_reference(zone_reference_path) if zone_reference_path is not None else {}
    features_raw = payload.get("features")
    if not isinstance(features_raw, list):
        raise NWSHazardsError("Active alerts payload is missing features")

    normalized_alerts = [alert for feature in features_raw if (alert := _normalize_alert(feature)) is not None]
    if not normalized_alerts:
        raise NWSHazardsError("Active alerts payload had no recognized alerts")

    bundle_updated = _coerce_datetime(payload.get("updated"))
    issue_time_candidates = [
        bundle_updated,
        *[alert.sent_time for alert in normalized_alerts if alert.sent_time is not None],
        *[alert.effective_time for alert in normalized_alerts if alert.effective_time is not None],
    ]
    issue_time = next((candidate for candidate in issue_time_candidates if candidate is not None), datetime.now(timezone.utc))
    valid_time = bundle_updated or issue_time

    county_buckets: dict[str, list[NormalizedHazardAlert]] = {}
    zone_buckets: dict[str, list[NormalizedHazardAlert]] = {}
    geometry_features: list[dict[str, Any]] = []
    zone_candidate_alerts: list[NormalizedHazardAlert] = []
    needed_zone_codes: set[str] = set()
    for alert in normalized_alerts:
        resolved_geoids = [geoid for geoid in alert.county_geoids if geoid in counties]
        if alert.geometry is not None and _prefers_alert_geometry(alert):
            geometry_features.append(
                _build_geometry_feature(
                    geometry=alert.geometry,
                    primary=alert,
                    alerts=[alert],
                    hover_name=alert.style.label,
                    area_description=alert.area_description,
                    fill_opacity=0.42,
                    stroke_width=1.6,
                )
            )
            continue
        if alert.zone_codes:
            zone_candidate_alerts.append(alert)
            needed_zone_codes.update(alert.zone_codes)
            continue
        if resolved_geoids:
            for geoid in resolved_geoids:
                county_buckets.setdefault(geoid, []).append(alert)
            continue
        if alert.geometry is not None:
            geometry_features.append(
                _build_geometry_feature(
                    geometry=alert.geometry,
                    primary=alert,
                    alerts=[alert],
                    hover_name=alert.headline,
                    area_description=alert.area_description,
                    fill_opacity=0.42,
                    stroke_width=1.6,
                )
            )
            continue
        if alert.zone_codes:
            zone_candidate_alerts.append(alert)
            needed_zone_codes.update(alert.zone_codes)

    zone_references = _resolve_zone_references(
        needed_zone_codes,
        zone_reference=zone_references,
    )
    for alert in zone_candidate_alerts:
        resolved_zone_codes = [zone_code for zone_code in alert.zone_codes if zone_code in zone_references]
        if resolved_zone_codes:
            for zone_code in resolved_zone_codes:
                zone_buckets.setdefault(zone_code, []).append(alert)
            continue
        resolved_geoids = [geoid for geoid in alert.county_geoids if geoid in counties]
        if resolved_geoids:
            for geoid in resolved_geoids:
                county_buckets.setdefault(geoid, []).append(alert)
            continue
        if alert.geometry is not None:
            geometry_features.append(
                _build_geometry_feature(
                    geometry=alert.geometry,
                    primary=alert,
                    alerts=[alert],
                    hover_name=alert.headline,
                    area_description=alert.area_description,
                    fill_opacity=0.42,
                    stroke_width=1.6,
                )
            )

    county_features: list[dict[str, Any]] = []
    for geoid, alerts in county_buckets.items():
        county = counties.get(geoid)
        if county is None:
            continue
        sorted_alerts = _sort_alerts_by_priority(alerts)
        primary = sorted_alerts[0]
        county_name = str(county.get("name") or geoid)
        active_hazard_labels = _unique_hazard_labels(sorted_alerts)
        county_features.append(
            {
                "type": "Feature",
                "properties": {
                    "risk_code": primary.style.key,
                    "risk_label": primary.style.label,
                    "hover_label": _build_hover_label(county_name, sorted_alerts),
                    "fill": primary.style.fill,
                    "fill_opacity": 0.58,
                    "stroke": primary.style.stroke,
                    "stroke_width": 1.0,
                    "sort_rank": int(primary.style.priority),
                    "county_geoid": geoid,
                    "county_name": county_name,
                    "state": str(county.get("state") or ""),
                    "alert_count": len(sorted_alerts),
                    "alert_ids": [alert.alert_id for alert in sorted_alerts],
                    "active_hazards": active_hazard_labels,
                    "area_description": county_name,
                    "expires_time": primary.expires_time.strftime("%Y-%m-%dT%H:%M:%SZ") if primary.expires_time else None,
                },
                "geometry": county["geometry"],
            }
        )

    zone_features: list[dict[str, Any]] = []
    for zone_code, alerts in zone_buckets.items():
        zone = zone_references.get(zone_code)
        if zone is None:
            continue
        sorted_alerts = _sort_alerts_by_priority(alerts)
        primary = sorted_alerts[0]
        zone_name = str(zone.get("name") or zone_code)
        zone_features.append(
            _build_geometry_feature(
                geometry=zone["geometry"],
                primary=primary,
                alerts=sorted_alerts,
                hover_name=zone_name,
                area_description=zone_name,
                fill_opacity=0.42,
                stroke_width=1.6,
                extra_properties={
                    "zone_code": zone_code,
                    "zone_name": zone_name,
                    "state": str(zone.get("state") or ""),
                },
            )
        )

    all_features = _dissolve_area_features(county_features + zone_features + geometry_features)
    all_features.sort(key=lambda feature: int((feature.get("properties") or {}).get("sort_rank") or 0))
    return HazardFramePayload(
        fh=int(fh),
        valid_time=valid_time.astimezone(timezone.utc),
        issue_time=issue_time.astimezone(timezone.utc),
        features=all_features,
    )


def _build_frame_sidecar(*, run_id: str, frame: HazardFramePayload) -> dict[str, Any]:
    return {
        "contract_version": "3.0",
        "model": NWS_HAZARDS_MODEL_ID,
        "run": run_id,
        "var": "active",
        "fh": int(frame.fh),
        "region": NWS_HAZARDS_REGION_ID,
        "valid_time": frame.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issue_time": frame.issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "categorical",
        "legend_title": "NWS Hazards",
        "display_name": "Active Hazards",
        "legend_entries": _legend_entries_for_features(frame.features),
        "vector_layers": {
            "primary": {
                "format": "geojson",
                "path": f"vectors/fh{frame.fh:03d}.geojson",
                "style_key": "nws_hazards_active",
            }
        },
    }


def publish_active_hazards(
    *,
    data_root: Path,
    county_reference_path: Path | None = None,
    zone_reference_path: Path | None = None,
    zone_reference_signature: str | None = None,
    timeout_seconds: float = NWS_REQUEST_TIMEOUT,
    api_base: str = NWS_API_BASE,
    payload: dict[str, Any] | None = None,
) -> HazardPublishResult:
    resolved_payload = payload if payload is not None else fetch_active_alerts_geojson(timeout_seconds=timeout_seconds, api_base=api_base)
    fingerprint = _build_alert_fingerprint(resolved_payload)
    county_path = county_reference_path or default_county_reference_path(data_root)
    zone_path = zone_reference_path or default_zone_reference_path(data_root)
    frame = build_active_hazards_frame(
        resolved_payload,
        county_reference_path=county_path,
        zone_reference_path=zone_path,
    )
    resolved_zone_signature = zone_reference_signature or zone_reference_signature_for_path(zone_path)
    run_id = format_run_id(frame.valid_time, include_minutes=True)

    staging_run_root = data_root / "staging" / NWS_HAZARDS_MODEL_ID / run_id
    if staging_run_root.exists():
        shutil.rmtree(staging_run_root, ignore_errors=True)
    vector_root = staging_run_root / "active" / "vectors"
    vector_root.mkdir(parents=True, exist_ok=True)

    write_json_atomic(vector_root / "fh000.geojson", {"type": "FeatureCollection", "features": frame.features})
    write_json_atomic(staging_run_root / "active" / "fh000.json", _build_frame_sidecar(run_id=run_id, frame=frame))

    promote_run(data_root=data_root, model=NWS_HAZARDS_MODEL_ID, run_id=run_id)
    write_run_manifest(
        data_root=data_root,
        model=NWS_HAZARDS_MODEL_ID,
        run_id=run_id,
        targets=[("active", 0)],
        plugin=NWS_HAZARDS_MODEL,
        metadata={
            "source": "nws_hazards",
            "time_axis_mode": "valid",
            "target_frame_count": 1,
            "available_frame_count": 1,
            "latest_valid_time": frame.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_fingerprint": fingerprint,
            "zone_reference_signature": resolved_zone_signature,
        },
    )
    write_latest_pointer(data_root=data_root, model=NWS_HAZARDS_MODEL_ID, run_id=run_id, source="nws_hazards_publish")
    return HazardPublishResult(
        run_id=run_id,
        published_run_dir=data_root / "published" / NWS_HAZARDS_MODEL_ID / run_id,
        manifest_path=data_root / "manifests" / NWS_HAZARDS_MODEL_ID / f"{run_id}.json",
        frame_count=1,
        variable_ids=["active"],
        fingerprint=fingerprint,
    )