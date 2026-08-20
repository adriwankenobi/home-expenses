# Claude instructions for home-expenses

This project handles personal financial data. The following rules are not negotiable.

## Hard rule: never read real financial data

Never directly read the user's bank-statement files (Excel/CSV), invoice PDFs, or the stdout/printed output of any tool or script that reads them. Always delegate reading to code; verify behavior via synthetic fixtures or the user opening the HTML report.

Sanitized pastes from the user (real values replaced with fake equivalents preserving format) are allowed — they no longer contain real financial data.

## How to test

All tests use synthetic fixtures generated in-repo or in `tmp_path`. The canonical reference samples live in `tests/templates/` (e.g. `tests/templates/bank_statement.csv`). Real-world files (`bank_statements/`, `invoices/`, `config.json`, `cache/`, `report.html`) are gitignored and must never be read. The user keeps `config.json` *outside* the repo and points the CLI at the config via the `HOME_EXPENSES_CONFIG` env var (precedence: `--config` flag > env var > `./config.json`).

## Adding a new invoice vendor

1. Save a sanitized text dump of one invoice page at `tests/templates/<vendor>.txt`.
2. Add `src/home_expenses/parsers/invoices/<vendor>.py` exporting a `PARSER_NAME` constant, a `parse(path) -> Invoice` function, and a `parse_text(text, *, source_path, content_hash)` for testing.
3. Register the parser in `parsers/invoices/__init__.py`.
4. Write tests in `tests/parsers/invoices/test_<vendor>.py` driven by the template.

## Console output policy

`home-expenses` prints only operational metadata (counts, file paths, structural errors) to stdout/stderr. Never amounts, descriptions, vendor strings, or per-row data.

## Key types and invariants

### `Config.manual_mappings: dict[str, tuple[ManualMapping, ...]]`

Per description, a tuple of one or more `ManualMapping` entries. Each entry has an optional `amount: Decimal | None`. The JSON config accepts either a single object (legacy, normalized to a 1-tuple) or a list of objects (new). Routing walks the tuple in declaration order: first amount-match wins; an entry without `amount` is the fallback and must be last.

Always use the helper `resolve_manual_mapping(description, amount, config) -> ManualMapping | None` to look up the applicable entry — never call `config.manual_mappings.get(desc)` directly. The matcher's seven call sites all go through the helper.

### `ManualMapping`

```python
@dataclass(frozen=True)
class ManualMapping:
    category: str
    amount: Decimal | None = None
    recurrence: Recurrence | None = None
    period_contains_payment: bool | None = None
```

### `SplitGroup.kind: Literal["all_invoiced", "none_invoiced", "mixed"]`

A split group's kind is derived from whether its members have `invoice_folder` set. Routing rules differ:

- **`all_invoiced`**: per-transaction by amount; chronological-zip / min-lag pairing for same-amount disambiguation.
- **`none_invoiced`**: per-period rank by amount, members in config order. Requires uniform recurrence across members.
- **`mixed`**: per-transaction routing (always when `recurrence == "none"` or there are 2+ non-invoiced members), or per-period bucket sanity check (when exactly one non-invoiced member with a real recurrence). 2+ non-invoiced groups use an **implicit catch-all**: the unique non-invoiced member NOT referenced by any manual_mapping entry whose key contains a group pattern.

`all_invoiced` and `mixed` groups accept members with different recurrences (group recurrence falls back to `"none"`, matcher uses the per-transaction path). `none_invoiced` still requires uniform recurrence.

### `Item.from_manual_mapping: bool`

True when a `manual_mapping` was the *routing decision* for this item (not merely consulted for display overrides). Set in two places:

- `_route_mixed_leftover` when a mapping points to any group member and routes the leftover.
- Non-split-group manual_mapping fallthrough in `match()` (the final routing fallback).

The runner's `_has_expected_invoice` filter exempts items with `from_manual_mapping=True`, so they appear in the report even when in invoiced categories without an attached invoice. The matcher's `UNUSED_MANUAL_MAPPING` post-pass relies on this flag to decide whether each amount-specific entry actually routed something.

### `mapped_here` bypasses the window in `_try_invoice_match`

When a transaction's manual_mapping resolves to the current category (`mapped_here == True`), the date-window check on each candidate invoice is skipped. Amount equality and the not-already-consumed check still apply. The user has explicitly authored the routing; bypass the window guard.

### `Category.period_edge_days` is mirrored in Python and JS, with one asymmetry

A payment in the last `period_edge_days` days of its containing period is attributed to the *following* period. Two implementations must stay in sync:

- `assigned_period()` in `recurrence.py` — the **payment** period, used by `check_recurrence` to decide whether a period is covered.
- `payPeriodFor()` / `syntheticPeriodFor()` in `report.html.j2` — the **consumption** period drawn as the period bar for items with no invoice.

The asymmetry: the display path clamps the window to `max(1, period_edge_days)` when `period_contains_payment` is false. In-arrears display has always treated a payment on the last day of a period as settling that period (the old `+1 day` trick), so 1 is its floor. `assigned_period` has no such floor — it never had the `+1` behavior — so with `period_edge_days: 0` the two legitimately disagree for a last-day payment. Don't "fix" that by adding the floor to `assigned_period`: `check_recurrence` counts payments per calendar period, and a payment on 30 April did happen in April.

The shift is unconditional by design (no "only when it disambiguates" rule): a payment inside the edge window moves even if that empties its own period, which then raises `RECURRING_MISSED`. Surfacing the anomaly beats absorbing it.

There is no JS test harness in this repo; the template logic is verified by hand against the table of cases in the README section and the docstring examples.

## Alert kinds

`AlertKind` (in `models.py`) enumerates every alert. New kinds added during the mixed-invoice work:

- `AMBIGUOUS_SPLIT_BUCKET` — split group's per-period bucket size mismatch.
- `SPLIT_AMOUNT_TIE` — rank-based split group has multiple transactions with the same amount in one period.
- `UNUSED_MANUAL_MAPPING` — an amount-specific manual_mapping entry that didn't route any transaction (routed = produced an Item with `from_manual_mapping=True`). Entries whose `(description, amount)` coincides with an invoice-matched transaction are still flagged because the invoice match would have routed it without the mapping.

To add a new kind: extend the `AlertKind` enum, emit `Alert(kind=..., message=..., payload=...)` from the matcher, and ensure the report template's alerts panel handles it (today it shows them all uniformly).

