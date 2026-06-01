from __future__ import annotations

import atexit
import re
import threading
import zipfile
from io import BytesIO
from typing import Callable
from xml.etree import ElementTree as ET

from pypdf import PdfReader, filters
from playwright.sync_api import sync_playwright

filters.ZLIB_MAX_OUTPUT_LENGTH = 0


_thread_state = threading.local()
_cleanup_registered = False
_cleanup_lock = threading.Lock()
_playwright_instances: list[tuple[object, object]] = []


def _get_browser():
    global _cleanup_registered

    browser = getattr(_thread_state, "browser", None)
    if browser is None:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        _thread_state.playwright = playwright
        _thread_state.browser = browser
        with _cleanup_lock:
            _playwright_instances.append((playwright, browser))

        with _cleanup_lock:
            if not _cleanup_registered:
                def _close_all():
                    with _cleanup_lock:
                        instances = list(_playwright_instances)
                        _playwright_instances.clear()
                    for playwright_obj, browser_obj in instances:
                        try:
                            browser_obj.close()
                        finally:
                            playwright_obj.stop()

                atexit.register(_close_all)
                _cleanup_registered = True

    return browser


def extract_pdf_content_from_bytes(pdf_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("Encrypted PDF - cannot extract text without password.") from exc

    page_count = len(reader.pages)
    pages_text: list[str] = []
    for page in reader.pages:
        txt = (page.extract_text() or "").strip()
        if txt:
            pages_text.append(txt)

    if not pages_text:
        raise ValueError("No extractable text - likely scanned (image-only) PDF.")

    text = "\n\n".join(pages_text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace("\x00", "").strip(), page_count


def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    text, _ = extract_pdf_content_from_bytes(pdf_bytes)
    return text


def _read_zip_xml(zip_file: zipfile.ZipFile, member: str) -> ET.Element | None:
    try:
        with zip_file.open(member) as handle:
            return ET.fromstring(handle.read())
    except KeyError:
        return None


def extract_docx_content_from_bytes(docx_bytes: bytes) -> tuple[str, int | None]:
    with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
        document_root = _read_zip_xml(archive, "word/document.xml")
        app_root = _read_zip_xml(archive, "docProps/app.xml")

        if document_root is None:
            raise ValueError("Could not read DOCX document.xml.")

        namespaces = {
            "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
        }

        paragraphs: list[str] = []
        for paragraph in document_root.findall(".//w:p", namespaces):
            runs = [node.text or "" for node in paragraph.findall(".//w:t", namespaces)]
            text = "".join(runs).strip()
            if text:
                paragraphs.append(text)

        if not paragraphs:
            raise ValueError("No extractable text found in DOCX file.")

        page_count = None
        if app_root is not None:
            pages_node = app_root.find(".//ep:Pages", namespaces)
            if pages_node is not None and (pages_node.text or "").strip().isdigit():
                page_count = int((pages_node.text or "").strip())

        return re.sub(r"\n{3,}", "\n\n", "\n\n".join(paragraphs)).strip(), page_count


def extract_pptx_content_from_bytes(pptx_bytes: bytes) -> tuple[str, int]:
    with zipfile.ZipFile(BytesIO(pptx_bytes)) as archive:
        slide_names = sorted(
            name for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
        if not slide_names:
            raise ValueError("No slides found in PPTX file.")

        namespaces = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        slide_texts: list[str] = []
        for slide_name in slide_names:
            slide_root = _read_zip_xml(archive, slide_name)
            if slide_root is None:
                continue
            texts = [(node.text or "").strip() for node in slide_root.findall(".//a:t", namespaces)]
            slide_text = "\n".join(text for text in texts if text)
            if slide_text:
                slide_texts.append(slide_text)

        if not slide_texts:
            raise ValueError("No extractable text found in PPTX file.")

        return re.sub(r"\n{3,}", "\n\n", "\n\n".join(slide_texts)).strip(), len(slide_names)


def fetch_with_browser(
    url: str,
    allow_url: Callable[[str], bool] | None = None,
    timeout_ms: int = 45_000,
) -> tuple[str, str]:
    browser = _get_browser()
    context = browser.new_context()
    page = context.new_page()
    try:
        if allow_url is not None:
            def _route_handler(route):
                if allow_url(route.request.url):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            page.route("**/*", _route_handler)

        page.goto(url, timeout=timeout_ms, wait_until="networkidle")
        resolved = page.url
        if allow_url is not None and not allow_url(resolved):
            raise ValueError("Browser navigation resolved to a blocked address.")
        return resolved, page.content()
    finally:
        page.close()
        context.close()
