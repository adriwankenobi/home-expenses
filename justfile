set dotenv-load := true

default:
    @just --choose

# Install dependencies via uv.
install:
    uv sync

# Run lint + types + tests.
check:
    uv run ruff check src tests
    uv run ruff format --check src tests
    uv run mypy
    uv run pytest

# Run tests only.
test:
    uv run pytest

# Build report.html using config.json.
report:
    uv run home-expenses report

# Wipe the extraction cache (forces every invoice/statement to re-parse).
clear-cache:
    uv run home-expenses cache clear

# Extract all-pages text from an invoice PDF (sanitize before pasting).
extract-invoice path:
    uv run scripts/extract_invoice_text.py {{path}}
