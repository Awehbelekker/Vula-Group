"""
vula/commerce/xlsx_check.py — read a spreadsheet Vula wrote back from the saved file, before it is sent.

31 Oct 2026: the project bill (project_bill.py) and the sheet builder (sheet_builder.py) write
real formulas so the owner can edit their files. A green build proves nothing about the figures,
so the saved bytes are re-opened and every checked cell is evaluated from the file itself and
compared with the books' figure. Only what these modules write is understood: numbers, cell
references and ranges, + − × ÷, brackets, SUM / AVERAGE / COUNT / MIN / MAX, ROUND and IFERROR.
Anything else is an error — a cell the check can't read is never passed as correct.
"""
from __future__ import annotations

import io
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

_TOKEN = re.compile(r'\s*(?:(\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)|([A-Z]+)\(|(\d+(?:\.\d+)?)|("")|([-+*/(),]))')
_CELL = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
_FUNCS = {"SUM", "AVERAGE", "COUNT", "MIN", "MAX", "ROUND", "IFERROR"}


class _XlError(Exception):
    """An Excel error value (#DIV/0!, #VALUE!) — IFERROR turns it into its fallback."""


def _col_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _col_letters(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _round(x: float, places: int) -> float:
    """Excel's ROUND: half away from zero, on the value as Excel shows it (15 significant digits)."""
    d = Decimal(repr(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if x < 0 and d > 0:
        d = -d
    return float(d)


class _Eval:
    def __init__(self, ws):
        self.ws, self.depth = ws, 0

    def cell(self, ref: str) -> Any:
        """A cell's value: a number, "" (text or an IFERROR fallback), or None (empty)."""
        self.depth += 1
        if self.depth > 200:
            raise ValueError("formula loop")
        try:
            v = self.ws[ref.replace("$", "")].value
            if v is None:
                return None
            if isinstance(v, bool):
                return float(v)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str) and v.startswith("="):
                return self.formula(v[1:])
            return ""                                    # text, a date: not a number
        finally:
            self.depth -= 1

    def cells(self, rng: str) -> List[Any]:
        if ":" not in rng:
            return [self.cell(rng)]
        a, b = rng.replace("$", "").split(":")
        (ca, ra), (cb, rb) = _CELL.fullmatch(a).groups(), _CELL.fullmatch(b).groups()
        out = []
        for row in range(int(ra), int(rb) + 1):
            for col in range(_col_num(ca), _col_num(cb) + 1):
                out.append(self.cell(f"{_col_letters(col)}{row}"))
        return out

    def formula(self, text: str) -> Any:
        toks, pos = [], 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                if text[pos:].strip():
                    raise ValueError(f"can't read {text[pos:]!r}")
                break
            toks.append(m.groups())
            pos = m.end()
        saved = (getattr(self, "toks", None), getattr(self, "i", 0))     # a referenced cell's own
        self.toks, self.i = toks, 0                                       # formula is read inside this one
        try:
            v = self.expr()
            if self.i != len(self.toks):
                raise ValueError(f"can't read {text!r}")
            return v
        finally:
            self.toks, self.i = saved

    # -- recursive descent over the tokens --------------------------------------------------
    def peek(self, op: str) -> bool:
        return self.i < len(self.toks) and self.toks[self.i][4] == op

    def take(self):
        self.i += 1
        return self.toks[self.i - 1]

    # Excel errors (#DIV/0!, #VALUE!) are values that flow through a formula, as in Excel: the whole
    # formula is always read to its end, and only IFERROR (or the top of the cell) acts on them.
    @staticmethod
    def _num(v: Any) -> Any:
        if isinstance(v, _XlError) or v is None:
            return v if v is not None else 0.0
        if v == "":
            return _XlError("#VALUE!")
        return float(v)

    @classmethod
    def _arith(cls, op: str, a: Any, b: Any) -> Any:
        a, b = cls._num(a), cls._num(b)
        if isinstance(a, _XlError):
            return a
        if isinstance(b, _XlError):
            return b
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        return _XlError("#DIV/0!") if b == 0 else a / b

    def expr(self) -> Any:
        v = self.term()
        while self.peek("+") or self.peek("-"):
            op = self.take()[4]
            v = self._arith(op, v, self.term())
        return v

    def term(self) -> Any:
        v = self.factor()
        while self.peek("*") or self.peek("/"):
            op = self.take()[4]
            v = self._arith(op, v, self.factor())
        return v

    def factor(self) -> Any:
        if self.i >= len(self.toks):
            raise ValueError("formula ends early")
        ref, func, num, empty, op = self.take()
        if num:
            return float(num)
        if empty:
            return ""
        if op == "-":
            return self._arith("-", 0.0, self.factor())
        if op == "+":
            return self._num(self.factor())
        if op == "(":
            v = self.expr()
            if not self.peek(")"):
                raise ValueError("bracket not closed")
            self.take()
            return v
        if ref:
            if ":" in ref:
                raise ValueError("a range outside a function")
            return self.cell(ref)
        if func:
            return self.call(func)
        raise ValueError("unexpected token")

    def args(self, ranges: bool) -> List[Any]:
        out: List[Any] = []
        while True:
            if ranges and self.i < len(self.toks) and self.toks[self.i][0]:
                ref = self.take()[0]
                out.append(("range", ref))
            else:
                out.append(("value", self.expr()))
            if self.peek(","):
                self.take()
                continue
            if self.peek(")"):
                self.take()
                return out
            raise ValueError("function arguments not closed")

    def call(self, name: str) -> Any:
        if name not in _FUNCS:
            raise ValueError(f"function {name} isn't checked")
        args = self.args(ranges=name in ("SUM", "AVERAGE", "COUNT", "MIN", "MAX"))
        if name == "IFERROR":
            first = args[0][1]
            return args[1][1] if isinstance(first, _XlError) else first
        for kind, v in args:
            if isinstance(v, _XlError):
                return v
        if name == "ROUND":
            x, n = self._num(args[0][1]), self._num(args[1][1])
            return x if isinstance(x, _XlError) else _round(x, int(n))
        nums: List[float] = []
        for kind, v in args:
            vals = self.cells(v) if kind == "range" else [v]
            for x in vals:
                if isinstance(x, _XlError):
                    return x
                if isinstance(x, float):
                    nums.append(x)
                elif kind == "value" and x is not None and x != "":
                    nums.append(float(x))
        if name == "SUM":
            return float(sum(nums))
        if name == "COUNT":
            return float(len(nums))
        if not nums:
            return _XlError("#DIV/0!") if name == "AVERAGE" else 0.0
        return {"AVERAGE": sum(nums) / len(nums), "MIN": min(nums), "MAX": max(nums)}[name]


def value(ws, ref: str) -> Any:
    """A cell's value evaluated from the file: a number, "" or None. ValueError if it can't be read;
    an Excel error value is reported as ValueError too."""
    v = _Eval(ws).cell(ref)
    if isinstance(v, _XlError):
        raise ValueError(f"{ref} is {v}")
    return v


def evaluate(ws, ref: str, _depth: int = 0) -> float:
    """The number in one cell (0 when empty). ValueError when it isn't a number."""
    v = value(ws, ref)
    if v == "":
        raise ValueError(f"{ref} is not a number")
    return float(v or 0)


def mismatches(data: bytes, want: Dict[str, int], sheet: str = "",
               exact: Optional[Dict[str, Optional[float]]] = None) -> List[str]:
    """Each checked cell whose value, evaluated from the saved file, differs from the books.
    `want`: ref -> cents. `exact`: ref -> a plain number (a count, an average in rands, a ratio),
    or None for a cell that must be blank."""
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
    for ref, expected in (exact or {}).items():
        try:
            got = value(ws, ref)
        except ValueError as exc:
            out.append(f"{ref}: {exc}")
            continue
        if expected is None:
            if got not in ("", None):
                out.append(f"{ref}: file {got} vs blank")
        elif got in ("", None) or abs(float(got) - expected) > 1e-6 * max(1.0, abs(expected)):
            out.append(f"{ref}: file {got} vs books {expected}")
    return out


def safe_text(cell, value) -> None:
    """Write text from the books (a supplier name, a bank description) so it is never read as a
    formula: a value starting with = would otherwise run as one when the file is opened."""
    cell.value = value
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        cell.data_type = "s"
