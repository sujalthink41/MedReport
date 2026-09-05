.DEFAULT_GOAL := help
.PHONY: help install up down logs dev lint fmt types arch test test-unit cov check clean

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## sync dependencies and install git hooks
	uv sync
	uv run pre-commit install

up:  ## start postgres + redis
	docker compose up -d

down:  ## stop infrastructure
	docker compose down

logs:  ## tail infrastructure logs
	docker compose logs -f

dev:  ## run the API with autoreload
	uv run uvicorn app.main:app --reload --port 8000

lint:  ## ruff lint
	uv run ruff check app tests

fmt:  ## ruff format + autofix
	uv run ruff format app tests
	uv run ruff check --fix app tests

types:  ## mypy
	uv run mypy app

arch:  ## enforce the dependency rule
	uv run lint-imports

test:  ## full test suite
	uv run pytest

test-unit:  ## domain + application only — no docker required
	uv run pytest tests/domain tests/application

cov:  ## tests with coverage
	uv run pytest --cov=app --cov-report=term-missing

check: lint types arch test  ## everything CI runs

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
