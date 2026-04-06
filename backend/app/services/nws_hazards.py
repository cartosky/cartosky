from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.models.nws_hazards import NWS_HAZARDS_MODEL
from app.services.nws import NWS_API_BASE, NWS_REQUEST_TIMEOUT, NWS_USER_AGENT
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

NWS_HAZARDS_MODEL_ID = "nws_hazards"
NWS_HAZARDS_REGION_ID = "conus"
NWS_ALERTS_ACTIVE_URL = f"{NWS_API_BASE}/alerts/active"
DEFAULT_COUNTY_REFERENCE_RELATIVE_PATH = Path("hazards") / "county_reference.geojson"

STATE_ABBR_TO_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10",
    "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
    "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35",
    "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "PR": "72", "VI": "78", "AS": "60", "GU": "66", "MP": "69",
}


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


EVENT_STYLE_OVERRIDES: dict[str, HazardStyle] = {
    "tornado warning": HazardStyle("tornado_warning", "Tornado Warning", "#dc2626", "#7f1d1d", 390),
    "severe thunderstorm warning": HazardStyle("severe_thunderstorm_warning", "Severe Thunderstorm Warning", "#f59e0b", "#92400e", 370),
    "flash flood warning": HazardStyle("flash_flood_warning", "Flash Flood Warning", "#16a34a", "#14532d", 365),
    "flood warning": HazardStyle("flood_warning", "Flood Warning", "#15803d", "#14532d", 350),
    "blizzard warning": HazardStyle("blizzard_warning", "Blizzard Warning", "#d946ef", "#86198f", 360),
    "winter storm warning": HazardStyle("winter_storm_warning", "Winter Storm Warning", "#c026d3", "#701a75", 355),
    "ice storm warning": HazardStyle("ice_storm_warning", "Ice Storm Warning", "#a855f7", "#6b21a8", 352),
    "red flag warning": HazardStyle("red_flag_warning", "Red Flag Warning", "#f72585", "#9d174d", 358),
    "high wind warning": HazardStyle("high_wind_warning", "High Wind Warning", "#b45309", "#78350f", 345),
    "heat advisory": HazardStyle("heat_advisory", "Heat Advisory", "#f59e0b", "#9a3412", 145),
    "winter weather advisory": HazardStyle("winter_weather_advisory", "Winter Weather Advisory", "#67e8f9", "#0f766e", 140),
    "wind chill advisory": HazardStyle("wind_chill_advisory", "Wind Chill Advisory", "#22d3ee", "#155e75", 138),
    "dense fog advisory": HazardStyle("dense_fog_advisory", "Dense Fog Advisory", "#94a3b8", "#475569", 132),
    "special weather statement": HazardStyle("special_weather_statement", "Special Weather Statement", "#e7cfa2", "#8a6b3d", 70),
    "tornado watch": HazardStyle("tornado_watch", "Tornado Watch", "#fde047", "#854d0e", 285),
    "severe thunderstorm watch": HazardStyle("severe_thunderstorm_watch", "Severe Thunderstorm Watch", "#facc15", "#854d0e", 275),
    "flash flood watch": HazardStyle("flash_flood_watch", "Flash Flood Watch", "#84cc16", "#3f6212", 265),
    "winter storm watch": HazardStyle("winter_storm_watch", "Winter Storm Watch", "#7c3aed", "#4c1d95", 255),
    "marine warning": HazardStyle("marine_warning", "Marine Warning", "#6d28d9", "#4c1d95", 320),
    "small craft advisory": HazardStyle("small_craft_advisory", "Small Craft Advisory", "#60a5fa", "#1d4ed8", 135),
}

