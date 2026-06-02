from __future__ import annotations

import resource
import sys
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - runtime dependency fallback
    psutil = None  # type: ignore[assignment]


_PROC_SELF_STATUS = Path("/proc/self/status")


def current_rss_bytes() -> int:
    if sys.platform.startswith("linux"):
        proc_status_bytes = _current_rss_bytes_from_proc_status()
        if proc_status_bytes is not None:
            return proc_status_bytes
    if psutil is not None:
        try:
            return int(psutil.Process().memory_info().rss)
        except Exception:
            pass
    return peak_rss_bytes()


def peak_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _current_rss_bytes_from_proc_status() -> int | None:
    try:
        for line in _PROC_SELF_STATUS.read_text().splitlines():
            if not line.startswith("VmRSS:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return None
            return int(parts[1]) * 1024
    except Exception:
        return None
    return None