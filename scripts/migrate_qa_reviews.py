#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

QA_REVIEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    variable_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    forecast_hour INTEGER NOT NULL,
    auto_status TEXT NOT NULL,
    manual_status TEXT,
    auto_checks_json TEXT,
    coverage_fraction REAL,
    valid_pixel_count INTEGER,
    total_pixel_count INTEGER,
    range_min REAL,
    range_max REAL,
    warning_summary TEXT,
    severity TEXT,
    diagnostics_json TEXT,
    last_checked_at INTEGER,
    UNIQUE(model_id, variable_id, run_id, forecast_hour)
);

CREATE INDEX IF NOT EXISTS idx_qa_reviews_updated
    ON qa_reviews(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_reviews_model_run
    ON qa_reviews(model_id, run_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_reviews_status_updated
    ON qa_reviews(auto_status, updated_at DESC);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy qa_reviews rows into the dedicated CartoSky status DB.")
    parser.add_argument("--source-db", required=True, help="Existing SQLite DB that currently contains qa_reviews.")
    parser.add_argument("--target-db", required=True, help="Destination SQLite DB for the separated qa_reviews store.")
    args = parser.parse_args()

    source_db = Path(args.source_db).expanduser().resolve()
    target_db = Path(args.target_db).expanduser().resolve()
    target_db.parent.mkdir(parents=True, exist_ok=True)

    with _connect(source_db) as source, _connect(target_db) as target:
        target.executescript(QA_REVIEWS_SCHEMA)
        rows = source.execute(
            """
            SELECT
                created_at,
                updated_at,
                model_id,
                variable_id,
                run_id,
                forecast_hour,
                auto_status,
                manual_status,
                auto_checks_json,
                coverage_fraction,
                valid_pixel_count,
                total_pixel_count,
                range_min,
                range_max,
                warning_summary,
                severity,
                diagnostics_json,
                last_checked_at
            FROM qa_reviews
            ORDER BY updated_at ASC, model_id ASC, run_id ASC, variable_id ASC, forecast_hour ASC
            """
        ).fetchall()

        for row in rows:
            target.execute(
                """
                INSERT INTO qa_reviews (
                    created_at,
                    updated_at,
                    model_id,
                    variable_id,
                    run_id,
                    forecast_hour,
                    auto_status,
                    manual_status,
                    auto_checks_json,
                    coverage_fraction,
                    valid_pixel_count,
                    total_pixel_count,
                    range_min,
                    range_max,
                    warning_summary,
                    severity,
                    diagnostics_json,
                    last_checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id, variable_id, run_id, forecast_hour)
                DO UPDATE SET
                    updated_at=excluded.updated_at,
                    auto_status=excluded.auto_status,
                    manual_status=excluded.manual_status,
                    auto_checks_json=excluded.auto_checks_json,
                    coverage_fraction=excluded.coverage_fraction,
                    valid_pixel_count=excluded.valid_pixel_count,
                    total_pixel_count=excluded.total_pixel_count,
                    range_min=excluded.range_min,
                    range_max=excluded.range_max,
                    warning_summary=excluded.warning_summary,
                    severity=excluded.severity,
                    diagnostics_json=excluded.diagnostics_json,
                    last_checked_at=excluded.last_checked_at
                """,
                tuple(row),
            )

    print(target_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
