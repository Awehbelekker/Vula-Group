"""A project's fee as agreed (vula/commerce/job_costing.py, migration 255): nothing assumed."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.commerce import job_costing as jc


class _T:
    def __init__(self, db): self.db = db
    def select(self, *a): return self
    def eq(self, *a): return self
    def limit(self, *a): return self
    def upsert(self, row, **kw): self.db["row"] = row; return self
    def execute(self): return SimpleNamespace(data=list(self.db.get("rows", [])))


def _db(rows=None):
    db = {"rows": rows or []}
    return db, SimpleNamespace(table=lambda n: _T(db))


def test_only_fees_someone_set_are_agreed():
    db, c = _db([{"project": "Belladonna", "pricing": "fixed_fee", "fee_pct": None, "fee_cents": 4800000},
                 {"project": "*", "pricing": "cost_plus", "fee_pct": 10, "fee_cents": None},
                 {"project": "Broken", "pricing": "fixed_fee", "fee_pct": 10, "fee_cents": None}])
    with patch.object(jc, "_client", return_value=c):
        t = jc.agreed_terms("t1")
    assert t == {"Belladonna": {"pricing": "fixed_fee", "fee_pct": None, "fee_cents": 4800000},
                 "*": {"pricing": "cost_plus", "fee_pct": 10.0, "fee_cents": None}}


def test_nothing_set_means_nothing_agreed():
    _db_, c = _db([])
    with patch.object(jc, "_client", return_value=c):
        assert jc.agreed_terms("t1") == {}
        assert jc.terms("t1") == {"*": jc.DEFAULT_FEE_PCT}      # the owner's reports may still assume one


@pytest.mark.parametrize("pricing,kw,msg", [
    ("hourly", {}, "percentage on cost or a fixed amount"),
    ("cost_plus", {"fee_pct": 120}, "between 0 and 100"),
    ("cost_plus", {}, "between 0 and 100"),
    ("fixed_fee", {"fee_cents": -1}, "R0 or more"),
    ("fixed_fee", {}, "R0 or more"),
])
def test_a_fee_must_make_sense(pricing, kw, msg):
    with pytest.raises(ValueError, match=msg):
        jc.set_terms("t1", "Belladonna", pricing, **kw)


def test_setting_a_fixed_fee_is_read_back():
    db, c = _db()
    with patch.object(jc, "_client", return_value=c):
        db["rows"] = [{"project": "Belladonna", "pricing": "fixed_fee", "fee_pct": None, "fee_cents": 4800000}]
        out = jc.set_terms("t1", "Belladonna", "fixed_fee", fee_cents=4800000, by="owner")
    assert out == {"project": "Belladonna", "pricing": "fixed_fee", "fee_pct": None, "fee_cents": 4800000}
    assert db["row"]["fee_pct"] is None and db["row"]["agreed_by"] == "owner"
