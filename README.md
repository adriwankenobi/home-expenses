# home-expenses

A single-user CLI that reconciles your bank-statement CSVs against invoice PDFs and produces an interactive HTML report.

## Install

```bash
just install
```

## Configure

```bash
cp config.example.json config.json
# edit config.json with your absolute paths and categories
```

Tell the CLI where to find `config.json` via the `HOME_EXPENSES_CONFIG` env var. The simplest setup is a project-local `.env` file (gitignored, loaded automatically by `just`):

```bash
cp .env.example .env
# edit .env with the absolute path to your config.json
```

Precedence: explicit `--config` flag > `HOME_EXPENSES_CONFIG` env var > `./config.json` in the current directory.

## Per-transaction display overrides via `manual_mappings`

`manual_mappings` is the last-resort routing override: when a transaction's full description doesn't match any pattern, the matcher looks it up here. Each entry is an object with at least a `category` field; optional `recurrence` and `period_contains_payment` fields override the category's display semantics **for just that item**.

```jsonc
"manual_mappings": {
  "TRANSF ABUELA REGALO": {
    "category": "gifts"
  },
  "UNIQUE ANNUAL CHARGE 2024-09-15": {
    "category": "sharedQuarterly1",
    "recurrence": "yearly",
    "period_contains_payment": true
  }
}
```

Use this when a single transaction should be aggregated under an existing category but rendered with different period semantics — e.g., a yearly one-off fee that should display under your quarterly "WaterBill" card with a yearly period bar. The category remains quarterly (its recurrence checks are unaffected); just this item gets the override.

If `period_contains_payment: true` is set, the effective recurrence (mapping's `recurrence` if present, otherwise the referenced category's) must not be `"none"`.

## Run

```bash
just report
# or
uv run home-expenses report --config ./config.json --output ./report.html
```

Open `report.html` in **Firefox**. PDF links use `file://` URLs that Firefox opens inline; other browsers may block these.

## Cache

Parsed invoices and statements are cached in `./cache/` (relative to the working directory — i.e. the repo root when invoked via `just`) keyed by file content hash. Override with `cache_dir` in `config.json`. Wipe with:

```bash
uv run home-expenses cache clear
```

## Adding a new invoice vendor

See `CLAUDE.md` for the workflow. The short version: sanitize a `pdfplumber.extract_text()` dump of one invoice, save at `templates/<vendor>.txt`, add a parser module under `src/home_expenses/parsers/invoices/<vendor>.py`, and register it.

## Develop

```bash
just check   # ruff + mypy + pytest
just test    # pytest only
```
