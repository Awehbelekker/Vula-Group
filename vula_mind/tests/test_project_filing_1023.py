"""DIGG, 23 Oct: filing to a project by chat, new projects, and one list instead of many questions.

Ian sent an invoice, then "Please log in Atlantic Paarden Island" — Vula ran a web search and said
it couldn't find "Atlantic Paarden Island". Then "It's for a new project we started" got a web
search about starting a business. Judy had eleven "which project?" questions, one at a time.
"""
from unittest.mock import AsyncMock

import pytest

from vula import open_questions as oq
from vula.api import whatsapp as wa
from vula.commerce import expenses

PROJECTS = ["Atlantis Paarden Eiland", "Belladonna", "HPC Bokaap", "Sporty TV"]


@pytest.fixture()
def chat(monkeypatch):
    sent, asked, closed, filed = [], [], [], []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append(m) or True))
    monkeypatch.setattr(wa, "_is_team", lambda t, p: True)
    monkeypatch.setattr(expenses, "known_projects", lambda t: list(PROJECTS))
    monkeypatch.setattr(wa, "_recent_upload", lambda t, p: {"docs": [], "sent": True})
    monkeypatch.setattr(oq, "ask", lambda t, p, kind, ref, prompt="": asked.append((kind, ref, prompt)) or "q1")
    monkeypatch.setattr(oq, "close", lambda qid, answer="", status="answered": closed.append((qid, status)))
    monkeypatch.setattr(oq, "open_for", lambda t, p, limit=5: [])

    async def _file(t, p, project, doc_ids=None):
        filed.append((project, doc_ids))

    monkeypatch.setattr(wa, "_file_recent_under", _file)
    return sent, asked, closed, filed


@pytest.mark.parametrize("text,name", [
    ("Please log in Atlantic Paarden Island", "Atlantic Paarden Island"),
    ("file it under Belladonna", "Belladonna"),
    ("save this to the HPC project", "HPC project"),
    ("Can you put the invoice on Sporty TV please", "Sporty TV please"),
])
def test_filing_instructions_are_understood(text, name):
    m = wa._FILE_UNDER_RE.match(text)
    assert m and m.group("name") == name


@pytest.mark.asyncio
async def test_log_in_a_known_project_files_it_instead_of_web_search(chat):
    sent, asked, closed, filed = chat
    assert await wa._answer_batch_project("digg-demo", "27645755210", "Please log in Atlantic Paarden Island")
    assert filed == [("Atlantis Paarden Eiland", None)]


@pytest.mark.asyncio
async def test_an_unknown_project_is_offered_as_new_and_yes_adds_it(chat, monkeypatch):
    sent, asked, closed, filed = chat
    assert await wa._answer_batch_project("digg-demo", "27827077080", "file these under Hillside")
    assert asked[-1] == ("new_project", "recent", "New project: Hillside")
    assert "no project called *Hillside*" in sent[-1]
    monkeypatch.setattr(wa, "_create_project", lambda t, name: name)
    q = {"id": "q1", "kind": "new_project", "ref_id": "recent", "prompt": "New project: Hillside"}
    assert await wa._try_open_question("digg-demo", "27827077080", "yes", q)
    assert "Added the project *Hillside*" in sent[-1] and filed[-1] == ("Hillside", None)


@pytest.mark.asyncio
async def test_a_project_that_exists_is_never_created_twice(monkeypatch):
    from vula.commerce import service
    monkeypatch.setattr(service, "registered_projects",
                        lambda t: [{"name": "Atlantis Paarden Eiland", "aliases": ["Atlantis Foods"]}])
    monkeypatch.setattr(service, "_client", lambda: (_ for _ in ()).throw(AssertionError("no insert")))
    assert wa._create_project("digg-demo", "atlantis foods") == "Atlantis Paarden Eiland"


@pytest.mark.asyncio
async def test_no_upload_means_no_new_project_offer(chat, monkeypatch):
    sent, asked, closed, filed = chat
    monkeypatch.setattr(wa, "_recent_upload", lambda t, p: {"docs": [], "sent": False})
    assert not await wa._answer_batch_project("digg-demo", "27645755210", "please log in to the dashboard")
    assert not asked and not sent


@pytest.mark.asyncio
async def test_a_customer_never_triggers_filing(chat, monkeypatch):
    sent, asked, closed, filed = chat
    monkeypatch.setattr(wa, "_is_team", lambda t, p: False)
    assert not await wa._answer_batch_project("off-the-hook", "27820001111", "put it on my account")
    assert not filed and not asked


