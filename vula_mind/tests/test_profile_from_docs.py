"""Setting a business up from its own documents (vula/commerce/profile_from_docs.py): each upload
is filed, then read for the profile answers still open. An answer is only ever a DRAFT, kept only
when the document really says it, and the owner keeps or dismisses it in the dashboard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from tests.fake_supabase import FakeSupabase
from vula.commerce import business_profile as bp
from vula.commerce import profile_from_docs as pfd
from vula.commerce import service

T = "deli"
TERMS = ("Harbour & Vine Deli — Terms of trade.\n"
         "We are open Monday to Friday from 7am to 5pm and closed on public holidays.\n"
         "We deliver within Cape Town CBD and the Atlantic Seaboard.\n"
         "Payment is by EFT or card, due within 7 days of invoice.\n")


def _model_says(answers):
    async def fake(**_kw):
        import json
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"answers": answers})))])
    return fake


@pytest.fixture()
def db():
    d = FakeSupabase({"vula_business_profile": []})
    d.primary_keys["vula_business_profile"] = ["tenant_id"]
    with patch.object(service, "_client", return_value=d), patch.object(bp, "_business_type", return_value="food"), \
         patch("core.llm_router.resolve_cheap_route", AsyncMock(return_value=("m", None, None))):
        yield d


def _drafts(d):
    rows = d.rows["vula_business_profile"]
    return (rows[0].get("drafts") if rows else {}) or {}


# ── drafting ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answers_the_document_really_states_become_drafts_pointing_at_the_file(db):
    said = {
        "hours": {"answer": "Mon–Fri 7am to 5pm, closed public holidays.",
                  "quote": "We are open Monday to Friday from 7am to 5pm and closed on public holidays."},
        "area": {"answer": "Cape Town CBD and the Atlantic Seaboard.",
                 "quote": "We deliver within Cape Town CBD and the Atlantic Seaboard."},
    }
    with patch("litellm.acompletion", new=_model_says(said)):
        n = await pfd.draft_from_text(T, "Terms of trade.pdf", TERMS)
    assert n == 2
    d = _drafts(db)
    assert d["hours"]["answer"].startswith("Mon–Fri") and d["hours"]["url"] == "document: Terms of trade.pdf"
    assert bp.get_answers(T) == {}                       # a draft is never an answer
    st = bp.status(T)
    assert st["drafts"]["hours"]["source"] == {"kind": "document", "label": "Terms of trade.pdf"}
    assert "hours" in st["missing"]


@pytest.mark.asyncio
async def test_an_answer_the_document_does_not_say_is_dropped(db):
    said = {
        "delivery_fee": {"answer": "Delivery is R50, minimum order R200.", "quote": "Delivery costs R50 with a R200 minimum."},
        "cancellations": {"answer": "Full refund within 7 days.", "quote": "short"},
        "not_a_question": {"answer": "x", "quote": "We deliver within Cape Town CBD and the Atlantic Seaboard."},
        "payment": {"answer": "EFT or card, 7 days.", "quote": "Payment is by EFT or card, due within 7 days of invoice."},
    }
    with patch("litellm.acompletion", new=_model_says(said)):
        assert await pfd.draft_from_text(T, "Terms.pdf", TERMS) == 1
    assert set(_drafts(db)) == {"payment"}                # the invented fee and policy have no sentence behind them


@pytest.mark.asyncio
async def test_the_model_is_told_it_is_reading_documents_and_given_only_that_text(db):
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answers": {}}'))])

    with patch("litellm.acompletion", new=fake):
        await pfd.draft_from_text(T, "Terms.pdf", TERMS)
    system, user = seen["messages"][0]["content"], seen["messages"][1]["content"]
    assert "company documents" in system and "website" not in system
    assert "DOCUMENT TEXT" in user and "Page: document: Terms.pdf" in user and "open Monday to Friday" in user


@pytest.mark.asyncio
async def test_a_question_already_drafted_or_answered_is_left_alone(db):
    db.rows["vula_business_profile"] = [{"tenant_id": T, "answers": {"area": "Citywide"},
                                         "drafts": {"hours": {"answer": "From the website: 8 to 4", "quote": "q" * 20,
                                                              "url": "https://deli.example/contact"}}}]
    said = {"hours": {"answer": "Mon–Fri 7am to 5pm.",
                      "quote": "We are open Monday to Friday from 7am to 5pm and closed on public holidays."},
            "area": {"answer": "CBD.", "quote": "We deliver within Cape Town CBD and the Atlantic Seaboard."},
            "payment": {"answer": "EFT or card.", "quote": "Payment is by EFT or card, due within 7 days of invoice."}}
    with patch("litellm.acompletion", new=_model_says(said)):
        assert await pfd.draft_from_text(T, "Terms.pdf", TERMS) == 1
    d = _drafts(db)
    assert d["hours"]["answer"] == "From the website: 8 to 4"      # the first source keeps the question
    assert "area" not in d and d["payment"]["url"] == "document: Terms.pdf"


def test_a_long_document_is_read_in_pieces_that_all_point_at_the_same_file():
    pages = pfd.as_pages("Company profile.pdf", "x" * 20000)
    assert len(pages) == 4 and all(p[0] == "document: Company profile.pdf" for p in pages)
    assert pfd.as_pages("Empty.pdf", "   ") == []


# ── the run: file each document, then read it ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_document_is_filed_then_read_and_one_failure_does_not_stop_the_rest(db, tmp_path):
    files = []
    for name in ("Terms.pdf", "Broken.pdf", "Scan.png"):
        (tmp_path / name).write_bytes(b"x")
        files.append({"name": name, "path": str(tmp_path / name), "mime": "application/pdf"})
    filed = []

    async def file_it(tid, path, mime):
        filed.append(path.name)

    async def read(tid, path):
        if path.name == "Broken.pdf":
            raise RuntimeError("corrupt")
        return TERMS if path.name == "Terms.pdf" else ""

    said = {"hours": {"answer": "Mon–Fri 7 to 5.",
                      "quote": "We are open Monday to Friday from 7am to 5pm and closed on public holidays."}}
    saved = []
    with patch.object(pfd, "read_text", read), patch.object(pfd, "_save", lambda t, st: saved.append(dict(st))), \
         patch("litellm.acompletion", new=_model_says(said)):
        st = await pfd.run(T, files, file_it=file_it)
    assert filed == ["Terms.pdf", "Broken.pdf", "Scan.png"]                     # all three go into the knowledge base
    assert [(f["name"], f["status"], f["drafts"]) for f in st["files"]] == [
        ("Terms.pdf", "done", 1), ("Broken.pdf", "failed", 0), ("Scan.png", "no_text", 0)]
    assert st["running"] is False and st["step"] == "done" and st["drafts"] == 1
    assert saved[0]["running"] is True and saved[-1]["running"] is False       # progress was reported as it went


def test_what_can_be_uploaded():
    assert pfd.reject_reason(["Terms.pdf", "Prices.xlsx", "menu.JPG"]) is None
    assert "at least one" in pfd.reject_reason([])
    assert "up to 10" in pfd.reject_reason([f"{i}.pdf" for i in range(11)])
    assert "virus.exe" in pfd.reject_reason(["Terms.pdf", "virus.exe"])


# ── the owner keeps or dismisses what was found, in the dashboard ───────────────────────────

@pytest.fixture()
def with_drafts(db):
    db.rows["vula_business_profile"] = [{"tenant_id": T, "answers": {}, "drafts": {
        "hours": {"answer": "Mon–Fri 7am to 5pm.", "quote": "q" * 20, "url": "document: Terms.pdf"},
        "area": {"answer": "Cape Town CBD.", "quote": "q" * 20, "url": "https://deli.example/delivery"},
        "payment": {"answer": "EFT or card.", "quote": "q" * 20, "url": "document: Terms.pdf"}}}]
    with patch.object(bp, "_write_documents", AsyncMock(return_value=True)) as written:
        yield db, written


@pytest.mark.asyncio
async def test_keeping_a_draft_makes_it_the_answer_and_dismissing_one_deletes_it(with_drafts):
    from vula.api import business_profile as api
    db, written = with_drafts
    out = await api.review_drafts(T, {"accept": ["hours"], "dismiss": ["payment"]})
    assert out["accepted"] == ["hours"] and out["dismissed"] == 1
    assert out["answers"]["hours"] == "Mon–Fri 7am to 5pm."
    assert set(out["drafts"]) == {"area"} and out["drafts"]["area"]["source"]["kind"] == "website"
    written.assert_awaited()                                   # the profile documents Vula answers from are rewritten
    assert "payment" not in _drafts(db) and bp.get_answers(T) == {"hours": "Mon–Fri 7am to 5pm."}


@pytest.mark.asyncio
async def test_a_key_with_no_draft_is_ignored_and_an_empty_request_is_refused(with_drafts):
    from vula.api import business_profile as api
    out = await api.review_drafts(T, {"accept": ["lead_times"]})
    assert out["accepted"] == [] and out["answers"] == {}
    for bad in ({}, {"accept": "hours"}, {"accept": [], "dismiss": []}):
        with pytest.raises(HTTPException) as e:
            await api.review_drafts(T, bad)
        assert e.value.status_code == 400


def test_the_whatsapp_interview_names_the_document_a_draft_came_from(with_drafts):
    st = bp.status(T)
    q = next(q for q in st["questions"] if q["key"] == "hours")
    assert "Your document “Terms.pdf” says: “Mon–Fri 7am to 5pm.”" in bp._ask(q, st)
    q = next(q for q in st["questions"] if q["key"] == "area")
    assert "Your website says: “Cape Town CBD.”" in bp._ask(q, st)


# ── the upload endpoint ─────────────────────────────────────────────────────────────────────

class _Upload:
    def __init__(self, name, content=b"%PDF-1.4 text", content_type="application/pdf"):
        self.filename, self._content, self.content_type = name, content, content_type

    async def read(self):
        return self._content


@pytest.fixture()
def upload_env(tmp_path):
    from vula.api import server as srv
    started = []

    def fake_bg(tenant_id, label, coro):
        started.append((tenant_id, label))
        coro.close()                                   # not run here; pfd.run is tested above

    with patch.object(srv, "UPLOAD_DIR", tmp_path), patch.object(srv, "validate_tenant", lambda t: t), \
         patch("vula.commerce.background_tasks.run_background", fake_bg), \
         patch.object(pfd, "already_running", return_value=False):
        yield tmp_path, started


@pytest.mark.asyncio
async def test_uploaded_documents_are_saved_for_this_business_and_queued(upload_env):
    from vula.api import business_profile as api
    tmp_path, started = upload_env
    out = await api.setup_from_documents(T, [_Upload("Company profile.pdf"), _Upload("Empty.pdf", b"")])
    assert out["status"] == "queued" and out["files"] == ["Company profile.pdf"]
    assert out["skipped"] == [{"name": "Empty.pdf", "reason": "empty file"}]
    assert "Reading 1 document." in out["message"]
    assert [p.name for p in (tmp_path / T).iterdir()] == ["Company profile.pdf"]
    assert started == [(T, "setup_from_documents")]


@pytest.mark.asyncio
async def test_bad_uploads_are_refused_before_anything_is_saved(upload_env):
    from vula.api import business_profile as api
    tmp_path, started = upload_env
    for files, code in (([_Upload("run.exe")], 400), ([_Upload(f"{i}.pdf") for i in range(11)], 400),
                        ([_Upload("Empty.pdf", b"")], 400)):
        with pytest.raises(HTTPException) as e:
            await api.setup_from_documents(T, files)
        assert e.value.status_code == code
    with patch.object(pfd, "already_running", return_value=True), pytest.raises(HTTPException) as e:
        await api.setup_from_documents(T, [_Upload("Terms.pdf")])
    assert e.value.status_code == 409
    assert started == [] and not any((tmp_path / T).glob("*.pdf")) if (tmp_path / T).exists() else started == []
