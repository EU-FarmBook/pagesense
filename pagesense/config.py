from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(
    name: str,
    default: tuple[str, ...],
    *,
    lowercase: bool = False,
    separator: str = ",",
) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default

    values = []
    for item in raw.split(separator):
        value = item.strip()
        if not value:
            continue
        values.append(value.lower() if lowercase else value)
    return tuple(values)


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    host: str
    port: int
    debug: bool
    auto_reload: bool
    polite_mode: bool
    max_html_bytes: int
    max_pdf_bytes: int
    request_timeout: tuple[int, int]
    playwright_timeout_ms: int
    min_browser_fallback_text: int
    extraction_deadline_seconds: int
    request_logging_enabled: bool
    request_log_db_path: str
    request_log_api_enabled: bool
    request_log_api_token: str
    allowed_schemes: set[str]
    browsery_headers: dict[str, str]
    ua_pool: list[str]
    private_nets: tuple[str, ...]
    blocked_host_patterns: tuple[str, ...]
    public_base_url: str

    def with_overrides(self, **overrides: object) -> "AppConfig":
        data = asdict(self)
        data.update(overrides)
        return AppConfig(**data)


def load_config() -> AppConfig:
    load_env_file(BASE_DIR / ".env")

    return AppConfig(
        base_dir=BASE_DIR,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=env_int("PORT", 8006),
        debug=env_bool("DEBUG", False),
        auto_reload=env_bool("AUTO_RELOAD", False),
        polite_mode=env_bool("POLITE_MODE", False),
        max_html_bytes=env_int("MAX_HTML_BYTES", 5_000_000),
        max_pdf_bytes=env_int("MAX_PDF_BYTES", 50_000_000),
        request_timeout=(
            env_int("HTTP_CONNECT_TIMEOUT_SECONDS", 10),
            env_int("HTTP_READ_TIMEOUT_SECONDS", 30),
        ),
        playwright_timeout_ms=env_int("PLAYWRIGHT_TIMEOUT_MS", 30_000),
        min_browser_fallback_text=env_int("MIN_BROWSER_FALLBACK_TEXT", 120),
        extraction_deadline_seconds=env_int("EXTRACTION_DEADLINE_SECONDS", 30),
        request_logging_enabled=env_bool("REQUEST_LOGGING_ENABLED", True),
        request_log_db_path=os.environ.get("REQUEST_LOG_DB_PATH", str(BASE_DIR / "requests.db")),
        request_log_api_enabled=env_bool("REQUEST_LOG_API_ENABLED", False),
        request_log_api_token=os.environ.get("REQUEST_LOG_API_TOKEN", "").strip(),
        allowed_schemes=set(env_csv("ALLOWED_SCHEMES", ("http", "https"), lowercase=True)),
        browsery_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Accept-Encoding": "identity",
        },
        ua_pool=list(env_csv(
            "UA_POOL",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
            ),
            separator="||",
        )),
        private_nets=(
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "::1/128",
            "fc00::/7",
            "fe80::/10",
        ),
        blocked_host_patterns=env_csv(
            "BLOCKED_HOST_PATTERNS",
            (
                "youtube.com",
                "youtu.be",
                "m.youtube.com",
                "youtube-nocookie.com",
                "vimeo.com",
                "player.vimeo.com",
                "dailymotion.com",
                "www.dailymotion.com",
                "twitch.tv",
                "www.twitch.tv",
                "tiktok.com",
                "www.tiktok.com",
            ),
            lowercase=True,
        ),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "").strip(),
    )
