"""17 Nov 2026 audit of #314–#339, privacy: research written with a person's own thread or private
notes stays theirs, and a colleague never sees the question as it was asked; what leaves Vula in a
web search has no emails, numbers, amounts or titled names; chat attachments are swept daily and
deleted with the person's data; an oversized attachment isn't read whole; expired research leaves
the index; the cloud escalation is logged with the model that read the document."""
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from core import llm_router
from core.skills import web_search
from tests.fake_supabase import FakeSupabase
from vula import chat_attachments, personal_memory, privacy_data, research, research_memory as rm, retention
from vula.commerce import service

T = "digg-demo"
JUDY, SAM = "m:m1", "m:m2"
PAGES = [{"title": "Pool Co", "url": "https://pool.example/prices", "text": "A 6 m pool costs R 300 000. " * 30}]


@pytest.fixture()
def db():
    d = FakeSupabase({"vula_research_notes": []})
    with patch.object(service, "_client", return_value=d), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.ingest_text", AsyncMock()), \
         patch.object(rm, "_candidates", AsyncMock(side_effect=RuntimeError("no index here"))):
        yield d


async def _research(history="", notes=""):
    with patch.object(research, "plan_queries", AsyncMock(return_value=["pool prices"])), \
         patch.object(research, "_search", AsyncMock(return_value=[])), \
         patch.object(research, "read_pages", AsyncMock(return_value=PAGES)), \
         patch.object(research, "own_documents", AsyncMock(return_value=[])), \
         patch.object(research, "_complete", AsyncMock(return_value="A 6 m pool is about R 300 000 [1].")), \
         personal_memory.use(notes):
        return await research.run(T, "What does a 6 m pool cost?", history=history, person_key=JUDY)


@pytest.mark.asyncio
async def test_research_with_no_private_context_is_shared(db):
    out = await _research()
    assert out["shared"] is True and db.rows["vula_research_notes"][0]["shared"] is True


@pytest.mark.asyncio
async def test_research_written_from_the_persons_own_thread_stays_theirs(db):
    out = await _research(history="Judy: Mrs Naidoo at 14 Oak Rd wants a pool, budget R300k")
    assert out["note_id"] and out["shared"] is False
    assert db.rows["vula_research_notes"][0]["shared"] is False


@pytest.mark.asyncio
async def test_research_written_with_private_notes_stays_theirs(db):
    out = await _research(notes="PRIVATE NOTES — I'm moving house in March")
    assert out["shared"] is False


def test_a_colleague_never_sees_the_question_as_it_was_asked():
    row = {"created_by": JUDY, "question": "Pool for Mrs Naidoo?", "answer": "About R 300 000 [1].",
           "sources": [{"url": "https://pool.example/x"}], "created_at": "2026-11-17T08:00:00+00:00"}
    assert "Naidoo" not in rm.block(row, SAM) and "About R 300 000" in rm.block(row, SAM)
    assert "Naidoo" in rm.block(row, JUDY)


# ── web search ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asked,sent", [
    ("Mrs Naidoo pool cost R300k", "pool cost"),
    ("call 082 555 1234 or jo@client.co.za about tiles", "call or about tiles"),
    ("budget R 23 500.00 for Mr John Smith tiling", "budget for tiling"),
    ("PPC cement 50kg price South Africa", "PPC cement 50kg price South Africa"),
])
def test_what_leaves_in_a_search_is_scrubbed(asked, sent):
    assert web_search.scrub_query(asked) == sent


@pytest.mark.asyncio
async def test_every_search_is_scrubbed_before_it_is_sent(monkeypatch):
    sent = []

    async def serper(q, limit=5):
        sent.append(q)
        return [{"title": "x", "url": "https://x.example"}]
    monkeypatch.setattr(settings, "serper_api_key", "test-key")
    monkeypatch.setattr(web_search, "_serper_search", serper)
    await web_search._ddg_search("pool for Mrs Naidoo, 082 555 1234, R300k")
    assert len(sent) == 1 and not any(w in sent[0] for w in ("Naidoo", "082", "R300"))


def test_the_research_fallback_query_is_scrubbed_too():
    assert research._parse_queries("no list", "Pool for Mrs Naidoo R300k?") == ["Pool for ?"]


# ── chat attachments ─────────────────────────────────────────────────────────

def _put(tmp_path, tenant, session_key, age_s, filed=False):
    d = tmp_path / tenant / "chat"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{len(list(d.iterdir()))}-x.pdf"
    f.write_bytes(b"pdf")
    meta = d / f"{f.stem}.json"
    meta.write_text(json.dumps({"session_key": session_key, "path": str(f), "created_at": time.time() - age_s,
                                "filed": filed}))
    return f, meta


def test_attachments_past_their_day_are_swept_for_every_business(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    old_a, meta_a = _put(tmp_path, "biz-a", "webadmin:m:1", 2 * 86400)
    new_b, meta_b = _put(tmp_path, "biz-b", "webadmin:m:2", 60)
    assert "chat_attachments" in [r.name for r in retention.RULES]
    assert chat_attachments.prune_all(1) == {"attachments": 1}
    assert not old_a.exists() and not meta_a.exists() and new_b.exists() and meta_b.exists()


def test_deleting_a_members_data_removes_their_attachments_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    mine, _ = _put(tmp_path, T, "webadmin:m:m1", 60)
    theirs, _ = _put(tmp_path, T, "webadmin:m:m2", 60)
    monkeypatch.setattr(privacy_data, "_member_thread", lambda t, v: ["webadmin:m:m1"])
    assert privacy_data._erase_chat_attachments(T, ["27821112222"]) == 1
    assert not mine.exists() and theirs.exists()
    assert ("chat_attachments", privacy_data._erase_chat_attachments) in privacy_data.ERASE_STEPS


class _Upload:
    def __init__(self, data, size=None):
        self.data, self.size, self.filename, self.asked = data, size, "big.pdf", []

    async def read(self, n=-1):
        self.asked.append(n)
        return self.data if n < 0 else self.data[:n]


@pytest.mark.asyncio
async def test_an_oversized_attachment_is_refused_without_reading_it_whole():
    up = _Upload(b"x" * 50, size=50)
    with pytest.raises(chat_attachments.AttachmentError):
        await chat_attachments.read_capped(up, 10)
    assert up.asked == []                                  # the declared size was enough
    up = _Upload(b"x" * 50)
    with pytest.raises(chat_attachments.AttachmentError):
        await chat_attachments.read_capped(up, 10)
    assert up.asked == [11]                                # never more than one past the limit
    assert await chat_attachments.read_capped(_Upload(b"ok"), 10) == b"ok"


# ── research index and the cloud log ─────────────────────────────────────────

def test_expired_research_leaves_the_index_too(db, monkeypatch):
    db.rows["vula_research_notes"].append({"id": "n1", "tenant_id": T, "expires_at": "2020-01-01T00:00:00+00:00"})
    dropped = []
    monkeypatch.setattr(rm, "_drop_index", lambda t, rows: dropped.extend(r["id"] for r in rows))
    rm.prune(T)
    assert dropped == ["n1"] and db.rows["vula_research_notes"] == []


def test_the_cloud_escalation_is_logged_with_the_model_that_read_it(monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "openrouter_api_key", "k")
    monkeypatch.setattr(llm_router, "_log_decision", lambda **kw: seen.append(kw))
    route = llm_router.resolve_cloud_route("document_analysis")
    assert route and seen and seen[0]["backend"] == route[0] and seen[0]["task"] == "document_analysis"
    assert seen[0]["outcome"] == "cloud" and seen[0]["reason"] == "cheap_tier_escalation"
