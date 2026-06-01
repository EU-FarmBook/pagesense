#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from pagesense.config import load_config


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="View recent PageSense request logs.")
    parser.add_argument("--db", default=config.request_log_db_path, help="Path to the SQLite request log database.")
    parser.add_argument("--limit", type=int, default=20, help="Number of rows to show.")
    parser.add_argument("--offset", type=int, default=0, help="Rows to skip from newest to oldest.")
    parser.add_argument("--source", choices=["api", "ui"], help="Filter by request source.")
    parser.add_argument("--ok", choices=["true", "false"], help="Filter by success flag.")
    parser.add_argument("--json", action="store_true", help="Print rows as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    query = """
        SELECT
            id,
            created_at,
            source,
            method,
            path,
            client_ip,
            target_url,
            response_status,
            ok,
            duration_ms,
            error_message
        FROM request_logs
    """
    clauses: list[str] = []
    params: list[object] = []
    if args.source:
        clauses.append("source = ?")
        params.append(args.source)
    if args.ok:
        clauses.append("ok = ?")
        params.append(1 if args.ok == "true" else 0)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([max(1, args.limit), max(0, args.offset)])

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]

    for row in rows:
        row["ok"] = bool(row["ok"])

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        return 0

    for row in rows:
        print(
            f"[{row['id']}] {row['created_at']} {row['source'].upper()} "
            f"{row['method']} {row['path']} status={row['response_status']} "
            f"ok={row['ok']} ip={row['client_ip']} duration_ms={row['duration_ms']}"
        )
        if row["target_url"]:
            print(f"  target_url={row['target_url']}")
        if row["error_message"]:
            print(f"  error={row['error_message']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
