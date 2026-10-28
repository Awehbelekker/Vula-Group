"""PDF tables read as tables (2026-10-28).

Ian: "Why is Vula struggling to read PDFs? We should be able to read any document and extract all
information accurately." Two causes, both fixed here and in vula/api/whatsapp.py:

1. A PDF's text came from PyMuPDF's plain text join, which runs a table's columns together —
   Judy's Belladonna project bill (PDF) read P&G as R4,800 for R48,000, while the same bill as a
   spreadsheet was read exactly. Docling (vula/ingestion/docling_extract.py, installed in the
   image) keeps rows and columns, but was only tried as a last retry for invoices. Now a PDF whose
   text looks like a table is read through Docling FIRST (looks_tabular / structured_markdown).
2. A BOQ or project bill in a PDF went to the model, while the same bill as a spreadsheet went
   through vula/ingestion/boq_sheet.py cell by cell. The tables Docling finds now go through that
   same reader (boq_lines), so a PDF bill gets the spreadsheet's accuracy: budget, actual, paid.

Bank statements and payment notices never come through here: their own parsers read PyMuPDF's
exact layout. Everything is best-effort: no Docling, a scanned PDF (no text layer) or no table
found → the caller carries on exactly as before.
"""
from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)

# A table-ish line: two or more amounts/quantities on it ("Tiles  m2  45  R 320.00  R 14 400.00").
_NUMBER = re.compile(r"(?<![\w.])\d[\d  ,]*(?:[.,]\d{1,2})?(?![\w])")
MIN_TABLE_LINES = 6
# Docling is CPU work (~1 s a page warm): beyond this the document isn't converted again.
MAX_PDF_BYTES = 25_000_000
MAX_TEXT_CHARS = 150_000          # ~50 pages of text

_cache: "OrderedDict[tuple, Optional[str]]" = OrderedDict()
_CACHE_SIZE = 8


def looks_tabular(text: str) -> bool:
    """True when the plain text has enough rows of figures that its columns matter."""
    if not text or len(text) > MAX_TEXT_CHARS:
        return False
    rows = 0
    for line in text.splitlines():
        nums = [n for n in _NUMBER.findall(line) if len(re.sub(r"\D", "", n)) >= 2]
        if len(nums) >= 2:
            rows += 1
            if rows >= MIN_TABLE_LINES:
                return True
    return False


def has_tables(text: str) -> bool:
    """Markdown already carrying tables (Docling's output)."""
    return bool(text) and bool(re.search(r"^\s*\|.*\|\s*$\n^\s*\|\s*:?-{2,}", text, re.M))


async def structured_markdown(path: Union[str, Path]) -> Optional[str]:
    """Docling's Markdown for this PDF (tables as tables), or None. Cached per file, because the
    analysis and the BOQ reader both want it for the same upload."""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return None
    try:
        st = os.stat(p)
    except OSError:
        return None
    if st.st_size > MAX_PDF_BYTES:
        log.info("PDF %s is %d MB — not converted with Docling", p.name, st.st_size // 1_000_000)
        return None
    key = (str(p), st.st_size, int(st.st_mtime))
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    from vula.ingestion.docling_extract import extract_markdown
    md = await extract_markdown(p)
    _cache[key] = md
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return md


def markdown_tables(md: str) -> List[List[List[str]]]:
    """Each Markdown table in `md` as rows of cell strings (the |---| separator dropped)."""
    tables: List[List[List[str]]] = []
    cur: List[List[str]] = []
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            cells = [c.strip() for c in s[1:-1].split("|")]
            if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    return tables


def boq_lines(md: str) -> List[Dict[str, Any]]:
    """Every priced line of the bill tables in `md`, read cell by cell (vula/ingestion/boq_sheet.py).

    A table that runs over a page break comes back from Docling as two tables, the second without
    its header: one with the same number of columns as the last table read is read under that
    table's header."""
    from vula.ingestion import boq_sheet
    lines: List[Dict[str, Any]] = []
    already: set = set()
    header: Optional[List[str]] = None
    for rows in markdown_tables(md):
        got = boq_sheet.parse_rows(rows, "", infer=not lines)
        if not got and header and len(rows[0]) == len(header):
            got = boq_sheet.parse_rows([header] + rows, "", infer=False)
            # still the section the page break interrupted, until the table names a new one
            last = lines[-1].get("section") if lines else None
            for li in got:
                if li.get("section") in (None, "", "General") and last:
                    li["section"] = last
                else:
                    last = li.get("section") or last
        elif got:
            header = rows[0]
        if not got or boq_sheet._seen(got, already):
            continue
        already |= {(li.get("description") or "").lower() for li in got}
        lines.extend(got)
    return lines
