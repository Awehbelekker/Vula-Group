"""The decision log (vula/commerce/decisions.py, migration 235), 31 Oct 2026.

Ian: "start with the decision log". The owner's rulings on a project (who paid, what's in or out,
why a figure is what it is) are kept with their reason, followed in later answers, and quoted when
someone asks why. All figures made up.
"""
import pytest

from tests.test_job_costing import FakeDB
from vula.commerce import decisions, service

TID = "digg-demo"


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"vula_projects": [{"tenant_id": TID, "name": "Belladonna", "number": None, "aliases": []},
                                     {"tenant_id": TID, "name": "Porterfield", "number": "2604", "aliases": []}],
                   "vula_decisions": []})
    monkeypatch.setattr(service, "_client", lambda: fake)
    stamps = iter(f"2026-10-{d:02d}T10:00:00+00:00" for d in range(1, 29))
    monkeypatch.setattr(decisions, "_now", lambda: next(stamps))
    return fake


def test_a_ruling_is_kept_on_its_project_and_read_back(db):
    r = decisions.record(TID, "The R1,234 plumbing change was paid by the business.", project="belladonna",
                         subject="Plumbing change", amount_cents=123_400, made_by="Ian", role="owner",
                         phone="0821111111")
    assert (r["project"], r["status"], r["amount_cents"], r["made_by"]) == ("Belladonna", "active", 123_400, "Ian")
    assert [d["id"] for d in decisions.for_project(TID, "Belladonna")] == [r["id"]]
    assert decisions.for_project(TID, "Porterfield") == []


def test_the_same_ruling_twice_is_kept_once(db):
    decisions.record(TID, "Sea Point Hire was Belladonna", project="Belladonna")
    again = decisions.record(TID, "sea point hire was belladonna", project="Belladonna")
    assert again["already_known"] and len(db.tables["vula_decisions"]) == 1


def test_empty_or_too_long_is_refused(db):
    with pytest.raises(ValueError):
        decisions.record(TID, "  ")
    with pytest.raises(ValueError):
        decisions.record(TID, "x" * 900)


def test_a_new_ruling_replaces_the_old_one(db):
    old = decisions.record(TID, "Extra tile labour R500 is billed to the client", project="Belladonna")
    new = decisions.record(TID, "Take out the extra tile labour", project="Belladonna", supersedes=old["id"])
    assert [d["id"] for d in decisions.for_project(TID, "Belladonna")] == [new["id"]]
    replaced = [d for d in db.tables["vula_decisions"] if d["id"] == old["id"]][0]
    assert (replaced["status"], replaced["superseded_by"]) == ("superseded", new["id"])


def test_withdrawn_is_gone_from_answers(db):
    d = decisions.record(TID, "Painting is finished", project="Belladonna")
    assert decisions.withdraw(TID, d["id"])
    assert decisions.for_project(TID, "Belladonna") == []
    assert not decisions.withdraw(TID, d["id"])           # already taken back


def test_another_business_never_sees_them(db):
    decisions.record(TID, "Every tile supplier invoice is Belladonna", project="Belladonna")
    assert decisions.for_project("off-the-hook", "Belladonna") == []
    assert decisions.search("off-the-hook", "tile") == []
    assert decisions.block_for_prompt("off-the-hook", "what about belladonna tiles") == ""


def test_why_is_a_figure_finds_the_ruling_by_amount_or_words(db):
    decisions.record(TID, "The business paid the plumbing change", project="Belladonna",
                     subject="Plumbing change", amount_cents=123_400)
    decisions.record(TID, "Further painting is billed separately", project="Belladonna")
    assert decisions.search(TID, "why is it R1 234?")[0]["subject"] == "Plumbing change"
    assert decisions.search(TID, "why 1,234.00")[0]["subject"] == "Plumbing change"
    assert decisions.search(TID, "what about painting")[0]["decision"].startswith("Further painting")
    assert decisions.search(TID, "plumbing", project="Porterfield") == []


def test_the_prompt_block_carries_the_named_projects_decisions(db):
    decisions.record(TID, "The business paid the plumbing change", project="Belladonna")
    decisions.record(TID, "Porterfield was funded by the owner's partner", project="Porterfield")
    block = decisions.block_for_prompt(TID, "why is the plumbing on Belladonna so high?")
    assert "plumbing change" in block and "Porterfield was funded" not in block
    assert decisions.block_for_prompt(TID, "what's the weather") == ""


