# Claude instructions for home-expenses

This project handles personal financial data. The following rules are not negotiable.

## Hard rule: never read real financial data

Never directly read the user's bank-statement files (Excel/CSV), invoice PDFs, or the stdout/printed output of any tool or script that reads them. Always delegate reading to code; verify behavior via synthetic fixtures or the user opening the HTML report.

Sanitized pastes from the user (real values replaced with fake equivalents preserving format) are allowed — they no longer contain real financial data.

## How to test

All tests use synthetic fixtures generated in-repo or in `tmp_path`. The canonical reference samples live in `tests/templates/` (e.g. `tests/templates/bank_statement.csv`). Real-world files (`bank_statements/`, `invoices/`, `config.json`, `cache/`, `report.html`) are gitignored and must never be read.

## Adding a new invoice vendor

1. Save a sanitized text dump of one invoice page at `tests/templates/<vendor>.txt`.
2. Add `src/home_expenses/parsers/invoices/<vendor>.py` exporting a `PARSER_NAME` constant, a `parse(path) -> Invoice` function, and a `parse_text(text, *, source_path, content_hash)` for testing.
3. Register the parser in `parsers/invoices/__init__.py`.
4. Write tests in `tests/parsers/invoices/test_<vendor>.py` driven by the template.

## Console output policy

`home-expenses` prints only operational metadata (counts, file paths, structural errors) to stdout/stderr. Never amounts, descriptions, vendor strings, or per-row data.
