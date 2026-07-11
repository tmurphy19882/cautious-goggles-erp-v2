# Makefile for ERP v2 — the common local-dev entry points.
# Run from the service root: `cd services/erp && make <target>`.

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip
ALEMBIC ?= $(PYTHON) -m alembic
COVERAGE_THRESHOLD ?= 80

DATABASE_URL ?= postgresql+asyncpg://erp:erp@localhost:5432/erp_db
export ERP_DATABASE_URL ?= $(DATABASE_URL)
export ERP_TEST_DATABASE_URL ?= $(DATABASE_URL)

.PHONY: help install migrate upgrade downgrade run test test-int test-unit seed smoke openapi check-coverage clean lint format

help:
	@echo "Targets:"
	@echo "  install       — pip install the package + dev deps"
	@echo "  migrate       — alembic upgrade head"
	@echo "  downgrade     — alembic downgrade -1"
	@echo "  run           — uvicorn the v2 app on :8001"
	@echo "  test          — run the full pytest suite"
	@echo "  test-int      — run integration tests only"
	@echo "  test-unit     — run unit tests only"
	@echo "  seed          — run scripts/seed_demo.py"
	@echo "  smoke         — run scripts/smoke_test.py (boots app via ASGI)"
	@echo "  openapi       — dump the OpenAPI schema to docs/openapi/erp.json"
	@echo "  check-coverage— fail if coverage is below $(COVERAGE_THRESHOLD)%"

install:
	$(PIP) install -e ".[dev]"

migrate upgrade:
	$(ALEMBIC) upgrade head

downgrade:
	$(ALEMBIC) downgrade -1

run:
	uvicorn api.main:app --reload --port 8001

test:
	pytest

test-int:
	pytest -m integration

test-unit:
	pytest -m "not integration"

seed:
	$(PYTHON) scripts/seed_demo.py

smoke:
	$(PYTHON) scripts/smoke_test.py

openapi:
	$(PYTHON) scripts/dump_openapi.py

check-coverage:
	$(PYTHON) scripts/check_coverage.py --threshold $(COVERAGE_THRESHOLD)

lint:
	ruff check src tests
	mypy src

format:
	ruff format src tests
	ruff check --fix src tests

clean:
	rm -rf .pytest_cache .coverage htmlcov .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