def test_undo_takes_back_the_callers_last_decision(db):
    decisions.record(TID, "First", project="Belladonna", phone="0821111111")
    second = decisions.record(TID, "Second", project="Belladonna", phone="+27 82 111 1111")
    decisions.record(TID, "Someone else's", project="Belladonna", phone="0829999999")
    assert decisions.last_by(TID, "27821111111")["id"] == second["id"]


def test_before_the_migration_reads_are_empty_not_errors(monkeypatch):
    class _Broken:
        def table(self, _n):
            raise RuntimeError("relation vula_decisions does not exist")
    monkeypatch.setattr(service, "_client", lambda: _Broken())
    assert decisions.for_project(TID, "Belladonna") == []
    assert decisions.search(TID, "plumbing") == []
    assert decisions.block_for_prompt(TID, "belladonna") == ""


# ── the admin agent's tools ─────────────────────────────────────────────────

def _agent():
    from core.skills.commerce_admin import CommerceAdminSkill
    return CommerceAdminSkill()


def test_the_owner_records_a_decision_on_whatsapp(db):
    out = _agent()._decision_tool("record_decision", TID, {
        "decision": "The R1,234 plumbing change was paid by the business", "project": "Belladonna",
        "amount": 1234}, {"caller_role": "owner", "caller_name": "Ian", "phone": "0821111111"})
    assert out["reply_verbatim"].startswith("📝 Noted on *Belladonna*")
    row = db.tables["vula_decisions"][0]
    assert (row["amount_cents"], row["made_by"], row["source"]) == (123_400, "Ian", "whatsapp")


def test_staff_can_look_up_but_not_record(db):
    agent = _agent()
    out = agent._decision_tool("record_decision", TID, {"decision": "x"}, {"caller_role": "staff"})
    assert "owner or a manager" in out["error"] and db.tables["vula_decisions"] == []
    decisions.record(TID, "The business paid the plumbing change", project="Belladonna")
    found = agent._decision_tool("decision_log", TID, {"about": "plumbing"}, {"caller_role": "staff"})
    assert found["count"] == 1 and found["decisions"][0]["decision"].startswith("The business paid")


def test_no_decision_says_so_instead_of_inventing_one(db):
    out = _agent()._decision_tool("decision_log", TID, {"about": "roof", "project": "Belladonna"},
                                  {"caller_role": "owner"})
    assert out["count"] == 0 and "No decision on Belladonna" in out["message"]


def test_a_replacing_decision_and_undo(db):
    agent = _agent()
    ctx = {"caller_role": "owner", "phone": "0821111111"}
    agent._decision_tool("record_decision", TID, {"decision": "Extra tile labour is billed",
                                                  "project": "Belladonna"}, ctx)
    out = agent._decision_tool("record_decision", TID, {"decision": "Take out the extra tile labour",
                                                        "project": "Belladonna", "replaces": "tile labour"}, ctx)
    assert "replaces the earlier decision" in out["reply_verbatim"]
    assert [d["decision"] for d in decisions.for_project(TID, "Belladonna")] == ["Take out the extra tile labour"]
    undone = agent._decision_tool("withdraw_decision", TID, {"match": "last"}, ctx)
    assert undone["verified"] and decisions.for_project(TID, "Belladonna") == []


def test_the_tools_are_offered_to_owners_but_not_reps():
    from core.skills.commerce_admin import _tools_for
    names = lambda tools: {t["function"]["name"] for t in tools}   # noqa: E731
    assert {"record_decision", "decision_log", "withdraw_decision"} <= names(_tools_for(TID, role="owner"))
    assert not {"record_decision", "decision_log"} & names(_tools_for(TID, role="sales_rep"))


def test_a_client_slip_decision_is_logged(db):
    from vula.commerce import project_client as pc
    db.tables["vula_project_direct_payments"] = [{"id": "p1", "tenant_id": TID, "project": "Belladonna",
                                                  "supplier": "Tile Co", "amount_cents": 50_000,
                                                  "status": "proposed", "paid_on": None, "created_by": "27820000000"}]
    pc.decide_slip(TID, "p1", True, by_phone="0821111111")
    row = db.tables["vula_decisions"][0]
    assert row["project"] == "Belladonna" and "accepted" in row["decision"]
    assert row["refs"] == [{"table": "vula_project_direct_payments", "id": "p1"}]
