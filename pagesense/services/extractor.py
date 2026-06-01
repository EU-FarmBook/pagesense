from __future__ import annotations

import ipaddress
import logging
import json
import random
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Comment
from flask import current_app

from pagesense.browser import (
    extract_docx_content_from_bytes,
    extract_pdf_content_from_bytes,
    extract_pptx_content_from_bytes,
    fetch_with_browser,
)
from pagesense.config import AppConfig


LOGGER = logging.getLogger(__name__)
LAST_DOMAIN_CALL: dict[str, float] = {}


@dataclass(frozen=True)
class ExtractionResult:
    resolved_url: str
    text: str
    downloaded_bytes: int
    extracted_text_bytes: int
    content_kind: str
    page_count: int | None = None
    duration_seconds: float | None = None


def get_config() -> AppConfig:
    return current_app.extensions["pagesense_config"]


def get_private_networks() -> list[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(net) for net in get_config().private_nets]


def _get_private_networks_for_config(config: AppConfig) -> list[ipaddress._BaseNetwork]:
    return [ipaddress.ip_network(net) for net in config.private_nets]


def _is_allowed_url_for_config(raw_url: str, config: AppConfig) -> bool:
    parsed = urlparse((raw_url or "").strip())
    return bool(
        parsed.scheme in config.allowed_schemes
        and parsed.hostname
        and not _is_private_host_for_config(parsed.hostname, config)
        and not _is_blocked_media_host_for_config(parsed.hostname, config)
    )


def _is_blocked_media_host_for_config(hostname: str | None, config: AppConfig) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    return any(host == pattern or host.endswith(f".{pattern}") for pattern in config.blocked_host_patterns)


def is_blocked_media_host(hostname: str | None) -> bool:
    return _is_blocked_media_host_for_config(hostname, get_config())


def _is_private_host_for_config(hostname: str | None, config: AppConfig) -> bool:
    if not hostname:
        return True

    private_nets = _get_private_networks_for_config(config)
    try:
        ip = ipaddress.ip_address(hostname)
        return any(ip in net for net in private_nets)
    except ValueError:
        pass

    try:
        addrinfo = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False

    for ip_text in {item[4][0] for item in addrinfo if item[4]}:
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if any(ip in net for net in private_nets):
            return True
    return False


def is_private_host(hostname: str | None) -> bool:
    return _is_private_host_for_config(hostname, get_config())


def is_allowed_url(raw_url: str) -> bool:
    return _is_allowed_url_for_config(raw_url, get_config())


def validate_url(raw_url: str) -> tuple[str, str]:
    config = get_config()
    parsed = urlparse((raw_url or "").strip())
    if parsed.scheme not in config.allowed_schemes or not parsed.netloc:
        raise ValueError("Please enter a valid http(s) URL (e.g., https://example.com/page).")
    if _is_blocked_media_host_for_config(parsed.hostname, config):
        raise ValueError("Video platform URLs are not supported.")
    if _is_private_host_for_config(parsed.hostname, config):
        raise ValueError("Private/loopback addresses are not allowed.")
    return parsed.geturl(), parsed.netloc.lower()


def ensure_within_deadline(started_at: float) -> None:
    if time.monotonic() - started_at > get_config().extraction_deadline_seconds:
        raise ValueError("Extraction exceeded the 30-second limit.")


