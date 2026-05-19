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

`config.json` is gitignored.

## Run

```bash
just report
# or
uv run home-expenses report --config ./config.json --output ./report.html
```

Open `report.html` in **Firefox**. PDF links use `file://` URLs that Firefox opens inline; other browsers may block these.

## Cache

Parsed invoices and statements are cached in `<config-dir>/cache/` keyed by file content hash. Wipe with:

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
