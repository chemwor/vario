# Vario: developing from a checkout. (To just use Vario, see the README's install line.)
#   make setup     backend venv (uv) + web dependencies (npm)
#   make dev       API on :8787 (auto-reload) + the UI dev server on :3000
#   make build     build the UI into web/out (the backend serves it on :8787)
#   make check     lint + type-check everything
#   make test      backend tests
#   make dist      release archive in dist/ (UI prebuilt)
#   make publish   assemble the public repo in dist/public after a privacy check (never pushes)
#   make screenshots / make demo-gif   README images from a fresh `vario demo` (made-up data only)
SHELL := /bin/bash
API_PORT ?= 8787
WEB_PORT ?= 3000
export UV_CACHE_DIR ?= $(HOME)/.cache/uv
export NEXT_TELEMETRY_DISABLED := 1
PY := backend/.venv/bin/python

.PHONY: setup dev api web build check lint test dist publish clean demo-reset screenshots demo-gif

setup:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pyproject.toml --extra dev
	cd web && if [ -f package-lock.json ]; then npm ci; else npm install; fi

dev:
	@[ -x backend/.venv/bin/uvicorn ] && [ -d web/node_modules ] || $(MAKE) setup
	@trap 'kill 0' INT TERM EXIT; \
	  $(MAKE) --no-print-directory api & \
	  $(MAKE) --no-print-directory web & \
	  wait

# WATCHFILES_FORCE_POLLING: file-system events don't always reach uvicorn's watcher; polling always works
api:
	cd backend && WATCHFILES_FORCE_POLLING=true .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $(API_PORT) --reload --reload-dir app

web:
	cd web && WATCHPACK_POLLING=true npx next dev --hostname 127.0.0.1 --port $(WEB_PORT)

build:
	cd web && npx next build

lint:
	cd backend && .venv/bin/ruff check app tests
	cd web && npx eslint app components lib

check: lint
	cd backend && .venv/bin/python -m compileall -q app
	cd web && npx next typegen >/dev/null && npx tsc --noEmit
	bash -n install.sh bin/vario scripts/*.sh voice/hooks/*.sh

test:
	cd backend && .venv/bin/python -m pytest -q

dist: build
	scripts/package.sh

publish:
	scripts/publish.sh

# A fresh demo: its own data folder is reset (never your real data), then started without opening a browser.
DEMO_HOME := $(or $(VARIO_HOME),$(HOME)/.local/share/vario)/demo
PW := uv run --no-project --with playwright python
demo-reset: build
	bin/vario demo --stop >/dev/null 2>&1 || true
	rm -rf "$(DEMO_HOME)"
	bin/vario demo --no-open

screenshots: demo-reset
	$(PW) scripts/screenshots.py
	@echo "Check them: swift scripts/ocr.swift docs/inbox.png docs/briefing.png | grep -iFf .private-terms && echo LEAK || echo clean"

demo-gif: demo-reset
	$(PW) scripts/demo_gif.py

clean:
	rm -rf backend/.venv web/node_modules web/.next web/out dist
