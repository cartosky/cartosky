from __future__ import annotations

import os

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.main import _is_allowed_climate_image_proxy_url


def test_climate_image_proxy_rejects_userinfo_host_bypass() -> None:
    assert not _is_allowed_climate_image_proxy_url(
        "https://www.cpc.ncep.noaa.gov@169.254.169.254/latest/meta-data/"
    )


def test_climate_image_proxy_rejects_lookalike_host_prefix() -> None:
    assert not _is_allowed_climate_image_proxy_url(
        "https://www.cpc.ncep.noaa.gov.evil.example/data/indices/image.png"
    )


def test_climate_image_proxy_allows_configured_hosts_and_paths() -> None:
    assert _is_allowed_climate_image_proxy_url(
        "https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii"
    )
    assert _is_allowed_climate_image_proxy_url(
        "https://coralreefwatch.noaa.gov/product/5km/index_5km_ssta.php"
    )
