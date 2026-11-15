"""An answer's own tables as an Excel file ("Download as spreadsheet", 13 Nov 2026).

Ian asked for spreadsheets out of the chat. A sheet from the books is custom_sheet's job
(vula/commerce/sheet_builder.py). This is for what Vula wrote: a research comparison, a plan,
a table in an Ask anything answer. The file holds exactly what the answer showed:

  * Every Markdown table becomes a sheet, with a header row in the business's colour.
  * A cell that is plainly an amount ("R 1 450", "1 450.00") or a number becomes a number, so it
    can be summed. Anything else stays text, exactly as written. A citation on an amount ("R 1 450
    [2]") goes into a comment on the cell.
  * A research answer's sources ("[1] Title — https://…") get their own sheet, with links.
  * No model is asked for anything. Before the file is returned, every number cell is read back
    and compared with the text it came from (read_back), so a figure is never changed.
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

MAX_ROWS = 2000
MAX_TABLES = 10

_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CITE = re.compile(r"\s*\[(?:\d{1,2}|D\d{1,2})\]")
_SOURCE = re.compile(r"^\s*\[(\d{1,2}|D\d{1,2})\]\s+(.+?)(?:\s+[—-]\s+(https?://\S+))?\s*$")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\*\*)(.+?)(?:\*\*)?\s*:?\s*$")
_AMOUNT = re.compile(r"^(-)?\s*R\s?(\d{1,3}(?:[ , ]\d{3})*|\d+)(?:\.(\d{1,2}))?$")
_NUMBER = re.compile(r"^(-)?(?:([1-9]\d{0,2}(?:[ , ]\d{3})+)|(0|[1-9]\d*))(?:\.(\d+))?$")


class NoTable(ValueError):
    """Nothing in the answer to put in a sheet; the message says so, for the person."""


def _clean(cell: str) -> str:
    c = cell.strip()
    c = re.sub(r"\*\*(.+?)\*\*", r"\1", c)
    c = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", c)
    c = re.sub(r"`(.+?)`", r"\1", c)
    return c.replace("<br>", "\n").strip()


def _cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [_clean(c) for c in s.split("|")]


def tables(text: str) -> List[Dict[str, Any]]:
    """Every Markdown table in `text`: [{title, header, rows}], at most MAX_TABLES."""
    lines = (text or "").splitlines()
    out: List[Dict[str, Any]] = []
    i, last_heading = 0, ""
    while i < len(lines) and len(out) < MAX_TABLES:
        line = lines[i]
        h = _HEADING.match(line)
        if h and "|" not in line:
            last_heading = _clean(h.group(1))
        if "|" in line and i + 1 < len(lines) and _SEP.match(lines[i + 1]):
            header = _cells(line)
            rows: List[List[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                cells = _cells(lines[j])
                rows.append((cells + [""] * len(header))[:len(header)])
                j += 1
            if rows:
                out.append({"title": last_heading, "header": header, "rows": rows[:MAX_ROWS]})
            i = j
            continue
        i += 1
    return out


def sources(text: str) -> List[Dict[str, str]]:
    """A research answer's sources: the lines after "Sources:" like "[1] Title — https://…"."""
    out: List[Dict[str, str]] = []
    lines = (text or "").splitlines()
    try:
        start = next(k for k, ln in enumerate(lines) if ln.strip().lower().rstrip(":") == "sources")
    except StopIteration:
        return out
    for ln in lines[start + 1:]:
        m = _SOURCE.match(ln)
        if not m:
            if ln.strip():
                break
            continue
        out.append({"n": m.group(1), "title": m.group(2).strip(), "url": m.group(3) or ""})
    return out


def _body_without_sources(text: str) -> str:
    lines = (text or "").splitlines()
    for k, ln in enumerate(lines):
        if ln.strip().lower().rstrip(":") == "sources":
            return "\n".join(lines[:k]).strip()
    return (text or "").strip()


def value(cell: str) -> Tuple[Any, Optional[str], Optional[str]]:
    """(what goes in the cell, its number format or None, a citation note or None)."""
    note = None
    cites = _CITE.findall(cell)
    bare = _CITE.sub("", cell).strip()
    m = _AMOUNT.match(bare)
    if m:
        whole = int(re.sub(r"\D", "", m.group(2)))
        cents = whole * 100 + int((m.group(3) or "0").ljust(2, "0"))
        if m.group(1):
            cents = -cents
        if cites:
            note = "Source " + ", ".join(c.strip() for c in cites)
        return cents / 100, "R #,##0.00;[Red]-R #,##0.00", note
    m = _NUMBER.match(bare)
    if m and not cites:
        digits = re.sub(r"\D", "", m.group(2) or m.group(3) or "")
        frac = m.group(4)
        num: Any = float(f"{digits}.{frac}") if frac else int(digits)
        if m.group(1):
            num = -num
        return num, ("#,##0.00" if frac else "#,##0"), None
    return cell, None, None


