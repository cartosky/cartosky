from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.services import cpc_outlook, cpc_poller


def _config(tmp_path: Path) -> cpc_poller.CPCPollerConfig:
    return cpc_poller.CPCPollerConfig(
        data_root=tmp_path,
        poll_seconds=21_600,
        keep_runs=10,
        timeout_seconds=30.0,
    )


def _payload(var_id: str, *, issued_at: datetime, prob: float = 40.0) -> cpc_outlook.CPCOutlookPayload:
    config = cpc_outlook.CPC_PRODUCT_CONFIGS[var_id]
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-100.0, 40.0], [-99.0, 40.0], [-99.0, 41.0], [-100.0, 40.0]]],
        },
        "properties": {"category": "above", "prob": prob, "period": config.period},
    }
    return cpc_outlook.CPCOutlookPayload(
        product=config,
        issued_at=issued_at,
        valid_start=datetime(2026, 6, 24, tzinfo=timezone.utc),
        valid_end=datetime(2026, 6, 28, tzinfo=timezone.utc),
        valid_seas=None,
        features=[feature],
    )


def _daily_and_monthly_bundle(daily_issue: datetime) -> dict[str, cpc_outlook.CPCOutlookPayload]:
    # Monthly outlook is the oldest product, so it pins the bundle run_id.
    return {
        "cpc_610_temp": _payload("cpc_610_temp", issued_at=daily_issue),
        "cpc_1m_temp": _payload("cpc_1m_temp", issued_at=datetime(2026, 6, 18, 12, 45, tzinfo=timezone.utc)),
    }


def test_run_once_noops_when_fingerprint_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    products = _daily_and_monthly_bundle(datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc))
    issue_time = datetime(2026, 6, 18, 12, 45, tzinfo=timezone.utc)
    cpc_outlook.publish_cpc_outlooks(data_root=tmp_path, products=products, issued_at=issue_time)

    monkeypatch.setattr(cpc_poller, "collect_latest_cpc_outlooks", lambda **kwargs: (products, issue_time))

    result = cpc_poller.run_once(_config(tmp_path))

    assert result.action == "noop"
    assert result.published_run_id == "20260618_1245z"


def test_run_once_republishes_when_daily_product_refreshes_under_frozen_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bundle was published when the daily 6-10 day outlook was issued on Jun 20.
    issue_time = datetime(2026, 6, 18, 12, 45, tzinfo=timezone.utc)
    published = _daily_and_monthly_bundle(datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc))
    cpc_outlook.publish_cpc_outlooks(data_root=tmp_path, products=published, issued_at=issue_time)

    # The daily outlook now has a newer issue time, but the monthly outlook is unchanged,
    # so the min-derived run_id is still 20260618_1245z.
    refreshed = _daily_and_monthly_bundle(datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cpc_poller, "collect_latest_cpc_outlooks", lambda **kwargs: (refreshed, issue_time))
    monkeypatch.setattr(
        cpc_poller,
        "publish_latest_cpc_outlooks",
        lambda **kwargs: cpc_outlook.publish_cpc_outlooks(
            data_root=kwargs["data_root"], products=refreshed, issued_at=issue_time
        ),
    )

    result = cpc_poller.run_once(_config(tmp_path))

    assert result.action == "published"
    assert result.published_run_id == "20260618_1245z"

    sidecar = (tmp_path / "published" / "cpc" / "20260618_1245z" / "cpc_610_temp" / "fh000.json").read_text()
    assert "2026-06-24T12:00:00Z" in sidecar


def test_run_once_republishes_when_published_manifest_has_no_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a legacy bundle published before fingerprints existed.
    import json

    issue_time = datetime(2026, 6, 18, 12, 45, tzinfo=timezone.utc)
    products = _daily_and_monthly_bundle(datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc))
    cpc_outlook.publish_cpc_outlooks(data_root=tmp_path, products=products, issued_at=issue_time)

    manifest_path = tmp_path / "manifests" / "cpc" / "20260618_1245z.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["metadata"].pop("source_fingerprint", None)
    manifest_path.write_text(json.dumps(manifest))

    monkeypatch.setattr(cpc_poller, "collect_latest_cpc_outlooks", lambda **kwargs: (products, issue_time))
    monkeypatch.setattr(
        cpc_poller,
        "publish_latest_cpc_outlooks",
        lambda **kwargs: cpc_outlook.publish_cpc_outlooks(
            data_root=kwargs["data_root"], products=products, issued_at=issue_time
        ),
    )

    result = cpc_poller.run_once(_config(tmp_path))

    assert result.action == "published"
