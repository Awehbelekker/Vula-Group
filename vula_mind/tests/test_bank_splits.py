"""One bank payment split across projects (3 Nov 2026, migration 244).

A R19,000 payment to a subcontractor paid workers on two jobs: R15,050 for one, the rest for the
other. A bank line could carry one project only, so one job looked R15,050 too dear and the other
R15,050 too cheap. Made-up business, projects and figures throughout.
"""
import asyncio
from types import SimpleNamespace

import pytest

from vula.commerce import bank_splits as bs

T = "thandi-demo"
PROJECTS = ["Seaview", "Oak Street", "Main Road"]


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.rows_in = db, name, [], None, None

    def select(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def range(self, *a): return self
    def eq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) == v); return self
    def neq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) != v); return self
    def gte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) >= str(v)); return self
    def lte(self, k, v): self.f.append(lambda r, k=k, v=v: str(r.get(k)) <= str(v)); return self
    def insert(self, rows): self.rows_in = rows if isinstance(rows, list) else [rows]; return self
    def update(self, patch): self.patch = patch; return self

    def execute(self):
        table = self.db.tables.setdefault(self.name, [])
        if self.rows_in is not None:
            if self.db.fail_insert:
                return SimpleNamespace(data=[])
            for r in self.rows_in:
                table.append({"id": f"s{len(table) + 1}", **r})
            return SimpleNamespace(data=list(self.rows_in))
        hit = [r for r in table if all(f(r) for f in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=[dict(r) for r in hit])


class _DB:
    def __init__(self, **t):
        self.tables, self.fail_insert = t, False

    def table(self, name):
        return _Q(self, name)


def _line(i, amount, project, date="2026-09-25", desc="FNB App Payment To Ceiling Team Wages", tenant=T):
    return {"id": i, "tenant_id": tenant, "txn_date": date, "description": desc, "amount_cents": amount,
            "direction": "out", "project": project, "account_code": "casual_labour", "match_status": "unmatched"}


@pytest.fixture()
def db(monkeypatch):
    fake = _DB(commerce_bank_transactions=[
        _line("t1", 1900000, "Oak Street"),
        _line("t2", 1900000, "Main Road", date="2026-08-01", desc="Payshap Stock"),
        _line("t3", 500000, "Seaview", date="2026-09-26", desc="Tiler"),
        _line("x1", 1900000, "Oak Street", tenant="other"),
    ], commerce_bank_splits=[], vula_decisions=[])
    monkeypatch.setattr("vula.commerce.service._client", lambda: fake)
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda t: list(PROJECTS))
    monkeypatch.setattr("vula.commerce.decisions.record", lambda *a, **k: fake.tables["vula_decisions"].append(a))
    return fake


@pytest.mark.parametrize("text,cents", [("R15,050", 1505000), ("15 050.00", 1505000), (15050, 1505000),
                                        ("R19,000.00", 1900000), ("12,5", 1250), ("nope", None), (None, None)])
def test_amounts_as_the_owner_writes_them(text, cents):
    assert bs.to_cents(text) == cents


def test_a_split_gives_each_project_its_part_and_leaves_the_rest(db):
    res = bs.split(T, "t1", [{"project": "seaview", "amount_cents": 1505000}], by="Sam")
    assert res["remainder_cents"] == 395000
    assert [(p["project"], p["amount_cents"]) for p in res["parts"]] == [("Seaview", 1505000)]
    rows = bs.apply(T, [dict(r) for r in db.tables["commerce_bank_transactions"] if r["tenant_id"] == T])
    by_project = {}
    for r in rows:
        by_project[r["project"]] = by_project.get(r["project"], 0) + r["amount_cents"]
    assert by_project == {"Oak Street": 395000, "Seaview": 1505000 + 500000, "Main Road": 1900000}
    assert sum(r["amount_cents"] for r in rows) == 1900000 * 2 + 500000          # nothing made up or lost
    assert db.tables["commerce_bank_transactions"][0]["amount_cents"] == 1900000  # the bank line is untouched
    assert db.tables["vula_decisions"]                                            # kept as a decision


def test_a_whole_line_split_off_leaves_nothing_behind(db):
    bs.split(T, "t1", [{"project": "Seaview", "amount_cents": 1000000}, {"project": "Main Road", "amount_cents": 900000}])
    rows = bs.apply(T, [dict(db.tables["commerce_bank_transactions"][0])])
    assert sorted((r["project"], r["amount_cents"]) for r in rows) == [("Main Road", 900000), ("Seaview", 1000000)]


