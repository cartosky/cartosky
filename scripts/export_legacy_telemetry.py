#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEGACY_TABLES = (
    "perf_events",
    "usage_events",
    "synthetic_perf_runs",
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row["name"]) for row in rows]


def _table_rows(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM {table_name} ORDER BY id ASC").fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_readme(path: Path, *, db_path: Path, export_dir: Path, generated_at: str, counts: dict[str, int]) -> None:
    lines = [
        "# Legacy Telemetry Archive",
        "",
        f"- generated_at_utc: {generated_at}",
        f"- source_db: {db_path}",
        f"- export_dir: {export_dir}",
        "",
        "## Tables",
        "",
    ]
    for table_name in LEGACY_TABLES:
        lines.append(f"- `{table_name}`: {counts.get(table_name, 0)} rows")
    lines.extend(
        [
            "",
            "## Context",
            "",
            "- This archive covers the legacy custom frontend telemetry tables retired during Phase 6.",
            "- `qa_reviews` is intentionally not exported here because it remains part of the surviving first-party status/QA path.",
            "- JSON files preserve the full row payloads. CSV files provide operator-friendly tabular snapshots.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export legacy CartoSky telemetry tables to JSON and CSV archives.")
    parser.add_argument("--db", required=True, help="Path to the legacy telemetry SQLite database.")
    parser.add_argument("--outdir", required=True, help="Directory where the archive should be written.")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    output_root = Path(args.outdir).expanduser().resolve()
    generated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    export_dir = output_root / f"legacy_telemetry_{generated_at}"
    export_dir.mkdir(parents=True, exist_ok=False)

    counts: dict[str, int] = {}

    with _connect(db_path) as conn:
        for table_name in LEGACY_TABLES:
            if not _table_exists(conn, table_name):
                counts[table_name] = 0
                continue
            columns = _table_columns(conn, table_name)
            rows = _table_rows(conn, table_name)
            counts[table_name] = len(rows)
            _write_json(
                export_dir / f"{table_name}.json",
                {
                    "table": table_name,
                    "row_count": len(rows),
                    "columns": columns,
                    "rows": rows,
                },
            )
            _write_csv(export_dir / f"{table_name}.csv", columns, rows)

    _write_readme(
        export_dir / "README.md",
        db_path=db_path,
        export_dir=export_dir,
        generated_at=generated_at,
        counts=counts,
    )

    print(export_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
