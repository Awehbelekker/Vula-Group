"""A small in-memory stand-in for the Supabase client, for tests that need real filtering.

Supports the query shapes the code under test uses: select / insert / upsert / update / delete with
eq, neq, in_, like, ilike, gt, gte, lt, order, limit, range. Rows are plain dicts held per table, so a test can
seed `db.rows["table"]` and then assert on what is left.
"""
from __future__ import annotations

import fnmatch
import uuid
from typing import Any, Callable, Dict, List


class _Result:
    def __init__(self, data: List[dict]):
        self.data = data


class _Query:
    def __init__(self, db: "FakeSupabase", table: str):
        self._db, self._table = db, table
        self._filters: List[Callable[[dict], bool]] = []
        self._op, self._payload = "select", None
        self._order, self._limit = None, None
        self._single = False

    # ── operations ──
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict: str = "", **_k):
        self._op, self._payload = "upsert", payload
        # with no on_conflict, Postgres upserts on the table's primary key (db.primary_keys)
        self._conflict = ([c.strip() for c in on_conflict.split(",") if c.strip()]
                          or list(self._db.primary_keys.get(self._table, [])))
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    # ── filters ──
    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def neq(self, col, val):
        self._filters.append(lambda r: r.get(col) != val)
        return self

    def is_(self, col, val):
        """PostgREST `is.null` / `is.true` / `is.false`."""
        want = {"null": None, "true": True, "false": False}.get(str(val).lower(), val)
        self._filters.append(lambda r: r.get(col) is want if want is None else r.get(col) == want)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def like(self, col, pattern):
        glob = pattern.replace("%", "*").replace("_", "?")
        self._filters.append(lambda r: fnmatch.fnmatchcase(str(r.get(col) or ""), glob))
        return self

    def ilike(self, col, pattern):
        glob = pattern.lower().replace("%", "*").replace("_", "?")
        self._filters.append(lambda r: fnmatch.fnmatchcase(str(r.get(col) or "").lower(), glob))
        return self

    def gt(self, col, val):
        self._filters.append(lambda r: str(r.get(col) or "") > str(val))
        return self

    def gte(self, col, val):
        self._filters.append(lambda r: str(r.get(col) or "") >= str(val))
        return self

    def lt(self, col, val):
        self._filters.append(lambda r: str(r.get(col) or "") < str(val))
        return self

    def order(self, col, desc: bool = False):
        self._order = (col, desc)
        return self

    def single(self):
        """PostgREST .single(): one row as a dict (None when there is none)."""
        self._single = True
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, lo, hi):
        self._limit = hi - lo + 1
        return self

    # ── run ──
    def execute(self) -> _Result:
        rows = self._db.rows.setdefault(self._table, [])
        if self._table in self._db.fail:
            raise RuntimeError(f"{self._table} unavailable")
        if self._op == "upsert" and self._conflict:
            out = []
            for new in (self._payload if isinstance(self._payload, list) else [self._payload]):
                hit = next((r for r in rows if all(r.get(c) == new.get(c) for c in self._conflict)), None)
                if hit:
                    hit.update(new)
                else:
                    hit = dict(new)
                    rows.append(hit)
                out.append(dict(hit))
            return _Result(out)
        if self._op in ("insert", "upsert"):
            new = self._payload if isinstance(self._payload, list) else [self._payload]
            new = [{"id": str(uuid.uuid4()), **r} if "id" not in r else dict(r) for r in new]
            rows.extend(new)
            return _Result([dict(r) for r in new])
        hit = [r for r in rows if all(f(r) for f in self._filters)]
        if self._op == "delete":
            self._db.rows[self._table] = [r for r in rows if r not in hit]
            return _Result([dict(r) for r in hit])
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
            return _Result([dict(r) for r in hit])
        if self._order:
            col, desc = self._order
            hit = sorted(hit, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            hit = hit[: self._limit]
        if self._single:
            return _Result(dict(hit[0]) if hit else None)
        return _Result([dict(r) for r in hit])


class FakeSupabase:
    def __init__(self, rows: Dict[str, List[dict]] | None = None):
        self.rows: Dict[str, List[dict]] = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail: set = set()      # table names whose queries raise
        self.primary_keys: Dict[str, List[str]] = {}   # table -> key columns, for a bare upsert

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def count(self, table: str, **match: Any) -> int:
        return sum(1 for r in self.rows.get(table, []) if all(r.get(k) == v for k, v in match.items()))
