from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import spc_poller, spc_publish


def _frame(
    *,
    fh: int,
    day_label: str,
    issue_time: datetime,
    feature_count: int = 1,
) -> spc_publish.SPCFramePayload:
    features = [
        {
            "type": "Feature",
            "properties": {"risk_code": 3, "risk_label": "Slight", "fill": "#FFFF00", "sort_rank": 3},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-98.0, 35.0], [-97.0, 35.0], [-97.0, 36.0], [-98.0, 35.0]]],
            },
        }
    ] * feature_count
    return spc_publish.SPCFramePayload(
        fh=fh,
        day_label=day_label,
        valid_time=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
        issue_time=issue_time,
        features=features,
    )


def _write_published_bundle(
    tmp_path: Path,
    *,
    run_id: str,
    products: dict[str, list[spc_publish.SPCFramePayload]],
) -> None:
    spc_publish.publish_spc_products_bundle(
        data_root=tmp_path,
        products=products,
        issue_time=datetime.strptime(run_id.replace("z", ""), "%Y%m%d_%H%M").replace(tzinfo=timezone.utc),
    )


def test_run_once_noops_when_fingerprint_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issue_time = datetime(2026, 6, 11, 5, 34, tzinfo=timezone.utc)
    run_id = "20260611_0534z"
    products = {
        "convective": [
            _frame(fh=0, day_label="Day 1", issue_time=datetime(2026, 6, 11, 6, 19, tzinfo=timezone.utc)),
            _frame(fh=1, day_label="Day 2", issue_time=issue_time),
        ]
    }
    _write_published_bundle(tmp_path, run_id=run_id, products=products)

    monkeypatch.setattr(
        spc_poller,
        "collect_latest_spc_products",
        lambda **kwargs: (products, issue_time),
    )

    result = spc_poller.run_once(
        spc_poller.SPCPollerConfig(
            data_root=tmp_path,
            poll_seconds=900,
            keep_runs=10,
            timeout_seconds=30.0,
            base_url=spc_publish.SPC_LAYER_BASE_URL,
        )
    )

    assert result.action == "noop"
    assert result.published_run_id == run_id


def test_run_once_republishes_when_day1_issue_time_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_issue_time = datetime(2026, 6, 11, 5, 34, tzinfo=timezone.utc)
    run_id = "20260611_0534z"
    published_products = {
        "convective": [
            _frame(fh=0, day_label="Day 1", issue_time=datetime(2026, 6, 11, 6, 19, tzinfo=timezone.utc)),
            _frame(fh=1, day_label="Day 2", issue_time=bundle_issue_time),
        ]
    }
    _write_published_bundle(tmp_path, run_id=run_id, products=published_products)

    updated_products = {
        "convective": [
            _frame(
                fh=0,
                day_label="Day 1",
                issue_time=datetime(2026, 6, 11, 12, 58, tzinfo=timezone.utc),
                feature_count=2,
            ),
            _frame(fh=1, day_label="Day 2", issue_time=bundle_issue_time),
        ]
    }

    monkeypatch.setattr(
        spc_poller,
        "collect_latest_spc_products",
        lambda **kwargs: (updated_products, bundle_issue_time),
    )

    result = spc_poller.run_once(
        spc_poller.SPCPollerConfig(
            data_root=tmp_path,
            poll_seconds=900,
            keep_runs=10,
            timeout_seconds=30.0,
            base_url=spc_publish.SPC_LAYER_BASE_URL,
        )
    )

    assert result.action == "published"
    assert result.published_run_id == run_id

    sidecar = json.loads(
        (tmp_path / "published" / "spc" / run_id / "convective" / "fh000.json").read_text()
    )
    assert sidecar["issue_time"] == "2026-06-11T12:58:00Z"

    manifest = json.loads((tmp_path / "manifests" / "spc" / f"{run_id}.json").read_text())
    assert manifest["metadata"]["source_fingerprint"] == spc_publish.build_spc_products_fingerprint(updated_products)
