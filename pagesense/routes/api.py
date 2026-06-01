from __future__ import annotations

import time

import requests
from flask import Blueprint, Response, current_app, jsonify, request

from pagesense.services.extractor import extract_text_from_url
from pagesense.services.openapi import build_openapi_spec
from pagesense.services.request_logs import get_logs_from_db, is_log_api_authorized, log_request_event


api_bp = Blueprint("api", __name__)


@api_bp.route("/api/extract", methods=["GET", "POST"])
def api_extract():
    started_at = time.monotonic()
    if request.method == "GET":
        url = request.args.get("url")
    else:
        payload = request.get_json(silent=True) or {}
        url = payload.get("url") or request.form.get("url")

    if not url:
        response = jsonify({"ok": False, "error": "Missing 'url' parameter"})
        status = 400
        log_request_event(source="api", started_at=started_at, target_url=None, response_status=status, ok=False, error_message="Missing 'url' parameter")
        return response, status

    try:
        result = extract_text_from_url(url)
        metrics = {
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "downloaded_bytes": result.downloaded_bytes,
            "extracted_text_bytes": result.extracted_text_bytes,
            "content_kind": result.content_kind,
            "page_count": result.page_count,
            "duration_seconds": result.duration_seconds,
        }
        response = jsonify({
            "ok": True,
            "url": url,
            "resolved_url": result.resolved_url,
            "text": result.text,
            "metrics": metrics,
        })
        status = 200
        log_request_event(
            source="api",
            started_at=started_at,
            target_url=url,
            response_status=status,
            ok=True,
            resolved_url=result.resolved_url,
            downloaded_bytes=result.downloaded_bytes,
            extracted_text_bytes=result.extracted_text_bytes,
            page_count=result.page_count,
        )
        return response, status
    except ValueError as exc:
        response = jsonify({"ok": False, "url": url, "error": str(exc)})
        status = 422
        log_request_event(source="api", started_at=started_at, target_url=url, response_status=status, ok=False, error_message=str(exc))
        return response, status
    except requests.exceptions.Timeout:
        response = jsonify({"ok": False, "url": url, "error": "Upstream timeout"})
        status = 504
        log_request_event(source="api", started_at=started_at, target_url=url, response_status=status, ok=False, error_message="Upstream timeout")
        return response, status
    except requests.exceptions.RequestException as exc:
        message = f"Network/HTTP error: {exc}"
        response = jsonify({"ok": False, "url": url, "error": message})
        status = 502
        log_request_event(source="api", started_at=started_at, target_url=url, response_status=status, ok=False, error_message=message)
        return response, status
    except Exception as exc:
        message = f"Failed to extract text: {exc}"
        response = jsonify({"ok": False, "url": url, "error": message})
        status = 500
        log_request_event(source="api", started_at=started_at, target_url=url, response_status=status, ok=False, error_message=message)
        return response, status


@api_bp.route("/api/logs", methods=["GET"])
def api_logs():
    started_at = time.monotonic()
    config = current_app.extensions["pagesense_config"]

    if not config.request_log_api_enabled:
        return jsonify({"ok": False, "error": "Log API is disabled."}), 404
    if not is_log_api_authorized():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0

    source = request.args.get("source")
    if source not in {None, "", "api", "ui"}:
        source = None

    ok_param = request.args.get("ok")
    ok_filter = None if ok_param in {None, ""} else (1 if ok_param.strip().lower() in {"1", "true", "yes"} else 0)

    logs = get_logs_from_db(limit=limit, offset=offset, source=source, ok=ok_filter)
    response = jsonify({"ok": True, "logs": logs, "count": len(logs), "limit": limit, "offset": offset})
    log_request_event(source="api", started_at=started_at, target_url=None, response_status=200, ok=True)
    return response, 200


@api_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    config = current_app.extensions["pagesense_config"]
    return jsonify(build_openapi_spec(config))


@api_bp.route("/docs", methods=["GET"])
def swagger_ui():
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PageSense API Docs</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #fafafa; }
    .topbar { display: none; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis],
    });
  </script>
</body>
</html>
""".strip()
    return Response(html, mimetype="text/html")
