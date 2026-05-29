# /// script
# requires-python = ">=3.12"
# dependencies = ["pdfplumber"]
# ///
import sys
from pathlib import Path

import pdfplumber


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <invoice.pdf>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if i > 1:
                print(f"\n--- page {i} ---")
            print(page.extract_text() or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
