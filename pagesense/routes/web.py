from __future__ import annotations

import time

import requests
from flask import Blueprint, render_template, request

from pagesense.services.extractor import extract_text_from_url
from pagesense.services.request_logs import log_request_event


web_bp = Blueprint("web", __name__)


def format_bytes(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count / (1024 * 1024):.2f} MB"


def format_duration_seconds(duration_seconds: float | None) -> str | None:
    if duration_seconds is None:
        return None
    if duration_seconds < 60:
        return f"{duration_seconds:.1f}s"
    minutes, seconds = divmod(int(duration_seconds), 60)
    return f"{minutes}m {seconds}s"


@web_bp.route("/", methods=["GET", "POST"])
def index():
    started_at = time.monotonic()
    text_result = None
    error = None
    raw_url = None
    response_status = 200
    resolved_url = None
    result_meta = None

    if request.method == "POST":
        raw_url = (request.form.get("url") or "").strip()
        try:
            result = extract_text_from_url(raw_url)
            resolved_url = result.resolved_url
            text_result = result.text
            result_meta = {
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "downloaded_bytes": result.downloaded_bytes,
                "downloaded_human": format_bytes(result.downloaded_bytes),
                "extracted_text_bytes": result.extracted_text_bytes,
                "extracted_human": format_bytes(result.extracted_text_bytes),
                "content_kind": result.content_kind,
                "page_count": result.page_count,
                "duration_seconds": result.duration_seconds,
                "duration_human": format_duration_seconds(result.duration_seconds),
            }
        except ValueError as exc:
            error = str(exc)
            response_status = 422
        except requests.exceptions.RequestException as exc:
            error = f"Network/HTTP error: {exc}"
            response_status = 502
        except Exception as exc:
            error = f"Failed to extract text: {exc}"
            response_status = 500

    log_request_event(
        source="ui",
        started_at=started_at,
        target_url=raw_url,
        response_status=response_status,
        ok=error is None,
        resolved_url=resolved_url,
        error_message=error,
        downloaded_bytes=(result_meta or {}).get("downloaded_bytes"),
        extracted_text_bytes=(result_meta or {}).get("extracted_text_bytes"),
        page_count=(result_meta or {}).get("page_count"),
    )
    return render_template("index.html", text_result=text_result, error=error, result_meta=result_meta)