SIGNIFICANCE_FALLBACKS: dict[str, HazardStyle] = {
    "warning": HazardStyle("warning", "Warning", "#ef4444", "#7f1d1d", 300),
    "watch": HazardStyle("watch", "Watch", "#facc15", "#854d0e", 200),
    "advisory": HazardStyle("advisory", "Advisory", "#60a5fa", "#1d4ed8", 120),
    "statement": HazardStyle("statement", "Statement", "#d4d4d8", "#52525b", 60),
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
    with httpx.Client(timeout=float(timeout_seconds), follow_redirects=True, headers={
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
    }) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def default_county_reference_path(data_root: Path) -> Path:
    return data_root / DEFAULT_COUNTY_REFERENCE_RELATIVE_PATH


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
            "state": str(props.get("STUSPS") or props.get("state") or "").strip(),
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
    primary = alerts[0]
    if len(alerts) == 1:
        return f"{name}: {primary.style.label}"
    return f"{name}: {primary.style.label} +{len(alerts) - 1} more"


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


def build_active_hazards_frame(
    payload: dict[str, Any],
    *,
    county_reference_path: Path,
    fh: int = 0,
) -> HazardFramePayload:
    counties = load_county_reference(county_reference_path)
    features_raw = payload.get("features")
    if not isinstance(features_raw, list):
        raise NWSHazardsError("Active alerts payload is missing features")

    normalized_alerts = [alert for feature in features_raw if (alert := _normalize_alert(feature)) is not None]
    if not normalized_alerts:
        raise NWSHazardsError("Active alerts payload had no recognized alerts")

    issue_time_candidates = [alert.sent_time for alert in normalized_alerts if alert.sent_time is not None]
    valid_time_candidates = [
        _coerce_datetime(payload.get("updated")),
        *[alert.effective_time for alert in normalized_alerts if alert.effective_time is not None],
        *issue_time_candidates,
    ]
    issue_time = min(issue_time_candidates) if issue_time_candidates else datetime.now(timezone.utc)
    valid_time = next((candidate for candidate in valid_time_candidates if candidate is not None), issue_time)

    county_buckets: dict[str, list[NormalizedHazardAlert]] = {}
    fallback_features: list[dict[str, Any]] = []
    for alert in normalized_alerts:
        resolved_geoids = [geoid for geoid in alert.county_geoids if geoid in counties]
        if resolved_geoids:
            for geoid in resolved_geoids:
                county_buckets.setdefault(geoid, []).append(alert)
            continue
        if alert.geometry is None:
            continue
        fallback_features.append(
            {
                "type": "Feature",
                "properties": {
                    "risk_code": alert.style.key,
                    "risk_label": alert.style.label,
                    "hover_label": alert.headline,
                    "fill": alert.style.fill,
                    "fill_opacity": 0.42,
                    "stroke": alert.style.stroke,
                    "stroke_width": 1.6,
                    "sort_rank": int(alert.style.priority),
                    "alert_count": 1,
                    "alert_ids": [alert.alert_id],
                    "active_hazards": [alert.style.label],
                    "expires_time": alert.expires_time.strftime("%Y-%m-%dT%H:%M:%SZ") if alert.expires_time else None,
                    "area_description": alert.area_description,
                },
                "geometry": alert.geometry,
            }
        )

    county_features: list[dict[str, Any]] = []
    for geoid, alerts in county_buckets.items():
        county = counties.get(geoid)
        if county is None:
            continue
        sorted_alerts = _sort_alerts_by_priority(alerts)
        primary = sorted_alerts[0]
        county_name = str(county.get("name") or geoid)
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
                    "active_hazards": [alert.style.label for alert in sorted_alerts],
                    "area_description": county_name,
                    "expires_time": primary.expires_time.strftime("%Y-%m-%dT%H:%M:%SZ") if primary.expires_time else None,
                },
                "geometry": county["geometry"],
            }
        )

    all_features = county_features + fallback_features
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
    timeout_seconds: float = NWS_REQUEST_TIMEOUT,
    api_base: str = NWS_API_BASE,
) -> HazardPublishResult:
    payload = fetch_active_alerts_geojson(timeout_seconds=timeout_seconds, api_base=api_base)
    fingerprint = _build_alert_fingerprint(payload)
    county_path = county_reference_path or default_county_reference_path(data_root)
    frame = build_active_hazards_frame(payload, county_reference_path=county_path)
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