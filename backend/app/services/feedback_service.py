from __future__ import annotations

import logging
import os
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FEEDBACK_DB_PATH = Path(os.environ.get("CARTOSKY_FEEDBACK_DB_PATH", "./data/feedback.sqlite3"))
FEEDBACK_RATE_LIMIT_MAX_SUBMISSIONS = 10
FEEDBACK_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
FEEDBACK_CATEGORIES = ("bug", "performance", "feature", "data_accuracy", "ui_ux")

_db_initialized = False
_db_init_lock = threading.Lock()


@dataclass(frozen=True)
class Settings:
    feedback_notify_email: str
    smtp_password: str
    smtp_from: str
    cartosky_admin_base_url: str


def notification_settings_from_env() -> Settings:
    return Settings(
        feedback_notify_email=os.environ.get("FEEDBACK_NOTIFY_EMAIL", "").strip(),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip(),
        cartosky_admin_base_url=os.environ.get("CARTOSKY_ADMIN_BASE_URL", "").strip().rstrip("/"),
    )


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_datetime_filter(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return _format_utc(_parse_utc(stripped))


def _connect() -> sqlite3.Connection:
    _ensure_parent_dir(FEEDBACK_DB_PATH)
    conn = sqlite3.connect(FEEDBACK_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submitted_at TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                member_id INTEGER NOT NULL,
                forums_display_name TEXT NOT NULL,
                page_context TEXT NOT NULL,
                model_context TEXT,
                fhr_context INTEGER,
                user_agent TEXT NOT NULL,
                app_version TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_submitted_at
                ON feedback(submitted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_feedback_member_submitted
                ON feedback(member_id, submitted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_feedback_category_submitted
                ON feedback(category, submitted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_feedback_display_name
                ON feedback(forums_display_name);
            """
        )
        conn.commit()
        _db_initialized = True


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "submitted_at": row["submitted_at"],
        "category": row["category"],
        "message": row["message"],
        "member_id": row["member_id"],
        "forums_display_name": row["forums_display_name"],
        "page_context": row["page_context"],
        "model_context": row["model_context"],
        "fhr_context": row["fhr_context"],
        "user_agent": row["user_agent"],
        "app_version": row["app_version"],
    }


def _validate_category(category: str | None) -> str | None:
    if category is None:
        return None
    normalized = category.strip().lower()
    if normalized not in FEEDBACK_CATEGORIES:
        raise ValueError("category must be one of: bug, performance, feature, data_accuracy, ui_ux")
    return normalized


def _build_where(
    *,
    category: str | None,
    since: str | None,
    until: str | None,
    display_name: str | None,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    normalized_category = _validate_category(category)
    if normalized_category is not None:
        clauses.append("category = ?")
        params.append(normalized_category)
    if since is not None:
        clauses.append("submitted_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("submitted_at <= ?")
        params.append(until)
    if display_name is not None and display_name.strip():
        clauses.append("LOWER(forums_display_name) LIKE ?")
        params.append(f"%{display_name.strip().lower()}%")
    return " AND ".join(clauses), params


def insert_feedback(
    *,
    category: str,
    message: str,
    member_id: int,
    forums_display_name: str,
    page_context: str,
    model_context: str | None,
    fhr_context: int | None,
    user_agent: str,
    app_version: str | None,
) -> dict[str, Any]:
    normalized_category = _validate_category(category)
    submitted_at = _format_utc(_utc_now())
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO feedback(
                submitted_at,
                category,
                message,
                member_id,
                forums_display_name,
                page_context,
                model_context,
                fhr_context,
                user_agent,
                app_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submitted_at,
                normalized_category,
                message,
                member_id,
                forums_display_name,
                page_context,
                model_context,
                fhr_context,
                user_agent,
                app_version,
            ),
        )
        feedback_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    if row is None:
        raise RuntimeError("feedback insert succeeded but row could not be reloaded")
    return _row_to_dict(row)


def check_rate_limit(member_id: int) -> int:
    now = _utc_now()
    cutoff = _format_utc(now - timedelta(seconds=FEEDBACK_RATE_LIMIT_WINDOW_SECONDS))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT submitted_at
            FROM feedback
            WHERE member_id = ? AND submitted_at >= ?
            ORDER BY submitted_at ASC
            """,
            (member_id, cutoff),
        ).fetchall()
    if len(rows) < FEEDBACK_RATE_LIMIT_MAX_SUBMISSIONS:
        return 0
    oldest = _parse_utc(str(rows[0]["submitted_at"]))
    retry_after = FEEDBACK_RATE_LIMIT_WINDOW_SECONDS - int((now - oldest).total_seconds())
    return max(1, retry_after)


def get_paginated(
    *,
    page: int,
    page_size: int,
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    where_sql, params = _build_where(
        category=category,
        since=since,
        until=until,
        display_name=display_name,
    )
    offset = (page - 1) * page_size
    with _connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM feedback WHERE {where_sql}", params).fetchone()[0])
        rows = conn.execute(
            f"""
            SELECT *
            FROM feedback
            WHERE {where_sql}
            ORDER BY submitted_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    return {
        "items": [_row_to_dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def get_summary(
    *,
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    where_sql, params = _build_where(
        category=category,
        since=since,
        until=until,
        display_name=display_name,
    )
    now = _utc_now()
    cutoff_24h = _format_utc(now - timedelta(hours=24))
    cutoff_7d = _format_utc(now - timedelta(days=7))
    by_category = {category_name: 0 for category_name in FEEDBACK_CATEGORIES}
    with _connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM feedback WHERE {where_sql}", params).fetchone()[0])
        last_24h = int(
            conn.execute(
                f"SELECT COUNT(*) FROM feedback WHERE {where_sql} AND submitted_at >= ?",
                [*params, cutoff_24h],
            ).fetchone()[0]
        )
        last_7d = int(
            conn.execute(
                f"SELECT COUNT(*) FROM feedback WHERE {where_sql} AND submitted_at >= ?",
                [*params, cutoff_7d],
            ).fetchone()[0]
        )
        category_rows = conn.execute(
            f"""
            SELECT category, COUNT(*) AS count
            FROM feedback
            WHERE {where_sql}
            GROUP BY category
            ORDER BY category ASC
            """,
            params,
        ).fetchall()
        daily_rows = conn.execute(
            f"""
            SELECT substr(submitted_at, 1, 10) AS date, COUNT(*) AS count
            FROM feedback
            WHERE {where_sql}
            GROUP BY substr(submitted_at, 1, 10)
            ORDER BY date ASC
            """,
            params,
        ).fetchall()
    for row in category_rows:
        by_category[str(row["category"])] = int(row["count"])
    return {
        "summary": {
            "total": total,
            "last_24h": last_24h,
            "last_7d": last_7d,
            "by_category": by_category,
        },
        "daily_volume": [
            {"date": str(row["date"]), "count": int(row["count"])}
            for row in daily_rows
        ],
    }


def get_admin_feedback(
    *,
    page: int,
    page_size: int,
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    paginated = get_paginated(
        page=page,
        page_size=page_size,
        category=category,
        since=since,
        until=until,
        display_name=display_name,
    )
    aggregates = get_summary(
        category=category,
        since=since,
        until=until,
        display_name=display_name,
    )
    return {
        **paginated,
        **aggregates,
        "filters": {
            "category": _validate_category(category),
            "since": since,
            "until": until,
            "display_name": display_name.strip() if display_name and display_name.strip() else None,
        },
    }


def _build_email_body(submission: dict[str, Any], settings: Settings) -> str:
    admin_link = f"{settings.cartosky_admin_base_url}/admin/feedback" if settings.cartosky_admin_base_url else None
    body_lines = [
        f"Category: {submission.get('category')}",
        f"Submitted at: {submission.get('submitted_at')} UTC",
        f"Forums display name: {submission.get('forums_display_name')}",
        f"Member id: {submission.get('member_id')}",
        "",
        "Message:",
        str(submission.get("message") or ""),
        "",
        f"Page context: {submission.get('page_context')}",
        f"Model context: {submission.get('model_context') or 'n/a'}",
        f"Forecast hour context: {submission.get('fhr_context') if submission.get('fhr_context') is not None else 'n/a'}",
        f"App version: {submission.get('app_version') or 'n/a'}",
        f"User agent: {submission.get('user_agent') or 'n/a'}",
    ]
    if admin_link:
        body_lines.extend(["", f"Admin: {admin_link}"])
    return "\n".join(body_lines)


def send_feedback_notification(submission: dict[str, Any], settings: Settings) -> None:
    if not settings.feedback_notify_email or not settings.smtp_password or not settings.smtp_from:
        logger.info("Feedback notification email skipped; Resend destination, API key, or from address is not configured")
        return

    payload = json.dumps({
        "from": settings.smtp_from,
        "to": [settings.feedback_notify_email],
        "subject": f"[CartoSky Beta Feedback] [{submission['category'].upper()}] from {submission['forums_display_name']}",
        "text": _build_email_body(submission, settings),
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.smtp_password}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201):
                logger.error("Resend API returned %s", resp.status)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = "<unable to read response body>"
        logger.error("Failed to send feedback notification email: Resend API returned %s: %s", exc.code, body)
    except Exception as exc:
        logger.error("Failed to send feedback notification email: %s", exc)