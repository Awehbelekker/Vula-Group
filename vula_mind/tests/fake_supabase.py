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

    def lte(self, col, val):
        self._filters.append(lambda r: str(r.get(col) or "") <= str(val))
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


class _Rpc:
    def __init__(self, run: Callable[[], Any]):
        self._run = run

    def execute(self) -> _Result:
        return _Result(self._run())


class FakeSupabase:
    def __init__(self, rows: Dict[str, List[dict]] | None = None):
        self.rows: Dict[str, List[dict]] = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail: set = set()      # table names whose queries raise
        self.primary_keys: Dict[str, List[str]] = {}   # table -> key columns, for a bare upsert

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    # ── the ledger's database functions (migration 288), emulated ──
    def rpc(self, name: str, params: Dict[str, Any]) -> _Rpc:
        fn = getattr(self, "_rpc_" + name, None)
        if fn is None:
            raise RuntimeError(f"rpc {name} isn't emulated in FakeSupabase")
        return _Rpc(lambda: fn(**params))

    def _account_id(self, tenant_id: str, code: str) -> str:
        acc = next((a for a in self.rows.get("commerce_accounts", [])
                    if a.get("tenant_id") == tenant_id and a.get("code") == code), None)
        if acc is None:
            raise RuntimeError(f"unknown account code {code} for tenant {tenant_id}")
        acc.setdefault("id", str(uuid.uuid4()))
        return acc["id"]

    def _rpc_post_journal_entry_v2(self, p_tenant_id, p_entry_date, p_description, p_source_type, p_source_id,
                                   p_lines, p_meta=None):
        p_meta = p_meta or {}
        for view in ("accrual", "cash"):
            dr = sum(int(ln.get("debit_cents") or 0) for ln in p_lines if (ln.get("basis") or "") in ("", view))
            cr = sum(int(ln.get("credit_cents") or 0) for ln in p_lines if (ln.get("basis") or "") in ("", view))
            if dr != cr:
                raise RuntimeError(f"unbalanced journal entry ({view} view): debits {dr} != credits {cr}")
        if not any(int(ln.get("debit_cents") or 0) for ln in p_lines):
            raise RuntimeError("journal entry has zero value")
        entries = self.rows.setdefault("journal_entries", [])
        version = 1
        if p_source_id is not None:
            mine = [e for e in entries if e["tenant_id"] == p_tenant_id and e["source_type"] == p_source_type
                    and e["source_id"] == p_source_id]
            reversed_ids = {e.get("reverses_entry_id") for e in entries}
            live = sorted((e for e in mine if e["id"] not in reversed_ids), key=lambda e: -e["version"])
            if live:
                return live[0]["id"]
            version = max([e["version"] for e in mine], default=0) + 1
        rev = p_meta.get("reverses_entry_id")
        if rev and not any(e["id"] == rev and e["tenant_id"] == p_tenant_id for e in entries):
            raise RuntimeError("the entry to reverse is not this business's")
        if rev and any(e.get("reverses_entry_id") == rev for e in entries):
            raise RuntimeError("duplicate key: journal_entries_reversed_once")
        ids = [(ln, self._account_id(p_tenant_id, ln["account_code"])) for ln in p_lines]
        eid = str(uuid.uuid4())
        entries.append({"id": eid, "tenant_id": p_tenant_id, "entry_date": p_entry_date, "description": p_description,
                        "source_type": p_source_type, "source_id": p_source_id, "version": version,
                        "reverses_entry_id": rev, "doc_id": p_meta.get("doc_id"), "bank_txn_id": p_meta.get("bank_txn_id")})
        for ln, acc_id in ids:
            self.rows.setdefault("journal_lines", []).append({
                "id": str(uuid.uuid4()), "journal_entry_id": eid, "account_id": acc_id,
                "debit_cents": int(ln.get("debit_cents") or 0), "credit_cents": int(ln.get("credit_cents") or 0),
                "memo": ln.get("memo"), "basis": ln.get("basis") or None, "bank_account_id": ln.get("bank_account_id")})
        return eid

    def _rpc_ledger_balances(self, p_tenant_id, p_from=None, p_to=None, p_basis="accrual"):
        entries = {e["id"]: e for e in self.rows.get("journal_entries", []) if e["tenant_id"] == p_tenant_id
                   and (p_from is None or str(e["entry_date"]) >= str(p_from))
                   and (p_to is None or str(e["entry_date"]) <= str(p_to))}
        accounts = {a.get("id"): a for a in self.rows.get("commerce_accounts", []) if a.get("tenant_id") == p_tenant_id}
        out: Dict[tuple, dict] = {}
        for ln in self.rows.get("journal_lines", []):
            if ln["journal_entry_id"] not in entries or ln.get("basis") not in (None, p_basis):
                continue
            a = accounts.get(ln["account_id"], {})
            k = (a.get("code"), ln.get("bank_account_id"))
            row = out.setdefault(k, {"account_code": a.get("code"), "account_name": a.get("name"),
                                     "account_type": a.get("type"), "debit_cents": 0, "credit_cents": 0,
                                     "bank_account_id": ln.get("bank_account_id")})
            row["debit_cents"] += ln["debit_cents"]
            row["credit_cents"] += ln["credit_cents"]
        return list(out.values())

    def count(self, table: str, **match: Any) -> int:
        return sum(1 for r in self.rows.get(table, []) if all(r.get(k) == v for k, v in match.items()))
