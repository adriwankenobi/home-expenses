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

# Extract page-1 text from an invoice PDF (sanitize before pasting).
extract-invoice:
    uv run scripts/extract_invoice_text.py
