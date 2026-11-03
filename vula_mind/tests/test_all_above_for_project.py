"""3 Nov 2026: "Above is all for <project>" after a batch of photos files every one of them.

The owner sent six photos (quotes, a slip, a labour sheet) and then "Above is all for Seaview"
(made-up project). Nothing understood it: the assistant answered it as a remark and every document
kept waiting for a project, with the draft bills booked from the quotes on no project. The labour
sheet's reply also showed 5 of its 15 rows and its totals without their labels.
Made-up projects, people and figures throughout.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from vula import open_questions as oq
from vula.api import whatsapp as wa
from vula.commerce import expenses

PROJECTS = ["Seaview", "Oak Street", "Main Road"]


@pytest.mark.parametrize("text,name", [
    ("Above is all for Seaview", "Seaview"),
    ("The above is for Oak Street.", "Oak Street"),
    ("All of the above for Seaview", "Seaview"),
    ("Everything above is for Main Road", "Main Road"),
    ("These are all for Seaview", "Seaview"),
    ("They’re all for the Seaview project", "Seaview project"),
    ("It's all for Seaview!", "Seaview"),
    ("Alles hierbo is vir Seaview", "Seaview"),
    ("Dit is alles vir Oak Street", "Oak Street"),
    ("Hierdie is almal vir Seaview", "Seaview"),
    ("ok, above all for Seaview", "Seaview"),
])
def test_all_above_is_understood(text, name):
    m = wa._ALL_ABOVE_RE.match(text)
    assert m and m.group("name") == name


@pytest.mark.parametrize("text", [
    "Above all, call the client back",
    "Is it all for Seaview?",
    "Everything is fine thanks",
    "What is all for sale this week",
    "Seaview",
])
def test_ordinary_messages_are_not_taken_as_filing(text):
    assert not wa._ALL_ABOVE_RE.match(text)


@pytest.fixture()
def chat(monkeypatch):
    sent, filed = [], []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append(m) or True))
    monkeypatch.setattr(wa, "_is_team", lambda t, p: True)
    monkeypatch.setattr(expenses, "known_projects", lambda t: list(PROJECTS))
    monkeypatch.setattr(wa, "_recent_upload", lambda t, p: {"docs": [], "sent": True})
    monkeypatch.setattr(oq, "open_for", lambda t, p, limit=5: [])

    async def _file(t, p, project, doc_ids=None):
        filed.append((project, doc_ids))
    monkeypatch.setattr(wa, "_file_recent_under", _file)
    return sent, filed


@pytest.mark.asyncio
async def test_above_is_all_for_files_what_was_just_sent(chat):
    sent, filed = chat
    assert await wa._answer_batch_project("thandi-demo", "27820000001", "Above is all for Seaview")
    assert filed == [("Seaview", None)]


@pytest.mark.asyncio
async def test_a_typo_in_the_project_still_files(chat):
    sent, filed = chat
    assert await wa._answer_batch_project("thandi-demo", "27820000001", "Alles hierbo is vir Seavew")
    assert filed == [("Seaview", None)]


@pytest.mark.asyncio
async def test_nothing_sent_means_it_is_just_conversation(chat, monkeypatch):
    sent, filed = chat
    monkeypatch.setattr(wa, "_recent_upload", lambda t, p: {"docs": [], "sent": False})
    monkeypatch.setattr(wa, "_sender_busy", lambda t, p: False)
    assert not await wa._answer_batch_project("thandi-demo", "27820000001", "It's all for Seaview")
    assert filed == []


@pytest.mark.asyncio
async def test_a_document_waiting_for_a_project_counts_as_sent(chat, monkeypatch):
    sent, filed = chat
    monkeypatch.setattr(wa, "_recent_upload", lambda t, p: {"docs": [], "sent": False})
    monkeypatch.setattr(wa, "_sender_busy", lambda t, p: False)
    monkeypatch.setattr(oq, "open_for", lambda t, p, limit=5: [{"kind": "doc_project", "ref_id": "d1"}])
    assert await wa._answer_batch_project("thandi-demo", "27820000001", "These are all for Oak Street")
    assert filed == [("Oak Street", None)]


@pytest.mark.asyncio
async def test_a_customer_never_files(chat, monkeypatch):
    sent, filed = chat
    monkeypatch.setattr(wa, "_is_team", lambda t, p: False)
    assert not await wa._answer_batch_project("thandi-demo", "27820009999", "Above is all for Seaview")
    assert filed == []


# ── filing moves what was booked from each document ──────────────────────────

class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a): return self
    def eq(self, *a): return self
    def in_(self, *a): return self
    def gte(self, *a): return self
    def update(self, patch): self.rows.append(("update", patch)); return self
    def execute(self): return MagicMock(data=[r for r in self.rows if isinstance(r, dict)])


@pytest.mark.asyncio
async def test_each_document_moves_with_its_bill_and_expense(monkeypatch):
    from vula.commerce import service
    from vula.integrations import doc_filing
    docs = [{"id": "d1", "filename": "quote.jpg", "project": None, "status": "pending_project"},
            {"id": "d2", "filename": "slip.jpg", "project": None, "status": "pending_project"},
            {"id": "d3", "filename": "done.jpg", "project": "Seaview", "status": "filed"}]
    tables = {"vula_filed_documents": list(docs), "commerce_expenses": []}
    db = MagicMock()
    db.table.side_effect = lambda n: _Q(tables.setdefault(n, []))
    monkeypatch.setattr(service, "_client", lambda: db)
    moved = []

    def move(tid, project, doc_id=None, invoice_id=None):
        moved.append((tid, project, doc_id))
        return {"ok": doc_id != "d2", "error": "no"}
    monkeypatch.setattr(doc_filing, "move_to_project", move)
    monkeypatch.setattr(oq, "close_for", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(wa, "_send_reply", AsyncMock(side_effect=lambda p, m, **k: sent.append(m) or True))
    await wa._file_recent_under("thandi-demo", "27820000001", "Seaview")
    assert moved == [("thandi-demo", "Seaview", "d1"), ("thandi-demo", "Seaview", "d2")]   # d3 already there
    # the move that didn't read back still files the document row itself
    assert ("update", {"project": "Seaview", "status": "filed"}) in tables["vula_filed_documents"]
    assert "Filed 3 documents under *Seaview*" in sent[-1]


# ── the reply shows every row, and labelled totals ───────────────────────────

def test_every_row_of_a_sheet_is_in_the_reply():
    workers = [{"name": f"Worker {i}", "days": i, "rate": 350, "total": 350 * i} for i in range(1, 16)]
    out = wa._format_extraction({"fields": {"workers": workers,
                                            "totals": {"Day rates": 26600, "Half fortnight": 15050}}})
    assert all(f"Worker {i}," in out for i in range(1, 16))
    assert "more" not in out
    assert "• Totals: Day rates 26600; Half fortnight 15050" in out


def test_a_very_long_list_says_how_many_are_left_out():
    rows = [{"name": f"Row {i}"} for i in range(1, 26)]
    out = wa._format_extraction({"fields": {"rows": rows}})
    assert "Row 20" in out and "Row 21" not in out
    assert "…and 5 more (all 25 are in the filed document)" in out
