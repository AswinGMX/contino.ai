.PHONY: setup api web worker test build

setup:            ## install backend + frontend deps
	python3 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements-dev.txt
	cd web && npm ci

api:              ## run the API (with embedded worker) on :8000
	cd api && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:           ## run an extra worker process
	cd api && .venv/bin/python -m app.worker

web:              ## run the web app on :5173 (proxies /api to :8000)
	cd web && npm run dev

test:
	cd api && .venv/bin/python -m pytest

build:
	cd web && npm run build
