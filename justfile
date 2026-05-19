default:
    @just --choose

# Extract page-1 text from the invoice PDF hardcoded in the script.
extract-invoice:
    uv run scripts/extract_invoice_text.py
