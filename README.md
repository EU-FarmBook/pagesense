# PageSense

PageSense is a small Flask app that fetches a public URL and returns readable text or a short file summary. It supports regular HTML pages, text-based PDFs, DOCX files, and PPTX files, with a browser fallback for pages that render most of their content client-side. Media and data-file URLs are detected and reported instead of being treated as HTML.

## What it does

- Accepts a single `http` or `https` URL.
- Fetches HTML with streaming reads, timeouts, and size caps.
- Extracts text from text-based PDFs, DOCX files, and PPTX files.
- Detects media files and common data files, returning file metadata instead of noisy raw content.
- Removes scripts, forms, media, navigation, overlays, paywall markers, cookie banners, and other common non-content elements.
- Falls back to Playwright/Chromium only when the plain HTTP result looks script-driven or the upstream response cannot be decoded reliably.
- Exposes both a browser UI and a JSON API at `/api/extract`.

## Safety and operational limits

- Only `http` and `https` are allowed.
- Literal private IPs and hostnames that resolve to private or loopback ranges are blocked.
- Redirects to private or loopback targets are blocked.
- Common video-platform hosts such as YouTube, Vimeo, Dailymotion, Twitch, and TikTok are blocked.
- Default limits:
  - HTML: 5 MB
  - Files: 50 MB
  - HTTP timeout: connect 10s, read 30s
  - Browser timeout: 30s
  - Total extraction budget: 30s
- No arbitrary JavaScript is executed unless the browser fallback is required.

## Tech stack

- Python 3.10+
- Flask
- Flask-CORS
- Requests
- BeautifulSoup4 + lxml
- pypdf
- Playwright + Chromium
- Gunicorn

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

Open `http://127.0.0.1:8006`.

Runtime settings are loaded from [`.env`](.env). Use [`.env.sample`](.env.sample) as the template. `UA_POOL` uses `||` as the separator between user-agent strings.

Requests are logged to SQLite when `REQUEST_LOGGING_ENABLED=true`. By default the file is [requests.db](./requests.db) and each row stores timestamp, source (`ui` or `api`), client IP, forwarded IP chain, method, path, target URL, payload details, status, duration, and any error message.

For local auto-reload with `python app.py`, set `DEBUG=true` and `AUTO_RELOAD=true` in [`.env`](.env). Keep both `false` in production.

For local inspection, use [view_logs.py](view_logs.py):

```bash
.venv/bin/python view_logs.py --limit 20
```

You can also enable a token-guarded log API by setting `REQUEST_LOG_API_ENABLED=true` and `REQUEST_LOG_API_TOKEN` in [`.env`](.env), then calling:

```bash
curl -sS http://127.0.0.1:8006/api/logs \
  -H 'Authorization: Bearer change-me'
```

Interactive API docs are available at `/docs`, with the OpenAPI schema at `/openapi.json`. API routes allow CORS from any origin.

For server deployment behind Traefik, use [docker-compose-online.yml](docker-compose-online.yml) and [`.env.online.sample`](.env.online.sample) as the starting point. It mounts `./data` into the container so `requests.db` survives container recreation.

The Docker image installs Chromium dependencies and `ffmpeg`; `ffprobe` is used when possible to report media duration.

To build and push the container image to GHCR from the project root, use [build_and_push.sh](build_and_push.sh):

```bash
sh build_and_push.sh
sh build_and_push.sh v1
```

## API examples

```bash
curl -sS --get http://127.0.0.1:8006/api/extract \
  --data-urlencode "url=https://example.com/article"
```

```bash
curl -sS -X POST http://127.0.0.1:8006/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/article"}'
```

Form-encoded POSTs are also accepted:

```bash
curl -sS -X POST http://127.0.0.1:8006/api/extract \
  -d 'url=https://example.com/article'
```

## Production notes

- Use Gunicorn for production.
- If you keep the Playwright fallback enabled, prefer a process-based worker model over threaded workers unless you have explicitly validated your browser lifecycle design.

## Project structure

- [app.py](app.py): thin entrypoint that creates the Flask app.
- [pagesense/config.py](pagesense/config.py): `.env` loading and runtime config.
- [pagesense/routes/web.py](pagesense/routes/web.py): UI routes.
- [pagesense/routes/api.py](pagesense/routes/api.py): API, logs API, and docs routes.
- [pagesense/services/extractor.py](pagesense/services/extractor.py): URL validation, fetch flow, HTML cleanup, and browser fallback.
- [pagesense/services/request_logs.py](pagesense/services/request_logs.py): SQLite request logging.
- [pagesense/services/openapi.py](pagesense/services/openapi.py): OpenAPI schema generation.
- [pagesense/browser.py](pagesense/browser.py): Playwright, PDF, DOCX, and PPTX helpers.
- [templates/index.html](templates/index.html): browser UI template.
- [view_logs.py](view_logs.py): local SQLite log viewer.
- [utils.py](utils.py): compatibility exports for browser and PDF helpers.
- [tests/test_app.py](tests/test_app.py): lightweight unittest coverage.
- [Dockerfile](Dockerfile) and [docker-compose.yml](docker-compose.yml): container build and local compose config.

## Tests

Run the lightweight test suite with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
