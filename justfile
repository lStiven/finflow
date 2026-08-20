set shell := ['bash', '-cu']

default:
    just --list

prepare:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright
    uv run pytest

fix:
    uv run ruff format .
    uv run ruff check --fix .

format:
    uv run ruff format .

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

typecheck:
    uv run pyright

test *args:
    uv run pytest {{args}}

sync:
    uv sync

deps-update:
    uv lock --upgrade
    uv sync

deps-update-one package:
    uv lock --upgrade-package {{package}}
    uv sync
