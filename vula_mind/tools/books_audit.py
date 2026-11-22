"""Run the books audit for one business and print it. Changes nothing, except that reading a PDF
keeps what it contains on the document (fields._file_check) — --no-files skips that.

    python tools/books_audit.py digg-demo            # plain-text report
    python tools/books_audit.py digg-demo --json     # the full report, every finding's rows
    python tools/books_audit.py digg-demo --no-files # don't read the PDFs (writes nothing at all)

See vula/commerce/books_audit.py for what each section checks.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    from vula.commerce import books_audit
    rep = books_audit.run(args[0], check_files="--no-files" not in sys.argv)
    print(json.dumps(rep, indent=1, default=str) if "--json" in sys.argv else books_audit.text_report(rep))


if __name__ == "__main__":
    main()
