"""vula/db_pages.py — read every row a query matches, page by page.

The database (PostgREST) hands back at most 1 000 rows a request, whatever `.limit(N)` asks for, and
says nothing when it cuts a list short. 22 Nov 2026: DIGG's year of 1 346 bank lines came back as 998,
so the year's P&L was short (found by the year-end pack's add-up check); 96 places asked for more than
1 000 rows in one go. `all_rows` reads 1 000 at a time until the query runs out, or until `cap` rows
(the "latest 5 000" some screens mean). tests/test_no_silent_row_cap.py keeps `.limit(N > 1000)` out.

Pages need a stable order or a row can be skipped or read twice between them, so `all_rows` adds
`order` (the table's key, `id` by default) after any order the query already has.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

PAGE = 1000


def all_rows(make_query: Callable[[], Any], cap: Optional[int] = None, order: Optional[str] = "id",
             page: int = PAGE) -> List[Dict[str, Any]]:
    """Run `make_query()` (a fresh query each call, without .limit/.range) page by page."""
    first = make_query()
    if not callable(getattr(first, "range", None)):
        # a client that can't page (the tests' stand-ins for the database): one read, as before
        q = first.limit(cap) if cap is not None and callable(getattr(first, "limit", None)) else first
        rows = list(q.execute().data or [])
        return rows[:cap] if cap is not None else rows
    out: List[Dict[str, Any]] = []
    start, q = 0, first
    while True:
        size = page if cap is None else min(page, cap - len(out))
        if size <= 0:
            return out
        if order:
            q = q.order(order)
        rows = q.range(start, start + size - 1).execute().data or []
        out.extend(rows)
        if len(rows) < size:
            return out
        start += size
        q = make_query()
