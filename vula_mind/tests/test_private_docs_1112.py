"""One person's private documents (12 Nov 2026, Ian): "Private account is kept for Richard only, if he
wants to ask questions about it; this is his data and only visible to him." Richard's own FNB statements
had been filed as DIGG documents, so the owner saw them and Vula could quote them to anyone at DIGG.
Migration 258. Figures and ids made up."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_job_costing import FakeDB
from vula import private_docs
from vula.commerce import bank_accounts, bank_rec, service
from vula.ingestion import pipeline
from vula.integrations import doc_filing

TID = "digg-demo"
RICHARD, JUDY = "m-richard", "m-judy"
PRIVATE, MAIN = "63160575256", "63214254607"


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB({"commerce_bank_accounts": [
                       {"id": "a1", "tenant_id": TID, "account_number": MAIN, "project": None, "private_to": None},
                       {"id": "a9", "tenant_id": TID, "account_number": PRIVATE, "project": None, "private_to": RICHARD}],
                   "vula_filed_documents": [], "vula_private_documents": [], "vula_private_moves": [],
                   "vula_document_extractions": [], "commerce_bank_transactions": []})
    monkeypatch.setattr(service, "_client", lambda: fake)
    monkeypatch.setattr(bank_accounts, "available", lambda: True)
    return fake


# ── the knowledge base: private by default ───────────────────────────────────────────────────

def test_every_search_leaves_out_other_peoples_private_chunks():
    assert pipeline.audience_filter(None)["must"] == [{"is_empty": {"key": "private_to"}}]
    with pipeline.kb_viewer(RICHARD):
        assert pipeline.audience_filter(None)["must"] == [{"should": [
            {"is_empty": {"key": "private_to"}}, {"key": "private_to", "match": {"value": RICHARD}}]}]
        cust = pipeline.audience_filter("public")              # a customer never gets private chunks
        assert {"key": "private_to", "match": {"value": RICHARD}} in cust["must"][0]["should"]
    assert pipeline.audience_filter(None)["must"] == [{"is_empty": {"key": "private_to"}}]   # reset after


def test_word_matches_and_the_document_list_obey_the_same_rule():
    mine, shared = {"private_to": RICHARD, "text": "x"}, {"text": "y"}
    assert pipeline._audience_allows(None, shared) is True
    assert pipeline._audience_allows(None, mine) is False                 # Judy, or anyone unnamed
    with pipeline.kb_viewer(JUDY):
        assert pipeline._audience_allows(None, mine) is False
    with pipeline.kb_viewer(RICHARD):
        assert pipeline._audience_allows(None, mine) is True


def test_a_refused_filter_never_turns_into_an_unfiltered_search():
    import inspect
    src = inspect.getsource(pipeline.QdrantStore.search)
    assert 'for field_name in ("audience", "private_to")' in src
    assert 'body["filter"] = {k: v for k, v in aud.items() if v}' in src


# ── which documents are private ──────────────────────────────────────────────────────────────

def test_a_document_naming_a_private_account_is_that_members(db):
    assert private_docs.owner_of(TID, "Account statement for account 6316 057 5256 from FNB") == RICHARD
    assert private_docs.owner_of(TID, '{"account_number": "63160575256"}') == RICHARD
    assert private_docs.owner_of(TID, "Platinum Business Account 63214254607") is None
    assert private_docs.owner_of(TID, "nothing") is None


@pytest.mark.asyncio
async def test_a_private_statement_never_enters_the_business_books(db, monkeypatch):
    monkeypatch.setattr("vula.ingestion.fnb_statement.pdf_text", lambda p, pw: "Account Number: 63160575256")
    monkeypatch.setattr("vula.ingestion.fnb_statement.parse", lambda t: {"transactions": [
        {"date": "2026-10-01", "amount_cents": 100, "direction": "out", "description": "x"}]})
    monkeypatch.setattr(bank_rec, "get_statement_password", lambda t: None)
    reconcile = AsyncMock()
    monkeypatch.setattr(bank_rec, "reconcile", reconcile)
    rec = await bank_rec.ingest_statement(TID, "stmt.pdf")
    assert rec == {"private": True, "private_to": RICHARD, "bank_account": PRIVATE, "parsed": 1}
    reconcile.assert_not_awaited()
    assert db.tables["commerce_bank_transactions"] == []


@pytest.mark.asyncio
async def test_filing_one_goes_to_that_members_private_documents(db, monkeypatch):
    hide = AsyncMock(return_value=True)
    monkeypatch.setattr(pipeline.VulaIngestionPipeline, "__init__", lambda self, tenant_id=None: None)
    monkeypatch.setattr(pipeline.VulaIngestionPipeline, "store", SimpleNamespace(set_private=hide), raising=False)
    db.tables["vula_document_extractions"] = [{"tenant_id": TID, "doc_id": "d5256", "summary": "statement"}]
    row = await doc_filing.file_document(
        TID, filename="Account Statement - First National Bank 20261111-1236.pdf", data=b"%PDF",
        content_type="application/pdf", category="Account Statement",
        summary="An account statement from First National Bank for account 63160575256.",
        doc_id="d5256", source="whatsapp", project="Porterfield")
    assert row["status"] == "private" and row["member_id"] == RICHARD
    assert db.tables["vula_filed_documents"] == []                       # not the business's document
    assert [d["filename"] for d in db.tables["vula_private_documents"]] == [
        "Account Statement - First National Bank 20261111-1236.pdf"]
    hide.assert_awaited_once_with(TID, "d5256", RICHARD)
    assert db.tables["vula_document_extractions"] == []                  # its summary isn't left behind


@pytest.mark.asyncio
async def test_the_13_already_filed_are_moved_chunks_first(db, monkeypatch):
    db.tables["vula_filed_documents"] = [
        {"id": "f1", "tenant_id": TID, "filename": "Account Statement - First National Bank 1.pdf", "doc_id": "d1"},
        {"id": "f2", "tenant_id": TID, "filename": "Account Statement - First National Bank 2.pdf", "doc_id": "d2"}]
    db.tables["vula_private_moves"] = [
        {"id": "q1", "tenant_id": TID, "filed_document_id": "f1", "member_id": RICHARD, "done_at": None},
        {"id": "q2", "tenant_id": TID, "filed_document_id": "f2", "member_id": RICHARD, "done_at": None}]
    results = {"d1": True, "d2": False}                                  # d2's chunks couldn't be hidden yet
    monkeypatch.setattr(pipeline.VulaIngestionPipeline, "__init__", lambda self, tenant_id=None: None)
    monkeypatch.setattr(pipeline.VulaIngestionPipeline, "store",
                        SimpleNamespace(set_private=AsyncMock(side_effect=lambda t, d, m: results[d])), raising=False)
    monkeypatch.setattr(private_docs, "_client", lambda: db)
    assert await private_docs.process_moves() == 1
    assert [d["id"] for d in db.tables["vula_filed_documents"]] == ["f2"]   # kept until its chunks are private
    assert [d["doc_id"] for d in db.tables["vula_private_documents"]] == ["d1"]
    moves = {m["id"]: (bool(m["done_at"]), m.get("error")) for m in db.tables["vula_private_moves"]}
    assert moves == {"q1": (True, None), "q2": (False, "knowledge chunks not made private yet")}


def test_a_members_list_is_theirs_alone(db):
    db.tables["vula_private_documents"] = [{"tenant_id": TID, "member_id": RICHARD, "filename": "a.pdf"},
                                           {"tenant_id": TID, "member_id": JUDY, "filename": "b.pdf"}]
    with patch.object(private_docs, "_client", return_value=db):
        assert [d["filename"] for d in private_docs.list_for(TID, RICHARD)] == ["a.pdf"]
        assert private_docs.list_for(TID, "") == []


def test_a_private_account_is_not_listed_or_changeable_by_the_business(db):
    db.tables["commerce_bank_accounts"][0]["last_seen"] = "2026-10-31"
    db.tables["commerce_bank_accounts"][1]["last_seen"] = "2026-10-30"
    assert [a["account"] for a in bank_accounts.list_accounts(TID)] == ["…4607"]
    assert bank_accounts.set_project(TID, "a9", "Porterfield") == {"error": "account not found"}


# ── answering: who is asking ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_richard_on_whatsapp_is_answered_with_his_private_documents_and_judy_is_not(monkeypatch):
    from vula.api import whatsapp
    seen = []

    async def inner(*a, **k):
        seen.append(pipeline._KB_VIEWER.get())
        return "ok"
    monkeypatch.setattr(whatsapp, "_rag_reply_inner", inner)
    monkeypatch.setattr(private_docs, "member_id_for_phone",
                        lambda t, p: {"27820005210": RICHARD, "27820007080": JUDY}.get(p))
    monkeypatch.setattr(whatsapp, "_kb_audience_for", lambda *a: None)
    await whatsapp._rag_reply(TID, "what did I spend in October?", phone="27820005210")
    await whatsapp._rag_reply(TID, "what did Richard spend?", phone="27820007080")
    with pipeline.kb_viewer(RICHARD):                       # the staff app's chat says who, with no phone
        await whatsapp._rag_reply(TID, "my statements", phone="")
    assert seen == [RICHARD, JUDY, RICHARD]


def test_the_admin_agent_and_staff_chat_say_who_is_asking():
    import inspect
    from vula.api import chat, whatsapp
    assert "kb_viewer(member_id_for_phone(tenant_id, phone))" in inspect.getsource(whatsapp._run_commerce_admin)
    assert "kb_viewer(member_id_for_phone(tenant_id, phone))" in inspect.getsource(chat.send_message)
