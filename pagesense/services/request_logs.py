from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from flask import current_app, request

from pagesense.config import AppConfig


LOGGER = logging.getLogger(__name__)

# Wait up to this long for a competing writer to release the lock before
# raising "database is locked". Multiple gunicorn workers share one SQLite file.
_SQLITE_BUSY_TIMEOUT_MS = 3000


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000)
    # WAL lets readers and a writer proceed concurrently; busy_timeout makes
    # writers wait for the lock instead of failing immediately under contention.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, column: str, coltype: str) -> None:
    # Idempotent and race-safe: several gunicorn workers run this on boot at
    # once, so a check-then-ALTER guard TOCTOUs. Just add it and tolerate the
    # loser's "duplicate column name" error.
    try:
        conn.execute(f"ALTER TABLE request_logs ADD COLUMN {column} {coltype}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def init_request_log_db(config: AppConfig) -> None:
    if not config.request_logging_enabled:
        return

    db_path = Path(config.request_log_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                client_ip TEXT,
                forwarded_for TEXT,
                user_agent TEXT,
                referer TEXT,
                target_url TEXT,
                query_string TEXT,
                request_content_type TEXT,
                request_payload TEXT,
                response_status INTEGER,
                ok INTEGER NOT NULL,
                resolved_url TEXT,
                error_message TEXT,
                duration_ms INTEGER,
                downloaded_bytes INTEGER,
                extracted_text_bytes INTEGER,
                page_count INTEGER,
                headers_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_source ON request_logs(source)")
        for column in ("downloaded_bytes", "extracted_text_bytes", "page_count"):
            _add_column_if_missing(conn, column, "INTEGER")


def get_client_ip() -> tuple[str | None, str | None]:
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip() or None
    if forwarded_for:
        client_ip = forwarded_for.split(",", 1)[0].strip() or request.remote_addr
        return client_ip, forwarded_for
    return request.remote_addr, None


def serialize_request_payload() -> str | None:
    if request.method == "GET":
        return None

    payload: dict[str, object] = {}
    json_payload = request.get_json(silent=True)
    if json_payload is not None:
        payload["json"] = json_payload
    if request.form:
        payload["form"] = request.form.to_dict(flat=True)
    if not payload:
        raw_body = request.get_data(cache=True, as_text=True)
        if raw_body:
            payload["raw"] = raw_body[:4000]

    return json.dumps(payload, ensure_ascii=True, sort_keys=True) if payload else None


def log_request_event(
    *,
    source: str,
    started_at: float,
    target_url: str | None,
    response_status: int,
    ok: bool,
    resolved_url: str | None = None,
    error_message: str | None = None,
    downloaded_bytes: int | None = None,
    extracted_text_bytes: int | None = None,
    page_count: int | None = None,
) -> None:
    config: AppConfig = current_app.extensions["pagesense_config"]
    if not config.request_logging_enabled:
        return

    client_ip, forwarded_for = get_client_ip()
    headers_snapshot = {
        "host": request.headers.get("Host"),
        "x_forwarded_proto": request.headers.get("X-Forwarded-Proto"),
        "x_forwarded_host": request.headers.get("X-Forwarded-Host"),
        "accept": request.headers.get("Accept"),
    }

    try:
        with _connect(config.request_log_db_path) as conn:
            conn.execute(
                """
                INSERT INTO request_logs (
                    created_at, source, method, path, client_ip, forwarded_for,
                    user_agent, referer, target_url, query_string, request_content_type,
                    request_payload, response_status, ok, resolved_url, error_message,
                    duration_ms, downloaded_bytes, extracted_text_bytes, page_count, headers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    source,
                    request.method,
                    request.path,
                    client_ip,
                    forwarded_for,
                    request.headers.get("User-Agent"),
                    request.headers.get("Referer"),
                    target_url,
                    request.query_string.decode("utf-8", errors="replace") or None,
                    request.content_type,
                    serialize_request_payload(),
                    response_status,
                    1 if ok else 0,
                    resolved_url,
                    error_message,
                    int((time.monotonic() - started_at) * 1000),
                    downloaded_bytes,
                    extracted_text_bytes,
                    page_count,
                    json.dumps(headers_snapshot, ensure_ascii=True, sort_keys=True),
                ),
            )
    except sqlite3.Error as exc:
        # Logging must never fail the request it is recording.
        LOGGER.warning("failed to write request log: %s", exc)


def get_logs_from_db(*, limit: int, offset: int, source: str | None = None, ok: int | None = None) -> list[dict[str, object]]:
    config: AppConfig = current_app.extensions["pagesense_config"]
    query = """
        SELECT id, created_at, source, method, path, client_ip, forwarded_for,
               user_agent, referer, target_url, query_string, request_content_type,
               request_payload, response_status, ok, resolved_url, error_message,
               duration_ms, downloaded_bytes, extracted_text_bytes, page_count, headers_json
        FROM request_logs
    """
    clauses: list[str] = []
    params: list[object] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if ok is not None:
        clauses.append("ok = ?")
        params.append(ok)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _connect(config.request_log_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    results: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["ok"] = bool(item["ok"])
        item["headers"] = json.loads(item.pop("headers_json")) if item.get("headers_json") else None
        item["request_payload"] = json.loads(item["request_payload"]) if item.get("request_payload") else None
        results.append(item)
    return results


def is_log_api_authorized() -> bool:
    config: AppConfig = current_app.extensions["pagesense_config"]
    if not config.request_log_api_enabled or not config.request_log_api_token:
        return False

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip() == config.request_log_api_token

    token = request.args.get("token") or request.headers.get("X-Logs-Token", "")
    return token.strip() == config.request_log_api_token
