from __future__ import annotations

import re
from datetime import datetime, timezone

RUN_ID_RE = re.compile(r"^(?P<day>\d{8})_(?P<hour>\d{2})(?P<minute>\d{2})?z$")


def parse_run_id_datetime(run_id: str) -> datetime | None:
    match = RUN_ID_RE.match(str(run_id).strip())
    if match is None:
        return None
    try:
        day = match.group("day")
        hour = int(match.group("hour"))
        minute_raw = match.group("minute")
        minute = int(minute_raw) if minute_raw is not None else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return datetime(
            int(day[0:4]),
            int(day[4:6]),
            int(day[6:8]),
            hour,
            minute,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def format_run_id(run_dt: datetime, *, include_minutes: bool | None = None) -> str:
    resolved = run_dt.astimezone(timezone.utc)
    if include_minutes is None:
        include_minutes = bool(resolved.minute or resolved.second or resolved.microsecond)
    if include_minutes:
        return resolved.strftime("%Y%m%d_%H%Mz")
    return resolved.strftime("%Y%m%d_%Hz")


def run_id_hour(run_id: str) -> int | None:
    parsed = parse_run_id_datetime(run_id)
    if parsed is None:
        return None
    return int(parsed.hour)
