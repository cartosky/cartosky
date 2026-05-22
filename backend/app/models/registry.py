from __future__ import annotations

import logging

from fastapi import HTTPException

from .base import ModelCapabilities, ModelPlugin
from .hrrr import HRRR_MODEL

logger = logging.getLogger(__name__)

MODEL_REGISTRY: dict[str, ModelPlugin] = {
    HRRR_MODEL.id: HRRR_MODEL,
}

try:
    from .gfs import GFS_MODEL
    MODEL_REGISTRY[GFS_MODEL.id] = GFS_MODEL
except ImportError as exc:
    logger.warning("GFS plugin unavailable (missing dependency): %s", exc)

try:
    from .nam import NAM_MODEL
    MODEL_REGISTRY[NAM_MODEL.id] = NAM_MODEL
except ImportError as exc:
    logger.warning("NAM plugin unavailable (missing dependency): %s", exc)

try:
    from .nbm import NBM_MODEL
    MODEL_REGISTRY[NBM_MODEL.id] = NBM_MODEL
except ImportError as exc:
    logger.warning("NBM plugin unavailable (missing dependency): %s", exc)

try:
    from .mrms import MRMS_MODEL
    MODEL_REGISTRY[MRMS_MODEL.id] = MRMS_MODEL
except ImportError as exc:
    logger.warning("MRMS plugin unavailable (missing dependency): %s", exc)

try:
    from .goes_east import GOES_EAST_MODEL
    MODEL_REGISTRY[GOES_EAST_MODEL.id] = GOES_EAST_MODEL
except ImportError as exc:
    logger.warning("GOES-East plugin unavailable (missing dependency): %s", exc)

try:
    from .rtma_ru import CURRENT_ANALYSIS_MODEL
    MODEL_REGISTRY[CURRENT_ANALYSIS_MODEL.id] = CURRENT_ANALYSIS_MODEL
except ImportError as exc:
    logger.warning("Current Analysis plugin unavailable (missing dependency): %s", exc)

try:
    from .spc import SPC_MODEL
    MODEL_REGISTRY[SPC_MODEL.id] = SPC_MODEL
except ImportError as exc:
    logger.warning("SPC plugin unavailable (missing dependency): %s", exc)

try:
    from .cpc import CPC_MODEL
    MODEL_REGISTRY[CPC_MODEL.id] = CPC_MODEL
except ImportError as exc:
    logger.warning("CPC plugin unavailable (missing dependency): %s", exc)

try:
    from .nws_hazards import NWS_HAZARDS_MODEL
    MODEL_REGISTRY[NWS_HAZARDS_MODEL.id] = NWS_HAZARDS_MODEL
except ImportError as exc:
    logger.warning("NWS Hazards plugin unavailable (missing dependency): %s", exc)

try:
    from .ecmwf import ECMWF_MODEL
    MODEL_REGISTRY[ECMWF_MODEL.id] = ECMWF_MODEL
except ImportError as exc:
    logger.warning("ECMWF plugin unavailable (missing dependency): %s", exc)

try:
    from .aifs import AIFS_MODEL
    MODEL_REGISTRY[AIFS_MODEL.id] = AIFS_MODEL
except ImportError as exc:
    logger.warning("AIFS plugin unavailable (missing dependency): %s", exc)

try:
    from .aigfs import AIGFS_MODEL
    MODEL_REGISTRY[AIGFS_MODEL.id] = AIGFS_MODEL
except ImportError as exc:
    logger.warning("AIGFS plugin unavailable (missing dependency): %s", exc)

try:
    from .gefs import GEFS_MODEL
    MODEL_REGISTRY[GEFS_MODEL.id] = GEFS_MODEL
except ImportError as exc:
    logger.warning("GEFS plugin unavailable (missing dependency): %s", exc)

try:
    from .eps import EPS_MODEL
    MODEL_REGISTRY[EPS_MODEL.id] = EPS_MODEL
except ImportError as exc:
    logger.warning("EPS plugin unavailable (missing dependency): %s", exc)

try:
    from .ndfd import NDFD_MODEL
    MODEL_REGISTRY[NDFD_MODEL.id] = NDFD_MODEL
except ImportError as exc:
    logger.warning("NDFD plugin unavailable (missing dependency): %s", exc)


def get_model(model_id: str) -> ModelPlugin:
    model = MODEL_REGISTRY.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")
    return model


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    model = get_model(model_id)
    capabilities = getattr(model, "capabilities", None)
    if capabilities is None:
        raise HTTPException(
            status_code=500,
            detail=f"Capabilities unavailable for model: {model_id}",
        )
    return capabilities


def list_model_capabilities() -> dict[str, ModelCapabilities]:
    capabilities_by_model: dict[str, ModelCapabilities] = {}
    for model_id, model in MODEL_REGISTRY.items():
        capabilities = getattr(model, "capabilities", None)
        if capabilities is not None:
            capabilities_by_model[model_id] = capabilities
    return capabilities_by_model
