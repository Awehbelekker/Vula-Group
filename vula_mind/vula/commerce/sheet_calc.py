"""
vula/commerce/sheet_calc.py — the owner's own calculated columns in a sheet ("total minus VAT", "margin %").

31 Oct 2026 (sheet builder, item 1). A formula is parsed by this module into a small tree — never
eval'd, and never pasted into the spreadsheet as text: the cell formula is rebuilt from the tree, so
nothing the model writes can reach Excel except column references, numbers, + − × ÷ and brackets.

Each row is worked out here in exact decimals (money in rands, rounded half away from zero like
Excel's ROUND), and the file carries the same formula per row, so the sheet recalculates when edited
and the read-back (xlsx_check) can prove the two agree.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Set, Tuple

MAX_LEN = 120
KINDS = ("money", "number", "percent")
_TOK = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([a-z_][a-z0-9_]*)|(\S))")

Node = Tuple[Any, ...]


class FormulaError(ValueError):
    """The formula can't be used; the message says why, in plain words."""


def _tokens(text: str):
    t = (text or "").lower().replace("×", "*").replace("÷", "/").replace("−", "-").strip()
    if not t:
        raise FormulaError("The calculation is empty.")
    if len(t) > MAX_LEN:
        raise FormulaError("That calculation is too long — keep it to a few columns.")
    out, pos = [], 0
    while pos < len(t):
        m = _TOK.match(t, pos)
        if not m or m.end() == pos:
            break
        num, name, op = m.groups()
        if op and op not in "+-*/()":
            raise FormulaError(f"A calculation can only use + − × ÷ and brackets, not “{op}”.")
        out.append(("num", Decimal(num)) if num else ("name", name) if name else ("op", op))
        pos = m.end()
    return out


def parse(text: str, allowed: Dict[str, str]) -> Node:
    """The tree for `text`. `allowed`: the column ids it may use -> their kind (money / number /
    percent). FormulaError for anything else."""
    toks, i = _tokens(text), 0

    def peek():
        return toks[i] if i < len(toks) else ("end", None)

    def take():
        nonlocal i
        if i >= len(toks):
            return ("end", None)
        i += 1
        return toks[i - 1]

    def expr(depth=0):
        if depth > 12:
            raise FormulaError("That calculation has too many brackets.")
        node = term(depth)
        while peek() in (("op", "+"), ("op", "-")):
            op = take()[1]
            node = (op, node, term(depth))
        return node

    def term(depth):
        node = factor(depth)
        while peek() in (("op", "*"), ("op", "/")):
            op = take()[1]
            node = (op, node, factor(depth))
        return node

    def factor(depth):
        kind, val = take()
        if kind == "num":
            return ("num", val)
        if kind == "name":
            if val not in allowed:
                raise FormulaError(f"“{val}” isn't a number column in this sheet. It can use: "
                                   + ", ".join(sorted(allowed)) + ".")
            return ("ref", val)
        if (kind, val) == ("op", "-"):
            return ("neg", factor(depth))
        if (kind, val) == ("op", "("):
            node = expr(depth + 1)
            if take() != ("op", ")"):
                raise FormulaError("A bracket in the calculation isn't closed.")
            return node
        raise FormulaError("I couldn't read that calculation — e.g. “total - vat” or “(total - vat) / total”.")

    node = expr()
    if i != len(toks):
        raise FormulaError("I couldn't read that calculation — e.g. “total - vat” or “(total - vat) / total”.")
    return node


def refs(node: Node) -> Set[str]:
    if node[0] == "ref":
        return {node[1]}
    if node[0] == "num":
        return set()
    return set().union(*(refs(n) for n in node[1:]))


def unit(node: Node, kinds: Dict[str, str]) -> str:
    """What the result is: money (M), a plain number (N) or a ratio (P, shown as a percentage)."""
    k = node[0]
    if k == "num":
        return "N"
    if k == "ref":
        return "M" if kinds[node[1]] == "money" else "N"
    if k == "neg":
        return unit(node[1], kinds)
    a, b = unit(node[1], kinds), unit(node[2], kinds)
    a, b = ("N" if a == "P" else a), ("N" if b == "P" else b)
    if k in "+-":
        return "M" if "M" in (a, b) else "N"
    if k == "*":
        if a == b == "M":
            raise FormulaError("Money times money doesn't give a meaningful figure — multiply money by a quantity or a number.")
        return "M" if "M" in (a, b) else "N"
    return {"MM": "P", "MN": "M", "NM": "N", "NN": "N"}[a + b]


def kind_of(node: Node, kinds: Dict[str, str]) -> str:
    return {"M": "money", "P": "percent", "N": "number"}[unit(node, kinds)]


def round_half_up(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def evaluate(node: Node, values: Dict[str, Optional[Decimal]]) -> Optional[Decimal]:
    """The value from the row's values (money in rands). None when an input is missing or a division
    is by zero — the cell is left blank, as the file's IFERROR does."""
    k = node[0]
    if k == "num":
        return node[1]
    if k == "ref":
        return values.get(node[1])
    if k == "neg":
        v = evaluate(node[1], values)
        return None if v is None else -v
    a, b = evaluate(node[1], values), evaluate(node[2], values)
    if a is None or b is None:
        return None
    try:
        if k == "+":
            return a + b
        if k == "-":
            return a - b
        if k == "*":
            return a * b
        return None if b == 0 else a / b
    except InvalidOperation:
        return None


def to_excel(node: Node, cell: Callable[[str], str]) -> str:
    """The same calculation as an Excel expression, with each column replaced by its cell."""
    k = node[0]
    if k == "num":
        return format(node[1], "f")
    if k == "ref":
        return cell(node[1])
    if k == "neg":
        return f"(-{to_excel(node[1], cell)})"
    return f"({to_excel(node[1], cell)}{k}{to_excel(node[2], cell)})"


PLACES = {"money": 2, "percent": 4, "number": 4}


def cell_formula(node: Node, kind: str, cell: Callable[[str], str]) -> str:
    return f'=IFERROR(ROUND({to_excel(node, cell)},{PLACES[kind]}),"")'


def stored(value: Optional[Decimal], kind: str) -> Any:
    """A computed value as a row keeps it: money in cents, others as a rounded number."""
    if value is None:
        return None
    r = round_half_up(value, PLACES[kind])
    return int(r * 100) if kind == "money" else float(r)
