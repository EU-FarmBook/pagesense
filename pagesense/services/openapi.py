from __future__ import annotations

from pagesense.config import AppConfig


def build_openapi_spec(config: AppConfig) -> dict[str, object]:
    servers = [{"url": config.public_base_url}] if config.public_base_url else []
    extract_success = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "example": True},
            "url": {"type": "string", "format": "uri"},
            "resolved_url": {"type": "string", "format": "uri"},
            "text": {"type": "string"},
            "metrics": {"$ref": "#/components/schemas/ExtractMetrics"},
        },
        "required": ["ok", "url", "resolved_url", "text", "metrics"],
    }
    error_response = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "example": False},
            "url": {"type": "string", "format": "uri", "nullable": True},
            "error": {"type": "string"},
        },
        "required": ["ok", "error"],
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "PageSense API", "version": "1.0.0", "description": "Extract readable text from public web pages and PDFs."},
        "servers": servers,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "Token"}},
            "schemas": {
                "ExtractRequest": {"type": "object", "properties": {"url": {"type": "string", "format": "uri"}}, "required": ["url"]},
                "ExtractMetrics": {
                    "type": "object",
                    "properties": {
                        "duration_ms": {"type": "integer"},
                        "downloaded_bytes": {"type": "integer"},
                        "extracted_text_bytes": {"type": "integer"},
                        "content_kind": {"type": "string", "enum": ["html", "pdf", "document", "media", "data"]},
                        "page_count": {"type": "integer", "nullable": True},
                        "duration_seconds": {"type": "number", "nullable": True},
                    },
                    "required": ["duration_ms", "downloaded_bytes", "extracted_text_bytes", "content_kind", "page_count", "duration_seconds"],
                },
                "ExtractSuccess": extract_success,
                "ErrorResponse": error_response,
                "LogEntry": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "source": {"type": "string", "enum": ["api", "ui"]},
                        "method": {"type": "string"},
                        "path": {"type": "string"},
                        "client_ip": {"type": "string", "nullable": True},
                        "forwarded_for": {"type": "string", "nullable": True},
                        "user_agent": {"type": "string", "nullable": True},
                        "referer": {"type": "string", "nullable": True},
                        "target_url": {"type": "string", "nullable": True},
                        "query_string": {"type": "string", "nullable": True},
                        "request_content_type": {"type": "string", "nullable": True},
                        "request_payload": {"type": "object", "nullable": True},
                        "response_status": {"type": "integer", "nullable": True},
                        "ok": {"type": "boolean"},
                        "resolved_url": {"type": "string", "nullable": True},
                        "error_message": {"type": "string", "nullable": True},
                        "duration_ms": {"type": "integer", "nullable": True},
                        "headers": {"type": "object", "nullable": True},
                    },
                },
                "LogsResponse": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean", "example": True},
                        "logs": {"type": "array", "items": {"$ref": "#/components/schemas/LogEntry"}},
                        "count": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                    "required": ["ok", "logs", "count", "limit", "offset"],
                },
            },
        },
        "paths": {
            "/api/extract": {
                "get": {
                    "summary": "Extract readable text from a URL",
                    "parameters": [{"name": "url", "in": "query", "required": True, "schema": {"type": "string", "format": "uri"}, "description": "Public HTTP or HTTPS URL to extract."}],
                    "responses": {
                        "200": {"description": "Successful extraction", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ExtractSuccess"}}}},
                        "400": {"description": "Missing URL", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "422": {"description": "Blocked, invalid, or unsupported URL", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "502": {"description": "Upstream fetch failure", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "504": {"description": "Upstream timeout", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                },
                "post": {
                    "summary": "Extract readable text from a URL",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ExtractRequest"}}, "application/x-www-form-urlencoded": {"schema": {"$ref": "#/components/schemas/ExtractRequest"}}}},
                    "responses": {
                        "200": {"description": "Successful extraction", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ExtractSuccess"}}}},
                        "400": {"description": "Missing URL", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "422": {"description": "Blocked, invalid, or unsupported URL", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "502": {"description": "Upstream fetch failure", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "504": {"description": "Upstream timeout", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                },
            },
            "/api/logs": {
                "get": {
                    "summary": "Read recent request logs",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0, "minimum": 0}},
                        {"name": "source", "in": "query", "schema": {"type": "string", "enum": ["api", "ui"]}},
                        {"name": "ok", "in": "query", "schema": {"type": "boolean"}},
                    ],
                    "responses": {
                        "200": {"description": "Recent logs", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LogsResponse"}}}},
                        "401": {"description": "Unauthorized", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "404": {"description": "Log API disabled", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                }
            },
        },
    }
