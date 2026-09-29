"""2026-09-29, Judy (digg-demo owner): "Vula is not answering… can't link things based on project
numbers". With a council letter waiting for a project, her next messages were taken as the
project answer: two filed the letter under ATLANTIS FOODS on a coincidental word, "What data are
you using to reference cost" got "I couldn't match that to a project", and none reached the
assistant. Real project register: HPC Bokaap (DIGG-2024-017), Porterfield (2604), Breco Seafoods.
"""
from unittest.mock import MagicMock

import pytest

from vula.integrations import doc_filing, project_resolver

REGISTER = [{"name": "HPC Bokaap", "number": "DIGG-2024-017"},
            {"name": "Porterfield", "number": "2604"},
            {"name": "Breco Seafoods", "number": None}]
USED = ["HPC Bokaap", "HPC_Bokaap", "ATLANTIS FOODS", "Sporty – Phase 2", "PORTERFIELD"]


def _canon(_tid, name):
    from vula.commerce.service import project_key
    for r in REGISTER:
        if project_key(name) in (project_key(r["name"]), project_key(r["number"])):
            return r["name"]
    return {"hpc bokaap": "HPC Bokaap"}.get(project_key(name), name)


@pytest.fixture
def digg(monkeypatch):
    q = MagicMock()
    q.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=REGISTER)
    monkeypatch.setattr(project_resolver, "_client", lambda: MagicMock(table=lambda _t: q))
    monkeypatch.setattr("vula.commerce.expenses.known_projects", lambda _t: list(USED))
    monkeypatch.setattr("vula.commerce.service.canonical_project", _canon)
    monkeypatch.setattr(doc_filing, "_own_names", lambda _t: ["DIGG", "Aweh Be Lekker t/a DIGG Collection"])


@pytest.mark.parametrize("text", [
    "What data are you using to reference cost",              # real, 07:03
    "Are you ok?", "?",
    "Can you give me the landlord details that Barend shared with me in email",
    "Please link the Atlantis invoices to the Paarden Island office job and show me the cost so far",
    "I need the HPC variation claim filed and the total",
])
def test_a_real_message_is_never_taken_as_a_project_answer(text):
    assert not doc_filing.looks_like_project_answer(text)


@pytest.mark.parametrize("text", ["HPC", "2604", "Porterfield", "yes", "skip", "Atlantis Foods",
                                  "DIGG-2024-017", "hpc_bokaap"])
def test_a_project_answer_still_is(text):
    assert doc_filing.looks_like_project_answer(text)


def test_project_numbers_and_spellings_link_to_the_project(digg):
    assert doc_filing._named_project_answer("digg-demo", "2604")["project"] == "Porterfield"
    assert doc_filing._named_project_answer("digg-demo", "DIGG-2024-017")["project"] == "HPC Bokaap"
    assert doc_filing._named_project_answer("digg-demo", "hpc_bokaap")["project"] == "HPC Bokaap"
    assert doc_filing._named_project_answer("digg-demo", "HPC001")["project"] == "HPC Bokaap"


@pytest.mark.asyncio
async def test_a_waiting_document_does_not_swallow_her_question(monkeypatch):
    pending = [{"id": "d1", "filename": "$value (5).pdf", "status": "pending_project",
                "filed_by": "27827077080", "fields": {}, "summary": "Molteno Court plans NOT IN ORDER"}]
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.gte.return_value = q
    q.order.return_value = q
    q.limit.return_value = q
    q.execute.return_value = MagicMock(data=pending)
    monkeypatch.setattr(doc_filing, "_client", lambda: MagicMock(table=lambda _t: q))
    monkeypatch.setattr(doc_filing, "match_project",
                        lambda *_a: {"project": "ATLANTIS FOODS", "clickup_list_id": "L1",
                                     "confidence": 0.6, "ambiguous": False})
    for text in ("What data are you using to reference cost",
                 "Please link the Atlantis invoices to the Paarden Island office job and show me the cost so far"):
        assert await doc_filing.resolve_pending_document("digg-demo", "27827077080", text) is None
    q.update.assert_not_called()


def test_fuel_stations_and_trade_words_are_never_learned_as_a_project(monkeypatch):
    monkeypatch.setattr(doc_filing, "_own_names", lambda _t: ["DIGG"])
    monkeypatch.setattr("vula.commerce.party.resolve_party_name", lambda f, exclude=(): f.get("supplier"))
    assert doc_filing._signals_from({"supplier": "Astron Energy Buitengracht St"}, "digg-demo") == []
    assert doc_filing._signals_from({"supplier": "Builders"}, "digg-demo") == []
    assert doc_filing._signals_from({"supplier": "Solid Cape (Pty) Ltd"}, "digg-demo") == [
        ("supplier", "solid cape (pty) ltd")]


def test_rules_under_two_spellings_of_one_project_are_not_ambiguous(monkeypatch):
    rows = [{"project": "HPC_Bokaap", "signal": "edison maunganidze", "hits": 1},
            {"project": "HPC Bokaap", "signal": "edison maunganidze", "hits": 1}]
    q = MagicMock()
    q.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = \
        MagicMock(data=rows)
    monkeypatch.setattr(doc_filing, "_client", lambda: MagicMock(table=lambda _t: q))
    monkeypatch.setattr(doc_filing, "_signals_from", lambda f, t=None: [("supplier", "edison maunganidze")])
    monkeypatch.setattr("vula.commerce.service.canonical_project", _canon)
    got = doc_filing.lookup_learned_project("digg-demo", {"supplier": "Edison Maunganidze"})
    assert got["project"] == "HPC Bokaap" and not got["ambiguous"]