def workbook(business: str, title: str, text: str, accent: Optional[str] = None,
             on: Optional[date] = None) -> Tuple[bytes, Dict[str, Any]]:
    """The file, and what each number cell must read back as {sheet!ref: value}."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Font
    from vula.commerce import xlsx as X
    found = tables(text)
    srcs = sources(text)
    if not found and not srcs:
        raise NoTable("There's no table in that answer to put in a spreadsheet. Ask Vula for it as a table "
                      "(e.g. \"as a table with supplier, product and price\") and download that.")
    on = on or date.today()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    want: Dict[str, Any] = {}
    token = X._fill_var.set(X._fill_for(accent))
    try:
        used: set = set()

        def sheet(name: str):
            base = re.sub(r"[\[\]\*\?/\\:]", " ", name or "Sheet").strip()[:28] or "Sheet"
            n, k = base, 2
            while n.lower() in used:
                n, k = f"{base[:25]} {k}", k + 1
            used.add(n.lower())
            ws = wb.create_sheet(n)
            ws.cell(row=1, column=1, value=f"{business} — {title}").font = Font(bold=True, size=13)
            ws.cell(row=2, column=1, value=f"From Vula's answer on {on.day} {on:%b %Y}").font = X._MUTED
            return ws

        if found:
            for t_i, t in enumerate(found, 1):
                ws = sheet(t["title"] or (title if len(found) == 1 else f"Table {t_i}"))
                widths = [max(10, min(48, max(len(str(h)), *(len(str(r[c])) for r in t["rows"])) + 2))
                          for c, h in enumerate(t["header"])]
                X._table(ws, 4, t["header"], widths)
                for r_i, row in enumerate(t["rows"], start=5):
                    for c_i, cell in enumerate(row, start=1):
                        v, fmt, note = value(cell)
                        c = ws.cell(row=r_i, column=c_i, value=v)
                        if fmt:
                            c.number_format = fmt
                            want[f"{ws.title}!{c.coordinate}"] = v
                        if note:
                            c.comment = Comment(note, "Vula")
                X._finish_table(ws, 4, 4 + len(t["rows"]), len(t["header"]))
        else:
            ws = sheet("Answer")
            X._table(ws, 4, ["Answer"], [100])
            for r_i, ln in enumerate([ln for ln in _body_without_sources(text).splitlines() if ln.strip()], start=5):
                ws.cell(row=r_i, column=1, value=_clean(ln))
        if srcs:
            ws = sheet("Sources")
            X._table(ws, 4, ["#", "Source", "Link"], [6, 60, 60])
            for r_i, s in enumerate(srcs, start=5):
                ws.cell(row=r_i, column=1, value=f"[{s['n']}]")
                ws.cell(row=r_i, column=2, value=s["title"])
                if s["url"]:
                    link = ws.cell(row=r_i, column=3, value=s["url"])
                    link.hyperlink = s["url"]
                    link.font = Font(color="FF0563C1", underline="single")
    finally:
        X._fill_var.reset(token)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), want


def read_back(data: bytes, want: Dict[str, Any]) -> List[str]:
    """Number cells whose saved value isn't what the answer said ([] = all as written)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))
    wrong = []
    for key, v in want.items():
        name, ref = key.rsplit("!", 1)
        got = wb[name][ref].value
        if not isinstance(got, (int, float)) or abs(float(got) - float(v)) > 0.0001:
            wrong.append(f"{key}: {got!r} != {v!r}")
    return wrong


def title_of(text: str) -> str:
    """A title for the file: the answer's first heading, else "Vula answer"."""
    for ln in (text or "").splitlines():
        h = _HEADING.match(ln)
        if h and "|" not in ln:
            return _clean(h.group(1))[:80]
    return "Vula answer"


def filename(title: str, on: Optional[date] = None) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:60] or "Vula_answer"
    return f"{stem}_{(on or date.today()).isoformat()}.xlsx"
