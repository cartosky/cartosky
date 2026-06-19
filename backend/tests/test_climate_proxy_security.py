from __future__ import annotations

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
