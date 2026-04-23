from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "test-client")
os.environ.setdefault("TWF_CLIENT_SECRET", "test-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("TWF_SCOPES", "profile forums_posts")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/models-v3")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_oauth_test.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app.config.regions import REGION_PRESETS
from app.main import LOOP_MANIFEST_BBOX
from app.models.aigfs import AIGFS_MODEL
from app.models.aifs import AIFS_MODEL
from app.models.ecmwf import ECMWF_MODEL
from app.models.eps import EPS_MODEL
from app.models.gefs import GEFS_MODEL
from app.models.gfs import GFS_MODEL
from app.models.hrrr import HRRR_MODEL
from app.models.nam import NAM_MODEL
from app.models.nbm import NBM_MODEL
from app.services.builder.cog_writer import REGION_BBOX_3857, REGION_BBOX_4326


EXPECTED_CONUS_BBOX_4326 = (-134.0, 24.0, -60.0, 55.0)
EXPECTED_CONUS_BBOX_3857 = (-14916811.77, 2753408.11, -6679169.45, 7361866.11)
EXPECTED_NA_BBOX_4326 = (-178.0, 5.0, -25.0, 82.0)
EXPECTED_NA_BBOX_3857 = (-19814869.36, 557305.26, -2782987.27, 16967796.94)


def test_conus_bbox_is_consistent_across_builder_and_metadata() -> None:
    assert REGION_BBOX_4326["conus"] == EXPECTED_CONUS_BBOX_4326
    assert REGION_BBOX_3857["conus"] == EXPECTED_CONUS_BBOX_3857

    assert tuple(REGION_PRESETS["conus"]["bbox"]) == EXPECTED_CONUS_BBOX_4326
    assert tuple(LOOP_MANIFEST_BBOX) == EXPECTED_CONUS_BBOX_4326

    for plugin in (HRRR_MODEL, GFS_MODEL, NAM_MODEL, NBM_MODEL):
        region = plugin.get_region("conus")
        assert region is not None
        assert region.bbox_wgs84 == EXPECTED_CONUS_BBOX_4326


def test_na_bbox_is_consistent_across_builder_and_global_model_metadata() -> None:
    assert REGION_BBOX_4326["na"] == EXPECTED_NA_BBOX_4326
    assert REGION_BBOX_3857["na"] == EXPECTED_NA_BBOX_3857
    assert tuple(REGION_PRESETS["na"]["bbox"]) == EXPECTED_NA_BBOX_4326

    for plugin in (GFS_MODEL, GEFS_MODEL, ECMWF_MODEL, AIGFS_MODEL, AIFS_MODEL, EPS_MODEL):
        region = plugin.get_region("na")
        assert region is not None
        assert region.bbox_wgs84 == EXPECTED_NA_BBOX_4326
