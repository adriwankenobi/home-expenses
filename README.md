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

## Charges that land just before a period boundary

A recurring charge sometimes arrives a day or two early — the fee *for May* hits your account on 30 April. By date it belongs to April, which leaves April with two charges and May with none, so you get a `recurring_missed` alert for May and a period bar drawn on the wrong month.

`period_edge_days` fixes both. A payment landing in the last N days of the period containing it is attributed to the **following** period:

```jsonc
"Comunidad": {
  "recurrence": "monthly",
  "patterns": ["COMUNIDAD"],
  "period_contains_payment": true,
  "period_edge_days": 2          // 29 or 30 April → the May period
}
```

The shift is **unconditional** — it doesn't look at what other charges exist. A payment that genuinely belongs to its own period but is dated inside the edge window gets pushed forward too, leaving its own period empty and raising `recurring_missed` there. That's deliberate: you see the anomaly rather than having it silently absorbed. Keep the window as narrow as the real billing behavior allows.

The window is **trailing only**. It never reaches backwards, so a charge on 1 May stays in May. For a category billed in arrears (`period_contains_payment` unset or `false`) the trailing reach is already 1 day — a payment on the last day of a period settles that period — and `period_edge_days` widens it.

`period_edge_days` must be a non-negative integer, requires a `recurrence` other than `"none"`, and must be shorter than the shortest period of that recurrence (28 days for `monthly`, 59 for `bimonthly`, 90 for `quarterly`, 365 for `yearly`). It is a category-level field; `manual_mappings` entries cannot override it.

Note this is unrelated to `match_window_days`, which is the tolerance for pairing an *invoice* with the bank payment that settles it.

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
