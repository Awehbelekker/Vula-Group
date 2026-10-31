"""
vula/commerce/xlsx_check.py — read a spreadsheet Vula wrote back from the saved file, before it is sent.

31 Oct 2026: the project bill (project_bill.py) and the sheet builder (sheet_builder.py) write
real formulas so the owner can edit their files. A green build proves nothing about the figures,
so the saved bytes are re-opened and every total cell is evaluated from the file itself and
compared with the books' figure, to the cent. Only the formulas these modules write are
understood: numbers, cell references, SUM(range), + and −.
"""
from __future__ import annotations

import io
import re
from typing import Dict, List

_TOKEN = re.compile(r"\s*(SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)|[A-Z]+\d+|\d+(?:\.\d+)?|[+-])")


def evaluate(ws, ref: str, _depth: int = 0) -> float:
    """The value of one cell: a number, or a formula of references, SUM(range), + and −."""
    if _depth > 50:
        raise ValueError("formula loop")
    v = ws[ref].value
    if v is None:
        return 0.0
    if not isinstance(v, str):
        return float(v)
    if not v.startswith("="):
        raise ValueError(f"{ref} is not a number")
    expr, pos, total, sign = v[1:], 0, 0.0, 1
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"{ref}: can't read {expr[pos:]!r}")
        tok, pos = m.group(1), m.end()
        if tok in "+-":
            sign = 1 if tok == "+" else -1
            continue
        if tok.startswith("SUM("):
            col, a, b = m.group(2), int(m.group(3)), int(m.group(5))
            val = sum(evaluate(ws, f"{col}{i}", _depth + 1) for i in range(a, b + 1))
        elif tok[0].isdigit():
            val = float(tok)
        else:
            val = evaluate(ws, tok, _depth + 1)
        total += sign * val
    return total


def mismatches(data: bytes, want: Dict[str, int], sheet: str = "") -> List[str]:
    """Each cell (ref -> expected cents) whose value, evaluated from the saved file, differs."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    ws = wb[sheet] if sheet else wb.active
    out = []
    for ref, cents in want.items():
        try:
            got = int(round(evaluate(ws, ref) * 100))
        except ValueError as exc:
            out.append(f"{ref}: {exc}")
            continue
        if got != cents:
            out.append(f"{ref}: file {got} vs books {cents}")
    return out


def safe_text(cell, value) -> None:
    """Write text from the books (a supplier name, a bank description) so it is never read as a
    formula: a value starting with = would otherwise run as one when the file is opened."""
    cell.value = value
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        cell.data_type = "s"
