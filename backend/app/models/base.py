from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence


@dataclass(frozen=True)
class RegionSpec:
    id: str
    name: str
    bbox_wgs84: Optional[tuple[float, float, float, float]] = None
    tile_matrix: Optional[str] = None
    clip: bool = False


@dataclass(frozen=True)
class VarSelectors:
    search: list[str] = field(default_factory=list)
    filter_by_keys: dict[str, str] = field(default_factory=dict)
    hints: dict[str, str] = field(default_factory=dict)


SelectorInput = VarSelectors | Mapping[str, str] | Sequence[str] | None


def normalize_selectors(value: SelectorInput) -> VarSelectors:
    if isinstance(value, VarSelectors):
        return value
    if value is None:
        return VarSelectors()
    if isinstance(value, Mapping):
        return VarSelectors(filter_by_keys=dict(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return VarSelectors(search=list(value))
    raise TypeError(f"Unsupported selector type: {type(value)!r}")


@dataclass(frozen=True)
class VarSpec:
    id: str
    name: str
    selectors: SelectorInput = field(default_factory=VarSelectors)
    primary: bool = False
    derived: bool = False
    derive: Optional[str] = None
    kind: Optional[str] = None
    units: Optional[str] = None
    normalize_units: Optional[str] = None
    scale: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", normalize_selectors(self.selectors))


@dataclass(frozen=True)
class VariableCapability:
    var_key: str
    name: str
    selectors: SelectorInput = field(default_factory=VarSelectors)
    supported_build_regions: list[str] = field(default_factory=list)
    primary: bool = False
    derived: bool = False
    derive_strategy_id: Optional[str] = None
    kind: Optional[str] = None
    units: Optional[str] = None
    normalize_units: Optional[str] = None
    scale: Optional[float] = None
    color_map_id: Optional[str] = None
    default_fh: Optional[int] = None
    buildable: bool = True
    order: Optional[int] = None
    group: Optional[str] = None
    legend_title: Optional[str] = None
    conversion: Optional[str] = None
    constraints: dict[str, Any] = field(default_factory=dict)
    frontend: dict[str, Any] = field(default_factory=dict)
    render_substrates: list[str] = field(default_factory=list)
    ensemble: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", normalize_selectors(self.selectors))

    def to_var_spec(self) -> VarSpec:
        return VarSpec(
            id=self.var_key,
            name=self.name,
            selectors=self.selectors,
            primary=self.primary,
            derived=self.derived,
            derive=self.derive_strategy_id,
            kind=self.kind,
            units=self.units,
            normalize_units=self.normalize_units,
            scale=self.scale,
        )


def build_var_specs(variable_catalog: Mapping[str, VariableCapability]) -> dict[str, VarSpec]:
    built: dict[str, VarSpec] = {}
    for key, capability in variable_catalog.items():
        normalized_key = str(key).strip()
        if normalized_key != capability.var_key:
            raise ValueError(
                f"Variable catalog key mismatch: key={normalized_key!r} "
                f"var_key={capability.var_key!r}"
            )
        built[normalized_key] = capability.to_var_spec()
    return built


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    name: str
    product: str = "sfc"
    canonical_region: str = "conus"
    grid_meters_by_region: dict[str, float] = field(default_factory=dict)
    run_discovery: dict[str, Any] = field(default_factory=dict)
    ui_defaults: dict[str, Any] = field(default_factory=dict)
    ui_constraints: dict[str, Any] = field(default_factory=dict)
    variable_catalog: dict[str, VariableCapability] = field(default_factory=dict)
    ensemble: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, capability in self.variable_catalog.items():
            normalized_key = str(key).strip()
            if normalized_key != capability.var_key:
                raise ValueError(
                    f"Invalid variable catalog entry for model={self.model_id!r}: "
                    f"key={normalized_key!r}, var_key={capability.var_key!r}"
                )


@dataclass(frozen=True)
class HerbieRequest:
    model: str
    product: str
    herbie_kwargs: dict[str, Any] = field(default_factory=dict)


class ModelPlugin(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def regions(self) -> Mapping[str, RegionSpec]: ...
    @property
    def vars(self) -> Mapping[str, VarSpec]: ...
    @property
    def product(self) -> str: ...
    @property
    def capabilities(self) -> ModelCapabilities | None: ...

    def get_region(self, region_id: str) -> RegionSpec | None:
        ...

    def get_var(self, var_id: str) -> VarSpec | None:
        ...

    def get_var_capability(self, var_key: str) -> VariableCapability | None:
        ...

    def run_discovery_config(self) -> dict[str, Any]:
        ...

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        ...

    def resolve_probe_var_key(self, requested_probe_var: str | None) -> str | None:
        ...

    def target_fhs(self, cycle_hour: int) -> list[int]:
        ...

    def normalize_var_id(self, var_id: str) -> str:
        ...

    def select_dataarray(self, ds: object, var_id: str) -> object:
        ...

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        ...

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        ensemble_view: str | None = None,
        run_date: datetime | None = None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        ...

    def default_ensemble_view(self, var_key: str) -> str | None:
        ...

    def supported_ensemble_views(self, var_key: str) -> list[str]:
        ...

    def resolve_runtime_var_id(self, var_id: str, ensemble_view: str | None = None) -> str:
        ...

    def search_patterns_for_var(
        self,
        *,
        var_key: str,
        fh: int | None = None,
        product: str | None = None,
        var_spec: VarSpec | None = None,
    ) -> list[str]:
        ...


@dataclass(frozen=True)
class BaseModelPlugin:
    id: str
    name: str
    regions: Mapping[str, RegionSpec] = field(default_factory=dict)
    vars: Mapping[str, VarSpec] = field(default_factory=dict)
    product: str = "sfc"
    capabilities: ModelCapabilities | None = None

    def __post_init__(self) -> None:
        if self.capabilities is None:
            return
        if not self.vars and self.capabilities.variable_catalog:
            object.__setattr__(self, "vars", build_var_specs(self.capabilities.variable_catalog))

    def get_region(self, region_id: str) -> RegionSpec | None:
        return self.regions.get(region_id)

    def get_var(self, var_id: str) -> VarSpec | None:
        return self.vars.get(var_id)

    def get_var_capability(self, var_key: str) -> VariableCapability | None:
        if self.capabilities is None:
            return None
        return self.capabilities.variable_catalog.get(var_key)

    def run_discovery_config(self) -> dict[str, Any]:
        if self.capabilities is None:
            return {}
        return dict(self.capabilities.run_discovery)

    def resolve_probe_var_key(self, requested_probe_var: str | None) -> str | None:
        if isinstance(requested_probe_var, str) and requested_probe_var.strip():
            normalized = self.normalize_var_id(requested_probe_var.strip().lower())
            if self.get_var(normalized) is not None:
                return normalized
        configured = self.run_discovery_config().get("probe_var_key")
        if isinstance(configured, str) and configured.strip():
            normalized = self.normalize_var_id(configured.strip().lower())
            if self.get_var(normalized) is not None:
                return normalized
        return None

    def _var_constraints(self, var_key: str) -> dict[str, Any]:
        capability = self.get_var_capability(var_key)
        if capability is None:
            return {}
        constraints = getattr(capability, "constraints", None)
        if isinstance(constraints, dict):
            return constraints
        return {}

    def scheduled_fhs_for_var(self, var_key: str, cycle_hour: int) -> list[int]:
        fhs = [int(fh) for fh in self.target_fhs(cycle_hour)]
        constraints = self._var_constraints(var_key)

        min_fh = constraints.get("min_fh")
        max_fh = constraints.get("max_fh")
        try:
            min_fh_value = int(min_fh) if min_fh is not None else None
        except (TypeError, ValueError):
            min_fh_value = None
        try:
            max_fh_value = int(max_fh) if max_fh is not None else None
        except (TypeError, ValueError):
            max_fh_value = None

        filtered: list[int] = []
        for fh in fhs:
            if min_fh_value is not None and fh < min_fh_value:
                continue
            if max_fh_value is not None and fh > max_fh_value:
                continue
            filtered.append(fh)
        return filtered

    def target_fhs(self, cycle_hour: int) -> list[int]:
        raise NotImplementedError("target_fhs is not implemented for this model")

    def normalize_var_id(self, var_id: str) -> str:
        return var_id

    def herbie_request(
        self,
        *,
        product: str | None = None,
        var_key: str | None = None,
        ensemble_view: str | None = None,
        run_date: datetime | None = None,
        fh: int | None = None,
        search_pattern: str | None = None,
    ) -> HerbieRequest:
        del var_key, ensemble_view, run_date, fh, search_pattern
        herbie_kwargs: dict[str, Any] = {}
        priority_raw = self.run_discovery_config().get("source_priority")
        if isinstance(priority_raw, str):
            parsed = [item.strip().lower() for item in priority_raw.split(",") if item.strip()]
            if parsed:
                herbie_kwargs["priority"] = parsed
        elif isinstance(priority_raw, Sequence) and not isinstance(priority_raw, (str, bytes)):
            parsed = [str(item).strip().lower() for item in priority_raw if str(item).strip()]
            if parsed:
                herbie_kwargs["priority"] = parsed
        return HerbieRequest(
            model=self.id,
            product=str(product or self.product),
            herbie_kwargs=herbie_kwargs,
        )

    def search_patterns_for_var(
        self,
        *,
        var_key: str,
        fh: int | None = None,
        product: str | None = None,
        var_spec: VarSpec | None = None,
    ) -> list[str]:
        del fh, product
        spec = var_spec or self.get_var(var_key)
        selectors = getattr(spec, "selectors", None)
        if selectors is None:
            return []
        search_list = getattr(selectors, "search", [])
        return [str(pattern) for pattern in search_list if str(pattern).strip()]

    def _ensemble_metadata(self, var_key: str) -> dict[str, Any]:
        capability = self.get_var_capability(var_key)
        if capability is None:
            return {}
        metadata = getattr(capability, "ensemble", None)
        if isinstance(metadata, dict):
            return metadata
        return {}

    def default_ensemble_view(self, var_key: str) -> str | None:
        metadata = self._ensemble_metadata(var_key)
        value = str(metadata.get("default_view") or "").strip().lower()
        return value or None

    def supported_ensemble_views(self, var_key: str) -> list[str]:
        metadata = self._ensemble_metadata(var_key)
        raw = metadata.get("supported_views")
        if not isinstance(raw, (list, tuple)):
            return []
        views: list[str] = []
        for item in raw:
            normalized = str(item or "").strip().lower()
            if normalized and normalized not in views:
                views.append(normalized)
        return views

    def resolve_runtime_var_id(self, var_id: str, ensemble_view: str | None = None) -> str:
        normalized_var = self.normalize_var_id(var_id)
        metadata = self._ensemble_metadata(normalized_var)
        artifact_map = metadata.get("artifact_map")
        normalized_view = str(ensemble_view or "").strip().lower()
        if not normalized_view:
            normalized_view = self.default_ensemble_view(normalized_var) or ""
        if isinstance(artifact_map, dict) and normalized_view:
            resolved = str(artifact_map.get(normalized_view) or "").strip()
            if resolved:
                return resolved
        return normalized_var

    def select_dataarray(self, ds: object, var_id: str) -> object:
        raise NotImplementedError("select_dataarray is not implemented for this model")

    def ensure_latest_cycles(self, keep_cycles: int, *, cache_dir: Path | None = None) -> dict[str, int]:
        raise NotImplementedError("ensure_latest_cycles is not implemented for this model")


def ensemble_member_descriptors(plugin: Any) -> dict[str, dict[str, Any]]:
    """Canonical var_key -> enabled ``ensemble.members`` descriptor.

    The member pipeline's registration shape (Phase 2 design R7 / plan open
    decision #5): per-member data is declared as metadata under the canonical
    buildable variable's capability — e.g. GEFS tmp2m carries
    ``ensemble.members = {"count": 30, "control": True, "prefix": "m",
    "enabled": True}`` — never as per-member catalog entries. Consumers
    (scheduler member pass, meteogram members probe) enumerate member ids
    deterministically from this descriptor instead of globbing directories.

    Only buildable entries with ``enabled: true`` and a positive ``count``
    are returned.
    """
    catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", None)
    if not isinstance(catalog, dict):
        return {}
    descriptors: dict[str, dict[str, Any]] = {}
    for var_key, capability in catalog.items():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        members = ensemble.get("members") if isinstance(ensemble, dict) else None
        if not isinstance(members, dict):
            continue
        try:
            count = int(members.get("count", 0))
        except (TypeError, ValueError):
            continue
        if bool(members.get("enabled", False)) and count > 0:
            descriptors[str(var_key).strip().lower()] = members
    return descriptors


def ensemble_member_ids(descriptor: dict[str, Any]) -> list[str]:
    """Deterministic member id list for a descriptor: ``m01..mNN`` (+
    ``control`` when the descriptor says so). Zero-padded 2-digit, matching
    the member pipeline plan Section 4.1 naming."""
    try:
        count = int(descriptor.get("count", 0))
    except (TypeError, ValueError):
        return []
    prefix = str(descriptor.get("prefix", "m") or "m").strip().lower()
    members = [f"{prefix}{i:02d}" for i in range(1, count + 1)]
    if bool(descriptor.get("control", False)):
        members.append("control")
    return members


# ── Ensemble stats products (member pipeline Phase 6 — Tier 2) ──────────────
def ensemble_stats_descriptors(plugin: Any) -> dict[str, dict[str, Any]]:
    """Canonical var_key -> enabled ``ensemble.stats`` descriptor.

    The stats twin of :func:`ensemble_member_descriptors` (stats design §3):
    percentile/probability map products are declared as metadata under the
    canonical buildable variable's capability — e.g.
    ``ensemble.stats = {"percentiles": [10, 25, 50, 75, 90],
    "prob_thresholds": [0.10, 0.25, 0.50, 1.00, 1.50, 2.00],
    "enabled": True}`` — never as per-product catalog entries.
    ``prob_thresholds`` are exceedance (``P(> x)``); two-sided variables
    (temperature, B2) add ``prob_lt_thresholds`` for non-exceedance
    (``P(< x)``). Adding a variable or threshold later is a descriptor
    edit, nothing else.

    Only buildable entries with ``enabled: true`` and at least one product
    are returned.
    """
    catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", None)
    if not isinstance(catalog, dict):
        return {}
    descriptors: dict[str, dict[str, Any]] = {}
    for var_key, capability in catalog.items():
        if not bool(getattr(capability, "buildable", False)):
            continue
        ensemble = getattr(capability, "ensemble", None)
        stats = ensemble.get("stats") if isinstance(ensemble, dict) else None
        if not isinstance(stats, dict):
            continue
        if not bool(stats.get("enabled", False)):
            continue
        percentiles = stats.get("percentiles") or []
        thresholds = stats.get("prob_thresholds") or []
        lt_thresholds = stats.get("prob_lt_thresholds") or []
        if not percentiles and not thresholds and not lt_thresholds:
            continue
        descriptors[str(var_key).strip().lower()] = stats
    return descriptors


def format_prob_threshold(value: float | int) -> str:
    """Canonical threshold token for probability var ids (plan §4.1: display
    units, decimal point as ``p``): ``0.5 -> "0p5"``, ``0.25 -> "0p25"``,
    ``1.0 -> "1p0"``, ``6 -> "6p0"``. Always carries a ``p`` (integers get
    ``p0``) so the token is unambiguous and round-trippable."""
    text = f"{float(value):g}"
    if "." in text:
        return text.replace(".", "p")
    return f"{text}p0"


def parse_prob_threshold(token: str) -> float:
    """Inverse of :func:`format_prob_threshold` (``"0p5" -> 0.5``)."""
    return float(str(token).replace("p", "."))


def ensemble_stats_product_ids(base_var: str, descriptor: dict[str, Any]) -> dict[str, str]:
    """Ordered product_key -> runtime var id map for one stats descriptor.

    Product keys are the id suffixes (``"p50"``, ``"prob_gt_0p5"``,
    ``"prob_lt_32p0"``) — the same tokens the viewer's product selector
    uses. Percentiles first (ascending), then non-exceedance thresholds
    (ascending — the cold rungs read 0/20/32), then exceedance thresholds
    (ascending), matching the plan §4.1 naming exactly.

    Thresholds are display-unit values and must be >= 0: the token grammar
    (``\\d+p\\d+`` across packing, canary, and frontend classification) has
    no sign — a negative threshold would mint an unclassifiable id.
    """
    base = str(base_var).strip().lower()
    products: dict[str, str] = {}
    for q in sorted(int(q) for q in (descriptor.get("percentiles") or [])):
        key = f"p{q:02d}"
        products[key] = f"{base}__{key}"
    for threshold in sorted(float(t) for t in (descriptor.get("prob_lt_thresholds") or [])):
        if threshold < 0:
            raise ValueError(
                f"Negative prob_lt threshold {threshold} on {base}: the id "
                "grammar carries no sign (extend it deliberately first)"
            )
        key = f"prob_lt_{format_prob_threshold(threshold)}"
        products[key] = f"{base}__{key}"
    for threshold in sorted(float(t) for t in (descriptor.get("prob_thresholds") or [])):
        if threshold < 0:
            raise ValueError(
                f"Negative prob_gt threshold {threshold} on {base}: the id "
                "grammar carries no sign (extend it deliberately first)"
            )
        key = f"prob_gt_{format_prob_threshold(threshold)}"
        products[key] = f"{base}__{key}"
    return products


# The single suffix grammar for ensemble-derived runtime var ids (plan §4.1:
# written ONCE, shared by packing resolution, manifest tooling, capabilities
# serialization, and canary scope classification). ``prob_lt`` (reserved by
# the Phase 6 grammar) is IMPLEMENTED as of B2: temperature "below"
# thresholds — P(< 32°F) etc.
_ENSEMBLE_SUFFIX_RE = re.compile(
    r"^(?P<base>.+)__(?P<suffix>mean|control|m\d{2}|p\d{2}|prob_(?:gt|lt)_\d+p\d+)$"
)


def classify_ensemble_var_id(var_id: str) -> tuple[str, str, Any] | None:
    """Classify a runtime var id's ensemble suffix.

    Returns ``(base_var, kind, detail)`` where kind is one of ``"mean"``,
    ``"control"``, ``"member"`` (detail = member token), ``"percentile"``
    (detail = int), ``"prob_gt"`` or ``"prob_lt"`` (detail = float
    threshold). ``None`` for ids with no ensemble suffix.
    """
    match = _ENSEMBLE_SUFFIX_RE.match(str(var_id or "").strip().lower())
    if match is None:
        return None
    base = match.group("base")
    suffix = match.group("suffix")
    if suffix == "mean":
        return (base, "mean", None)
    if suffix == "control":
        return (base, "control", None)
    if suffix.startswith("m") and suffix[1:].isdigit():
        return (base, "member", suffix)
    if suffix.startswith("p") and suffix[1:].isdigit():
        return (base, "percentile", int(suffix[1:]))
    if suffix.startswith("prob_gt_"):
        return (base, "prob_gt", parse_prob_threshold(suffix[len("prob_gt_"):]))
    if suffix.startswith("prob_lt_"):
        return (base, "prob_lt", parse_prob_threshold(suffix[len("prob_lt_"):]))
    return None
