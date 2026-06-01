# Python runtime
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Minimal OS deps:
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini curl ffmpeg libxml2 libxslt1.1 \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# App code
COPY . .

# Runtime config
ENV FLASK_ENV=production
EXPOSE 10000

# Basic HTTP healthcheck (fast, simple)
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -fsS http://localhost:10000/ || exit 1

# Entrypoint + server
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "-w", "2", \
     "--bind", "0.0.0.0:10000", "app:app", "--timeout", "45", "--log-level", "info"]
