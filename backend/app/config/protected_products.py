from __future__ import annotations

from typing import TypedDict


class ProtectedProduct(TypedDict):
    product_type: str
    required_feature: str
    label: str


PROTECTED_PRODUCTS: dict[str, ProtectedProduct] = {
    "ecmwf": {
        "product_type": "model",
        "required_feature": "ecmwf",
        "label": "ECMWF",
    },
}
