"""Compare one official bank statement PDF with what Vula holds for a business. Writes nothing.

    python tools/statement_compare.py digg-demo "FNB Sep 2026.pdf"
    python tools/statement_compare.py digg-demo "FNB Sep 2026.pdf" --json    # every proposed action

See vula/commerce/statement_compare.py. In the dashboard, the same comparison is kept for an owner or
manager to confirm (POST /v1/commerce/{tenant}/admin/books-audit/statements).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit(__doc__)
    from vula.commerce import statement_compare
    out = statement_compare.compare(args[0], filename=Path(args[1]).name, pdf_path=Path(args[1]), save=False)
    print(json.dumps(out, indent=1, default=str) if "--json" in sys.argv else statement_compare.text_summary(out))


if __name__ == "__main__":
    main()