@pytest.mark.asyncio
async def test_its_for_a_new_project_asks_the_name_then_offers_it(chat):
    sent, asked, closed, filed = chat
    assert await wa._answer_new_project_mention("digg-demo", "27645755210", "It's for a new project we started")
    assert asked[-1][0] == "new_project_name" and "What's the new project called?" in sent[-1]
    q = {"id": "q2", "kind": "new_project_name", "ref_id": "recent", "prompt": "New project name"}
    assert await wa._try_open_question("digg-demo", "27645755210", "Hillside", q)
    assert asked[-1] == ("new_project", "recent", "New project: Hillside")
    # ...and a name that already exists just files it there
    assert await wa._try_open_question("digg-demo", "27645755210", "Belladonna", q)
    assert filed[-1] == ("Belladonna", None)


@pytest.mark.asyncio
async def test_a_question_about_new_projects_is_left_to_the_assistant(chat):
    assert not await wa._answer_new_project_mention(
        "digg-demo", "27645755210", "How do I add a new project on the dashboard?")


# ── one list instead of many questions ───────────────────────────────────────

def test_list_numbers():
    assert wa._list_numbers("1, 3-5 and 7") == [1, 3, 4, 5, 7]
    assert wa._list_numbers("2") == [2]


@pytest.mark.asyncio
async def test_unanswered_project_questions_go_out_as_one_list(monkeypatch):
    stale = [{"id": f"q{i}", "kind": "doc_project", "ref_id": f"d{i}"} for i in (1, 2, 3)]
    docs = [{"id": "d1", "filename": "POS Account Sale 35-1937.pdf", "status": "pending_project",
             "fields": {"supplier": "Jack Hammer"}},
            {"id": "d2", "filename": "Sporty.com Fire Protection Rev 2.pdf", "status": "pending_project",
             "fields": {}},
            {"id": "d3", "filename": "00098116.pdf", "status": "filed", "fields": {}}]   # settled

    class Q:
        def __init__(self): self.f = {}
        def select(self, *a): return self
        def eq(self, *a): return self
        def in_(self, *a): return self
        def execute(self): return type("R", (), {"data": docs})()

    monkeypatch.setattr(oq, "_client", lambda: type("C", (), {"table": lambda self, n: Q()})())
    monkeypatch.setattr(oq, "_busy", lambda t, p: False)
    monkeypatch.setattr(oq, "_queued", lambda t, p: [])
    monkeypatch.setattr(oq, "open_for", lambda t, p, limit=5: stale)
    closed, asked, sent = [], [], []
    monkeypatch.setattr(oq, "close", lambda qid, answer="", status="answered": closed.append((qid, status)))
    monkeypatch.setattr(oq, "ask", lambda t, p, k, ref, prompt="": asked.append((k, ref)))
    monkeypatch.setattr("vula.api.whatsapp._send_reply",
                        AsyncMock(side_effect=lambda p, m, **k: sent.append(m) or True))
    monkeypatch.setattr("vula.integrations.doc_filing.project_examples", lambda t: ["Belladonna"])
    monkeypatch.setattr("vula.integrations.project_resolver.resolve",
                        lambda t, f, hint: {"project": "Sporty TV"} if "Sporty" in hint else {})

    assert await oq.release_next("digg-demo", "27827077080")
    assert len(sent) == 1
    msg = sent[0]
    assert "2 documents still need a project" in msg
    assert "1. POS Account Sale 35-1937.pdf (Jack Hammer)" in msg and "→ *Sporty TV*?" in msg
    assert asked == [("doc_project_list", "d1,d2")]
    assert sorted(closed) == [("q1", "listed"), ("q2", "listed"), ("q3", "closed")]


@pytest.mark.asyncio
async def test_list_answer_files_each_and_offers_unknown_projects(chat, monkeypatch):
    sent, asked, closed, filed = chat
    calls = []

    async def _resolve(t, p, text, doc_id=None):
        calls.append((doc_id, text))
        if text == "Hillside":
            return {"unmatched": True, "filename": doc_id}
        return {"filed": True, "project": text, "filename": doc_id}

    monkeypatch.setattr("vula.integrations.doc_filing.resolve_pending_document", _resolve)
    monkeypatch.setattr(wa, "_doc_still_pending", lambda t, d: d in ("d2", "d3"))
    q = {"id": "ql", "kind": "doc_project_list", "ref_id": "d1,d2,d3", "prompt": "Documents list"}
    assert await wa._try_open_question("digg-demo", "27827077080", "1 Belladonna\n2-3 Hillside", q)
    assert calls == [("d1", "Belladonna"), ("d2", "Hillside"), ("d3", "Hillside")]
    assert "Filed 'd1' under *Belladonna*" in sent[0]
    assert asked[-1] == ("new_project", "d2,d3", "New project: Hillside")


@pytest.mark.asyncio
async def test_an_ordinary_message_is_not_taken_as_a_list_answer(chat):
    q = {"id": "ql", "kind": "doc_project_list", "ref_id": "d1,d2", "prompt": "Documents list"}
    assert not await wa._try_open_question("digg-demo", "27827077080", "Please send me the HPC invoices", q)