def read_response_bytes(resp: requests.Response, *, byte_limit: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    resp.raw.decode_content = False
    for chunk in resp.raw.stream(16384, decode_content=False):
        if not chunk:
            continue
        total += len(chunk)
        if total > byte_limit:
            raise ValueError(f"{label} too large (over {byte_limit // 1_000_000} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def looks_like_pdf_response(*, content_type: str, path: str, content_disposition: str | None = None) -> bool:
    lowered_disposition = (content_disposition or "").lower()
    if "application/pdf" in content_type:
        return True
    if path.endswith(".pdf"):
        if "application/octet-stream" in content_type or "application/force-download" in content_type:
            return True
    if ".pdf" in lowered_disposition:
        return True
    return False


def has_pdf_magic_prefix(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def looks_like_docx_response(*, content_type: str, path: str, content_disposition: str | None = None) -> bool:
    lowered_disposition = (content_disposition or "").lower()
    return (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in content_type
        or path.endswith(".docx")
        or ".docx" in lowered_disposition
    )


def looks_like_pptx_response(*, content_type: str, path: str, content_disposition: str | None = None) -> bool:
    lowered_disposition = (content_disposition or "").lower()
    return (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" in content_type
        or path.endswith(".pptx")
        or ".pptx" in lowered_disposition
    )


def looks_like_media_response(*, content_type: str, path: str, content_disposition: str | None = None) -> bool:
    lowered_disposition = (content_disposition or "").lower()
    media_tokens = (
        "audio/",
        "video/",
        "application/ogg",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    )
    media_extensions = (
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga",
        ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".ts",
    )
    if any(token in content_type for token in media_tokens):
        return True
    if path.endswith(media_extensions):
        return True
    return any(ext in lowered_disposition for ext in media_extensions)


def looks_like_data_response(*, content_type: str, path: str, content_disposition: str | None = None) -> bool:
    lowered_disposition = (content_disposition or "").lower()
    data_tokens = (
        "text/plain",
        "text/csv",
        "application/json",
        "application/xml",
        "text/xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/csv",
    )
    data_extensions = (
        ".xlsx", ".xls", ".csv", ".json", ".txt", ".xml", ".tsv",
    )
    if any(token in content_type for token in data_tokens):
        return True
    if path.endswith(data_extensions):
        return True
    return any(ext in lowered_disposition for ext in data_extensions)


def probe_media_duration_from_url(url: str, timeout_seconds: int) -> float | None:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None

    try:
        payload = json.loads(proc.stdout)
        duration = ((payload.get("format") or {}).get("duration"))
        return float(duration) if duration is not None else None
    except (ValueError, TypeError):
        return None


def fetch_simple_html(url: str, config: AppConfig) -> tuple[str, str]:
    response = requests.get(
        url,
        headers={"User-Agent": random.choice(config.ua_pool), "Accept": "text/html,application/xhtml+xml"},
        timeout=config.request_timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    final_url = response.url
    final_parsed = urlparse(final_url)
    if _is_blocked_media_host_for_config(final_parsed.hostname, config):
        raise ValueError("Video platform URLs are not supported.")
    if _is_private_host_for_config(final_parsed.hostname, config):
        raise ValueError("Redirected to a private/loopback address, which is not allowed.")
    return final_url, response.text


def extract_clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body if soup.body else soup

    for node in body.find_all(string=lambda t: isinstance(t, Comment)):
        node.extract()

    for tag in body.find_all([
        "script", "style", "noscript", "template", "iframe", "frame", "frameset", "object",
        "embed", "canvas", "svg", "video", "audio", "picture", "source", "figure", "figcaption",
        "form", "button", "input", "select", "textarea", "label", "nav", "header", "footer",
        "aside", "menu", "dialog",
    ]):
        tag.decompose()

    for el in list(body.select(
        "[role=banner],[role=navigation],[role=complementary],[role=contentinfo],"
        "[role=search],[role=dialog],[role=alert],[role=alertdialog]"
    )):
        el.decompose()

    for el in list(body.select("[hidden], [style*='display:none'], [style*='visibility:hidden']")):
        el.decompose()

    substrings = [
        "cookie", "consent", "gdpr", "subscribe", "signup", "newsletter", "modal", "overlay",
        "paywall", "meter", "gate", "promo", "breadcrumb", "share", "social", "toolbar",
        "footer", "header", "nav", "sidebar",
    ]

    def has_noise_marker(tag) -> bool:
        tag_id = (tag.get("id") or "").lower()
        if any(term in tag_id for term in substrings):
            return True

        classes = [cls.lower() for cls in (tag.get("class") or [])]
        for cls in classes:
            if any(
                cls == term or cls.startswith(f"{term}-") or cls.startswith(f"{term}_")
                for term in substrings
            ):
                return True
        return False

    for el in [tag for tag in body.find_all(True) if has_noise_marker(tag)]:
        el.decompose()

    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", True)).strip()
    return post_process_text(text)


def post_process_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned_lines: list[str] = []
    for line in lines:
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        lowered = line.lower()
        if lowered == "back":
            continue
        if re.fullmatch(r"pdf\s*\[[^\]]+\]", line, flags=re.IGNORECASE):
            continue
        if lowered in {"leaflets & guidelines", "projects", "social media", "discuss the tool", "disqus"}:
            continue
        if lowered in {
            "applicability",
            "theme",
            "languages",
            "keywords",
            "year of release",
            "country of origin",
            "issuing organisation",
            "contact",
            "number of pages",
            "average rating to the tool:",
            "number of ratings to the tool:",
            "give your rating to the tool:",
        }:
            continue
        if lowered.startswith("more about the tool on organic eprints"):
            continue

        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def should_use_browser_fallback(html: str, clean_text: str) -> bool:
    config = get_config()
    if len(clean_text) >= config.min_browser_fallback_text:
        return False
    lowered = html.lower()
    return "<script" in lowered or 'id="app"' in lowered or 'id="root"' in lowered


def extract_text_from_url(raw_url: str) -> ExtractionResult:
    config = get_config()
    normalized_url, domain = validate_url(raw_url)
    parsed = urlparse(normalized_url)
    started_at = time.monotonic()

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"Accept-Encoding": "identity"})

    def attempt_fetch(user_agent: str) -> tuple[bytes, str, str, str, int | None, float | None, int]:
        ensure_within_deadline(started_at)

        if config.polite_mode:
            now = time.monotonic()
            last = LAST_DOMAIN_CALL.get(domain)
            if last is not None:
                required = random.uniform(1.2, 3.5)
                elapsed = now - last
                if elapsed < required:
                    time.sleep(required - elapsed)

        headers = {
            "User-Agent": user_agent,
            **config.browsery_headers,
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        }

        if config.polite_mode:
            try:
                session.get(f"{parsed.scheme}://{parsed.netloc}/", headers=headers, timeout=config.request_timeout, allow_redirects=True)
            except Exception:
                pass
            time.sleep(random.uniform(0.6, 1.8))

        with session.get(normalized_url, headers=headers, timeout=config.request_timeout, stream=True, allow_redirects=True) as resp:
            ensure_within_deadline(started_at)
            LAST_DOMAIN_CALL[domain] = time.monotonic()

            final_url = resp.url
            final_parsed = urlparse(final_url)
            if _is_blocked_media_host_for_config(final_parsed.hostname, config):
                raise ValueError("Video platform URLs are not supported.")
            if _is_private_host_for_config(final_parsed.hostname, config):
                raise ValueError("Redirected to a private/loopback address, which is not allowed.")

            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            content_disposition = resp.headers.get("Content-Disposition")
            final_path = final_parsed.path.lower()
            is_pdf = looks_like_pdf_response(
                content_type=ctype,
                path=final_path,
                content_disposition=content_disposition,
            )

            if is_pdf:
                pdf_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                pdf_text, page_count = extract_pdf_content_from_bytes(pdf_bytes)
                if not pdf_text.strip():
                    raise ValueError("Could not extract text from PDF (possibly scanned/image-only).")
                return pdf_text.encode("utf-8"), "utf-8", final_url, "pdf", page_count, None, len(pdf_bytes)

            if looks_like_docx_response(
                content_type=ctype,
                path=final_path,
                content_disposition=content_disposition,
            ):
                docx_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                docx_text, page_count = extract_docx_content_from_bytes(docx_bytes)
                return docx_text.encode("utf-8"), "utf-8", final_url, "document", page_count, None, len(docx_bytes)

            if looks_like_pptx_response(
                content_type=ctype,
                path=final_path,
                content_disposition=content_disposition,
            ):
                pptx_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                pptx_text, slide_count = extract_pptx_content_from_bytes(pptx_bytes)
                return pptx_text.encode("utf-8"), "utf-8", final_url, "document", slide_count, None, len(pptx_bytes)

            is_htmlish = (
                "text/html" in ctype
                or "application/xhtml+xml" in ctype
                or "application/octet-stream" in ctype
            )
            if looks_like_media_response(
                content_type=ctype,
                path=final_path,
                content_disposition=content_disposition,
            ):
                media_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                duration_seconds = probe_media_duration_from_url(final_url, timeout_seconds=config.request_timeout[1])
                summary = "Media file detected."
                if duration_seconds is not None:
                    summary = f"Media file detected. Duration: {duration_seconds:.2f} seconds."
                return summary.encode("utf-8"), "utf-8", final_url, "media", None, duration_seconds, len(media_bytes)

            if looks_like_data_response(
                content_type=ctype,
                path=final_path,
                content_disposition=content_disposition,
            ):
                data_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                summary = "Data file detected."
                return summary.encode("utf-8"), "utf-8", final_url, "data", None, None, len(data_bytes)

            if not is_htmlish:
                payload_bytes = read_response_bytes(resp, byte_limit=config.max_pdf_bytes, label="File")
                if has_pdf_magic_prefix(payload_bytes):
                    pdf_text, page_count = extract_pdf_content_from_bytes(payload_bytes)
                    if not pdf_text.strip():
                        raise ValueError("Could not extract text from PDF (possibly scanned/image-only).")
                    return pdf_text.encode("utf-8"), "utf-8", final_url, "pdf", page_count, None, len(payload_bytes)
                raise ValueError(f"Unsupported Content-Type: {ctype or 'unknown'}")

            resp.encoding = resp.apparent_encoding or resp.encoding
            html_bytes = read_response_bytes(resp, byte_limit=config.max_html_bytes, label="Page")
            return html_bytes, (resp.encoding or "utf-8"), final_url, "html", None, None, len(html_bytes)

    needs_browser_fetch = False
    browser_fallback_reason: Exception | None = None
    try:
        html_bytes, enc, resolved_url, content_kind, page_count, duration_seconds, downloaded_bytes = attempt_fetch(random.choice(config.ua_pool))
    except requests.exceptions.HTTPError as exc:
        sc = getattr(exc.response, "status_code", None)
        if sc in (401, 403, 451, 429):
            raise ValueError(
                f"Site refused access (HTTP {sc}). They may require a browser, login, or disallow automated fetches."
            ) from exc
        raise
    except (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ContentDecodingError,
        requests.exceptions.ConnectionError,
    ) as exc:
        needs_browser_fetch = True
        browser_fallback_reason = exc
        resolved_url = normalized_url
        content_kind = "html"
        html_bytes = b""
        enc = "utf-8"
        page_count = None
        duration_seconds = None
        downloaded_bytes = 0

    if content_kind == "pdf":
        ensure_within_deadline(started_at)
        text = html_bytes.decode(enc, errors="replace")
        return ExtractionResult(
            resolved_url=resolved_url,
            text=text,
            downloaded_bytes=len(html_bytes),
            extracted_text_bytes=len(text.encode("utf-8")),
            content_kind="pdf",
            page_count=page_count,
            duration_seconds=None,
        )

    if content_kind == "document":
        ensure_within_deadline(started_at)
        text = html_bytes.decode(enc, errors="replace")
        return ExtractionResult(
            resolved_url=resolved_url,
            text=text,
            downloaded_bytes=downloaded_bytes,
            extracted_text_bytes=len(text.encode("utf-8")),
            content_kind="document",
            page_count=page_count,
            duration_seconds=None,
        )

    if content_kind == "media":
        ensure_within_deadline(started_at)
        text = html_bytes.decode(enc, errors="replace")
        return ExtractionResult(
            resolved_url=resolved_url,
            text=text,
            downloaded_bytes=downloaded_bytes,
            extracted_text_bytes=len(text.encode("utf-8")),
            content_kind="media",
            page_count=None,
            duration_seconds=duration_seconds,
        )

    if content_kind == "data":
        ensure_within_deadline(started_at)
        text = html_bytes.decode(enc, errors="replace")
        return ExtractionResult(
            resolved_url=resolved_url,
            text=text,
            downloaded_bytes=downloaded_bytes,
            extracted_text_bytes=len(text.encode("utf-8")),
            content_kind="data",
            page_count=None,
            duration_seconds=None,
        )

    html = html_bytes.decode(enc, errors="replace")
    clean_text = extract_clean_text(html) if html else ""

    if not clean_text:
        try:
            ensure_within_deadline(started_at)
            simple_resolved_url, simple_html = fetch_simple_html(normalized_url, config)
            simple_text = extract_clean_text(simple_html)
            if simple_text:
                resolved_url = simple_resolved_url
                clean_text = simple_text
                html = simple_html
        except Exception as exc:
            LOGGER.warning("simple html fallback failed: %s", exc)

    if needs_browser_fetch or should_use_browser_fallback(html, clean_text):
        try:
            ensure_within_deadline(started_at)
            remaining_ms = max(1_000, int((config.extraction_deadline_seconds - (time.monotonic() - started_at)) * 1000))
            resolved_url, html = fetch_with_browser(
                resolved_url or normalized_url,
                allow_url=lambda url: _is_allowed_url_for_config(url, config),
                timeout_ms=min(config.playwright_timeout_ms, remaining_ms),
            )
            ensure_within_deadline(started_at)
            clean_text = extract_clean_text(html)
        except Exception as exc:
            LOGGER.warning("browser fallback failed: %s", exc)
            if not clean_text:
                if browser_fallback_reason is not None:
                    raise ValueError(f"Failed to fetch page with browser fallback: {exc}") from browser_fallback_reason
                raise

    ensure_within_deadline(started_at)
    return ExtractionResult(
        resolved_url=resolved_url,
        text=clean_text,
        downloaded_bytes=downloaded_bytes,
        extracted_text_bytes=len(clean_text.encode("utf-8")),
        content_kind="html",
        page_count=None,
        duration_seconds=None,
    )