def test_a_new_split_replaces_the_old_one_never_deletes(db):
    bs.split(T, "t1", [{"project": "Seaview", "amount_cents": 1505000}])
    bs.split(T, "t1", [{"project": "Seaview", "amount_cents": 1510000}])
    rows = db.tables["commerce_bank_splits"]
    assert [r["status"] for r in rows] == ["replaced", "active"]
    assert bs.unsplit(T, "t1") == 1
    assert bs.apply(T, [dict(db.tables["commerce_bank_transactions"][0])])[0]["project"] == "Oak Street"


@pytest.mark.parametrize("parts,err", [
    ([], "which project"),
    ([{"project": "Hillside", "amount_cents": 100}], "no project called"),
    ([{"project": "Seaview", "amount_cents": 0}], "more than R0"),
    ([{"project": "Seaview", "amount_cents": 1900001}], "more than the payment"),
    ([{"project": "Oak Street", "amount_cents": 100}], "already on Oak Street"),
])
def test_bad_parts_are_refused_with_the_reason(db, parts, err):
    with pytest.raises(ValueError, match=f"(?i){err}"):
        bs.split(T, "t1", parts)
    assert db.tables["commerce_bank_splits"] == []


def test_another_business_line_cannot_be_split(db):
    with pytest.raises(LookupError):
        bs.split(T, "x1", [{"project": "Seaview", "amount_cents": 100}])


def test_a_split_that_does_not_read_back_says_so(db):
    db.fail_insert = True
    with pytest.raises(RuntimeError):
        bs.split(T, "t1", [{"project": "Seaview", "amount_cents": 100}])


def test_find_the_payment_by_amount_date_and_word(db):
    assert [r["id"] for r in bs.find_lines(T, 1900000)] == ["t1", "t2"]
    assert [r["id"] for r in bs.find_lines(T, 1900000, "2026-09-24")] == ["t1"]
    assert [r["id"] for r in bs.find_lines(T, 1900000, words="wages")] == ["t1"]


def test_job_costing_counts_each_part_on_its_project(db):
    from vula.commerce import job_costing
    bs.split(T, "t1", [{"project": "Seaview", "amount_cents": 1505000}])
    rows = job_costing._txns(T)
    seaview = sum(r["amount_cents"] for r in rows if r.get("project") == "Seaview")
    oak = sum(r["amount_cents"] for r in rows if r.get("project") == "Oak Street")
    assert (seaview, oak) == (1505000 + 500000, 395000)


def test_before_migration_244_nothing_changes(db, monkeypatch):
    rows = [dict(r) for r in db.tables["commerce_bank_transactions"]]
    monkeypatch.setattr(bs, "_active", lambda t: {})
    assert bs.apply(T, rows) == rows


# ── the WhatsApp tool: preview, then the split, read back ────────────────────

def test_the_tool_previews_then_splits(db):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill.__new__(CommerceAdminSkill)
    args = {"amount": "R19,000", "date": "2026-09-25", "words": "wages",
            "parts": [{"project": "Seaview", "amount": "R15,050"}]}
    prev = skill._split_payment(T, args, {"caller_name": "Sam"})
    assert prev["preview"] and "R15,050.00 to Seaview" in prev["message"] and "R3,950.00 stays on Oak Street" in prev["message"]
    assert db.tables["commerce_bank_splits"] == []
    done = skill._split_payment(T, {**args, "confirm": True}, {"caller_name": "Sam"})
    assert done["verified"] and "R15,050.00 to Seaview" in done["reply_verbatim"]
    assert [r["project"] for r in db.tables["commerce_bank_splits"]] == ["Seaview"]


def test_the_tool_asks_which_payment_when_two_match(db):
    from core.skills.commerce_admin import CommerceAdminSkill
    skill = CommerceAdminSkill.__new__(CommerceAdminSkill)
    out = skill._split_payment(T, {"amount": "19000", "parts": [{"project": "Seaview", "amount": "100"}]})
    assert "which one" in out["error"] and len(out["choices"]) == 2
    assert asyncio.iscoroutinefunction(skill._split_payment) is False
