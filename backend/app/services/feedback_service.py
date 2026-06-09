from __future__ import annotations

import logging
import os
import http.client
import json
import sqlite3
import threading
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
                rate_limit_key TEXT,
                clerk_user_id TEXT,
                member_id INTEGER NOT NULL,
                forums_display_name TEXT NOT NULL,
                page_context TEXT NOT NULL,
                model_context TEXT,
                variable_context TEXT,
                run_context TEXT,
                fhr_context INTEGER,
                animation_state_context TEXT,
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
        cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(feedback)").fetchall()}
        if "rate_limit_key" not in cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN rate_limit_key TEXT")
        if "clerk_user_id" not in cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN clerk_user_id TEXT")
        if "variable_context" not in cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN variable_context TEXT")
        if "run_context" not in cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN run_context TEXT")
        if "animation_state_context" not in cols:
            conn.execute("ALTER TABLE feedback ADD COLUMN animation_state_context TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_rate_limit_key_submitted
                ON feedback(rate_limit_key, submitted_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_clerk_user_submitted
                ON feedback(clerk_user_id, submitted_at DESC)
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
        "clerk_user_id": row["clerk_user_id"],
        "member_id": row["member_id"],
        "forums_display_name": row["forums_display_name"],
        "page_context": row["page_context"],
        "model_context": row["model_context"],
        "variable_context": row["variable_context"],
        "run_context": row["run_context"],
        "fhr_context": row["fhr_context"],
        "animation_state_context": row["animation_state_context"],
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
    member_id: int | None,
    forums_display_name: str,
    page_context: str,
    model_context: str | None,
    variable_context: str | None = None,
    run_context: str | None = None,
    fhr_context: int | None,
    animation_state_context: str | None = None,
    user_agent: str,
    app_version: str | None,
    rate_limit_key: str | None = None,
    clerk_user_id: str | None = None,
) -> dict[str, Any]:
    normalized_category = _validate_category(category)
    submitted_at = _format_utc(_utc_now())
    normalized_rate_limit_key = rate_limit_key.strip() if rate_limit_key and rate_limit_key.strip() else None
    normalized_clerk_user_id = clerk_user_id.strip() if clerk_user_id and clerk_user_id.strip() else None
    stored_member_id = member_id if member_id is not None else 0
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO feedback(
                submitted_at,
                category,
                message,
                rate_limit_key,
                clerk_user_id,
                member_id,
                forums_display_name,
                page_context,
                model_context,
                variable_context,
                run_context,
                fhr_context,
                animation_state_context,
                user_agent,
                app_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submitted_at,
                normalized_category,
                message,
                normalized_rate_limit_key,
                normalized_clerk_user_id,
                stored_member_id,
                forums_display_name,
                page_context,
                model_context,
                variable_context,
                run_context,
                fhr_context,
                animation_state_context,
                user_agent,
                app_version,
            ),
        )
        feedback_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    if row is None:
        raise RuntimeError("feedback insert succeeded but row could not be reloaded")
    return _row_to_dict(row)


def check_rate_limit(*, clerk_user_id: str | None = None, member_id: int | None = None) -> int:
    return check_rate_limit_for_identity(clerk_user_id=clerk_user_id, member_id=member_id)


def check_rate_limit_for_identity(
    *,
    rate_limit_key: str | None = None,
    clerk_user_id: str | None = None,
    member_id: int | None = None,
) -> int:
    now = _utc_now()
    cutoff = _format_utc(now - timedelta(seconds=FEEDBACK_RATE_LIMIT_WINDOW_SECONDS))
    normalized_rate_limit_key = rate_limit_key.strip() if rate_limit_key and rate_limit_key.strip() else None
    normalized_clerk_user_id = clerk_user_id.strip() if clerk_user_id and clerk_user_id.strip() else None
    if normalized_rate_limit_key:
        identity_clause = "rate_limit_key = ?"
        identity_value: str | int = normalized_rate_limit_key
    elif normalized_clerk_user_id:
        identity_clause = "clerk_user_id = ?"
        identity_value: str | int = normalized_clerk_user_id
    elif member_id is not None:
        identity_clause = "member_id = ?"
        identity_value = member_id
    else:
        raise ValueError("rate_limit_key, clerk_user_id, or member_id is required for feedback rate limiting")
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT submitted_at
            FROM feedback
            WHERE {identity_clause} AND submitted_at >= ?
            ORDER BY submitted_at ASC
            """,
            (identity_value, cutoff),
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
    run_context = str(submission.get("run_context") or "").strip()
    formatted_run_context = f"{run_context}z" if len(run_context) == 10 and run_context.isdigit() else (run_context or "n/a")
    body_lines = [
        f"Category: {submission.get('category')}",
        f"Submitted at: {submission.get('submitted_at')} UTC",
        f"Name or Username: {submission.get('forums_display_name')}",
        f"Member id: {submission.get('member_id')}",
        f"Clerk user id: {submission.get('clerk_user_id') or 'n/a'}",
        f"Clerk display name: {submission.get('clerk_display_name') or 'n/a'}",
        f"Clerk email: {submission.get('clerk_email_address') or 'n/a'}",
        "",
        "Message:",
        str(submission.get("message") or ""),
        "",
        f"Page context: {submission.get('page_context')}",
        f"Model context: {submission.get('model_context') or 'n/a'}",
        f"Variable context: {submission.get('variable_context') or 'n/a'}",
        f"Run timestamp: {formatted_run_context}",
        f"Forecast hour context: {submission.get('fhr_context') if submission.get('fhr_context') is not None else 'n/a'}",
        f"Animation state: {submission.get('animation_state_context') or 'n/a'}",
        f"App version: {submission.get('app_version') or 'n/a'}",
        f"User agent: {submission.get('user_agent') or 'n/a'}",
    ]
    if admin_link:
        body_lines.extend(["", f"Admin: {admin_link}"])
    return "\n".join(body_lines)


def send_feedback_notification(submission: dict[str, Any], settings: Settings) -> None:
    if not settings.feedback_notify_email or not settings.smtp_password or not settings.smtp_from:
        logger.info("Feedback notification skipped; not configured")
        return

    payload = json.dumps({
        "from": settings.smtp_from,
        "to": [settings.feedback_notify_email],
        "subject": f"[CartoSky Beta Feedback] [{submission['category'].upper()}] from {submission['forums_display_name']}",
        "text": _build_email_body(submission, settings),
    }).encode()

    conn: http.client.HTTPSConnection | None = None
    try:
        conn = http.client.HTTPSConnection("api.resend.com", timeout=10)
        conn.request(
            "POST",
            "/emails",
            body=payload,
            headers={
                "Authorization": f"Bearer {settings.smtp_password}",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status not in (200, 201):
            logger.error("Resend API returned %s: %s", resp.status, body)
        else:
            logger.info("Feedback notification sent, Resend id: %s", body)
    except Exception as exc:
        logger.error("Failed to send feedback notification email: %s", exc)
    finally:
        if conn is not None:
            conn.close()
