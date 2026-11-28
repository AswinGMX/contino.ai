# Build context: repository root (docker-compose). api/ itself holds prompts/ and config.yaml.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY api/requirements.txt api/requirements.txt
RUN pip install -r api/requirements.txt
COPY api/app api/app
COPY api/prompts api/prompts
COPY api/config.yaml api/config.yaml
RUN useradd -r -u 10001 app && mkdir -p data output && chown -R app data output
USER app
WORKDIR /srv/api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
