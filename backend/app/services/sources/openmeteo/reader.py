"""``.om`` access layer — the GPLv2 isolation boundary.

``omfiles`` is GPLv2. Per the approved design
(``docs/ECMWF_FAST_PATH_INTEGRATION_DESIGN.md`` §2 "GPLv2 isolation",
Decision 6) it is imported **in-process but only from this module**, and only
lazily from inside functions/constructors. Two consequences worth keeping:

1. Importing ``app.services.sources.openmeteo`` (this module included) must
   never require ``omfiles`` — the API/test environments do not install it.
   Only *calling* into the reader does. ``_require_omfiles()`` is the single
   import site and raises a message naming the dedicated venv.
2. Swapping in a clean-room ``.om`` reader later is a change to this file
   alone: everything above it speaks numpy arrays.

Transport
---------
Naive ``omfiles`` HTTP reads issue **one request per 1024-point chunk**
(2,664 requests / 231 s for a single O1280 variable — measured, prototype
Phase 1). Compressed chunks covering a contiguous index range are contiguous
in the file, so a fixed-block cache in front collapses that to a handful of
range requests (16.85 MB in 3.8 s for the same read). ``BlockCachedHTTPFS`` is
therefore **mandatory**, not an optimisation.

Ported from ``scripts/spikes/openmeteo_9km/omblock.py`` (block cache) and
``phase5_inject.py`` / ``scripts/openmeteo_monitor/monitor.py`` (reader usage).

Shape dispatch
--------------
O1280 files store variables as ``(1, 6599680)`` — two dimensions, one grid.
Regular 0.25° files store ``(721, 1440)``. Dispatch is therefore on
``shape[-1]`` against the *expected point count*, never on ``ndim``
(prototype §4 gotcha 4).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

import numpy as np
import requests

logger = logging.getLogger(__name__)

#: Default HTTP block size for :class:`BlockCachedHTTPFS`. 512 KB is the
#: prototype-measured value (Phase 1): large enough to coalesce the 1024-point
#: chunk reads, small enough that an NA-band read does not pull the globe.
DEFAULT_BLOCK_BYTES = 512 * 1024

_OMFILES_MISSING_MESSAGE = (
    "The 'omfiles' package is required to read Open-Meteo .om files but is not "
    "installed in this environment. It is GPLv2 and therefore lives in the "
    "dedicated fast-path venv, not backend/requirements.txt — install it there "
    "(pip install omfiles) and run the fast-path scheduler from that venv. "
    "Importing app.services.sources.openmeteo does not need omfiles; only "
    "reading .om data does."
)


def _require_omfiles() -> Any:
    """Import and return the ``omfiles`` module, or raise a clear ImportError.

    The **only** ``omfiles`` import site in the codebase (outside the spike
    scripts). Keep it that way.
    """
    try:
        import omfiles  # noqa: PLC0415  (deliberately lazy: GPLv2 isolation)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(_OMFILES_MISSING_MESSAGE) from exc
    return omfiles


# ---------------------------------------------------------------------------
# Block-cached HTTP filesystem
# ---------------------------------------------------------------------------


class BlockCachedHTTPFS:
    """Duck-typed fsspec filesystem serving fixed-size cached blocks.

    ``omfiles`` only needs ``size(path)`` and ``cat_file(path, start, end)``.

    **Use one instance per file.** The cache is unbounded and holds every block
    the reader touched: a full-globe O1280 pass over ~10 variables is ~40 MB
    resident, which is the intended working set for one frame, but sharing an
    instance across frames would grow without bound. ``source.fetch_frame_fields``
    constructs one per call for exactly this reason.

    ``bytes_transferred`` / ``requests_made`` are live counters (the prototype's
    byte-accounting, kept for ops metrics).
    """

    def __init__(
        self,
        *,
        block_bytes: int = DEFAULT_BLOCK_BYTES,
        session: requests.Session | None = None,
        head_timeout: float = 30.0,
        get_timeout: float = 120.0,
    ) -> None:
        if int(block_bytes) <= 0:
            raise ValueError(f"block_bytes must be positive, got {block_bytes!r}")
        self.block_bytes = int(block_bytes)
        self.head_timeout = float(head_timeout)
        self.get_timeout = float(get_timeout)
        self.bytes_transferred = 0
        self.requests_made = 0
        self._owns_session = session is None
        self._session = session if session is not None else requests.Session()
        self._sizes: dict[str, int] = {}
        self._cache: dict[tuple[str, int], bytes] = {}
        self._lock = threading.Lock()

    # -- fsspec-ish surface -------------------------------------------------

    def size(self, path: str) -> int:
        cached = self._sizes.get(path)
        if cached is not None:
            return cached
        response = self._session.head(path, timeout=self.head_timeout, allow_redirects=True)
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length is None:
            raise ValueError(f"No Content-Length in HEAD response for {path}")
        size = int(length)
        self._sizes[path] = size
        return size

    def cat_file(self, path: str, start: int | None = None, end: int | None = None) -> bytes:
        first = 0 if start is None else int(start)
        last = self.size(path) if end is None else int(end)
        if last <= first:
            return b""
        out = bytearray()
        block = self.block_bytes
        for index in range(first // block, (last - 1) // block + 1):
            chunk = self._block(path, index)
            lo = max(0, first - index * block)
            hi = min(len(chunk), last - index * block)
            out += chunk[lo:hi]
        return bytes(out)

    # -- internals ----------------------------------------------------------

    def _block(self, path: str, index: int) -> bytes:
        key = (path, index)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        start = index * self.block_bytes
        end = min(start + self.block_bytes, self.size(path)) - 1
        response = self._session.get(
            path, headers={"Range": f"bytes={start}-{end}"}, timeout=self.get_timeout
        )
        response.raise_for_status()
        payload = response.content
        with self._lock:
            self._cache[key] = payload
            self.bytes_transferred += len(payload)
            self.requests_made += 1
        return payload

    # -- lifecycle ----------------------------------------------------------

    def clear(self) -> None:
        """Drop cached blocks (keeps sizes and the session)."""
        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        self.clear()
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> BlockCachedHTTPFS:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Thin reader wrapper
# ---------------------------------------------------------------------------


class OmFile:
    """A single ``.om`` object, opened over a block-cached HTTP filesystem.

    Wraps ``omfiles.OmFileReader`` so the rest of the source family never
    names an ``omfiles`` type.
    """

    def __init__(self, url: str, fs: BlockCachedHTTPFS, reader: Any) -> None:
        self.url = url
        self.fs = fs
        self._reader = reader
        self._children: dict[str, Any] | None = None

    # -- introspection ------------------------------------------------------

    def _child_map(self) -> dict[str, Any]:
        if self._children is None:
            children: dict[str, Any] = {}
            for index in range(self._reader.num_children):
                child = self._reader.get_child_by_index(index)
                name = getattr(child, "name", None)
                if name:
                    children[str(name)] = child
            self._children = children
        return self._children

    def list_variables(self) -> tuple[str, ...]:
        """Array variable names (scalars such as ``valid_time`` excluded)."""
        return tuple(
            sorted(name for name, child in self._child_map().items() if child.is_array)
        )

    def has_variable(self, name: str) -> bool:
        child = self._child_map().get(name)
        return child is not None and bool(child.is_array)

    def shape(self, name: str) -> tuple[int, ...]:
        return tuple(int(dim) for dim in self._array_child(name).shape)

    def _array_child(self, name: str) -> Any:
        child = self._child_map().get(name)
        if child is None:
            raise KeyError(f"{self.url}: no variable named {name!r}")
        if not child.is_array:
            raise KeyError(f"{self.url}: {name!r} is a scalar, not an array")
        return child

    # -- array reads --------------------------------------------------------

    def read_global_1d(self, name: str, *, expected_points: int | None = None) -> np.ndarray:
        """Read a whole reduced-Gaussian field as a 1D float32 array.

        O1280 files carry ``(1, 6599680)``. The check is on ``shape[-1]``, not
        ``ndim``: a future layout change to ``(6599680,)`` is fine, a change in
        the point count is not (that would mean a different grid).
        """
        child = self._array_child(name)
        shape = tuple(int(dim) for dim in child.shape)
        points = shape[-1]
        if expected_points is not None and points != int(expected_points):
            raise ValueError(
                f"{self.url}: {name!r} has {points} points along the last axis, "
                f"expected {int(expected_points)} — grid mismatch, refusing to decode"
            )
        leading = int(np.prod(shape[:-1])) if len(shape) > 1 else 1
        if leading != 1:
            raise ValueError(
                f"{self.url}: {name!r} shape {shape} is not a single field "
                f"(leading dimensions multiply to {leading})"
            )
        return self._read(child, shape, last_axis=(0, points))

    def read_band_1d(
        self,
        name: str,
        start: int,
        stop: int,
        *,
        expected_points: int | None = None,
    ) -> np.ndarray:
        """Read ``[start, stop)`` of a reduced-Gaussian field as 1D float32.

        The caller owns the offset: values map to global indices
        ``start .. stop-1``. ``grids.apply_sampler(..., offset=start)`` consumes
        it directly.
        """
        child = self._array_child(name)
        shape = tuple(int(dim) for dim in child.shape)
        points = shape[-1]
        if expected_points is not None and points != int(expected_points):
            raise ValueError(
                f"{self.url}: {name!r} has {points} points along the last axis, "
                f"expected {int(expected_points)}"
            )
        lo, hi = int(start), int(stop)
        if not 0 <= lo < hi <= points:
            raise ValueError(
                f"{self.url}: band [{lo}, {hi}) out of range for {name!r} with {points} points"
            )
        return self._read(child, shape, last_axis=(lo, hi))

    def read_grid_2d(self, name: str, nlat: int, nlon: int) -> np.ndarray:
        """Read a regular lat/lon field as a ``(nlat, nlon)`` float32 array.

        Dispatch is on ``shape[-1]``: ``(721, 1440)`` reads natively, a flat
        ``(1, 721*1440)`` layout reads flat and reshapes. Row order is *not*
        normalised here — orientation is a per-run detection
        (``grids.detect_south_to_north``), never an assumption
        (prototype §4 gotcha 1).
        """
        child = self._array_child(name)
        shape = tuple(int(dim) for dim in child.shape)
        rows, cols = int(nlat), int(nlon)
        if shape[-1] == cols:
            leading = int(np.prod(shape[:-1])) if len(shape) > 1 else 1
            if leading != rows:
                raise ValueError(
                    f"{self.url}: {name!r} shape {shape} does not match ({rows}, {cols})"
                )
            values = np.asarray(
                child.read_array((slice(0, rows), slice(0, cols))), dtype=np.float32
            )
            return values.reshape(rows, cols)
        if shape[-1] == rows * cols:
            flat = self._read(child, shape, last_axis=(0, rows * cols))
            return flat.reshape(rows, cols)
        raise ValueError(
            f"{self.url}: {name!r} shape {shape} matches neither ({rows}, {cols}) "
            f"nor a flat {rows * cols}-point layout"
        )

    def _read(
        self, child: Any, shape: tuple[int, ...], last_axis: tuple[int, int]
    ) -> np.ndarray:
        selector = tuple(slice(0, int(dim)) for dim in shape[:-1]) + (
            slice(last_axis[0], last_axis[1]),
        )
        values = np.asarray(child.read_array(selector), dtype=np.float32)
        return values.ravel()

    # -- scalar reads -------------------------------------------------------

    def read_scalar(self, name: str) -> Any:
        child = self._child_map().get(name)
        if child is None:
            raise KeyError(f"{self.url}: no scalar named {name!r}")
        return child.read_scalar()

    @property
    def crs_wkt(self) -> str:
        return str(self.read_scalar("crs_wkt"))

    @property
    def valid_time(self) -> datetime:
        """Valid time as a tz-aware UTC datetime (stored as a unix epoch)."""
        return datetime.fromtimestamp(int(self.read_scalar("valid_time")), tz=timezone.utc)

    @property
    def forecast_reference_time(self) -> datetime:
        """Run (reference) time as a tz-aware UTC datetime."""
        return datetime.fromtimestamp(
            int(self.read_scalar("forecast_reference_time")), tz=timezone.utc
        )

    # -- accounting / lifecycle --------------------------------------------

    @property
    def bytes_transferred(self) -> int:
        return self.fs.bytes_transferred

    @property
    def requests_made(self) -> int:
        return self.fs.requests_made

    def close(self) -> None:
        self.fs.close()

    def __enter__(self) -> OmFile:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_om(
    url: str,
    *,
    fs: BlockCachedHTTPFS | None = None,
    block_bytes: int = DEFAULT_BLOCK_BYTES,
    session: requests.Session | None = None,
) -> OmFile:
    """Open a remote ``.om`` object.

    Creates a dedicated :class:`BlockCachedHTTPFS` unless one is supplied —
    per-file instances are the documented usage (see the class docstring).
    Metadata open costs ~500 KB (one block).
    """
    omfiles = _require_omfiles()
    filesystem = fs if fs is not None else BlockCachedHTTPFS(
        block_bytes=block_bytes, session=session
    )
    reader = omfiles.OmFileReader.from_fsspec(filesystem, url)
    return OmFile(url, filesystem, reader)
