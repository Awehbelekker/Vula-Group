"""Files attached in the dashboard chat (vula/chat_attachments.py, 13 Nov 2026): read, not filed;
the person's own for 24 hours; given to the open chat fenced as their file; kept in the
business's documents only when they ask."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config import settings
from core.skills.base import SkillInput, SkillOutput
from tests.fake_supabase import FakeSupabase
from vula import chat_attachments as ca, identity, open_chat
from vula.api import commerce
from vula.commerce import service

T = "kelp-boardbags"
ME = "webadmin:m:m-own"
OWNER = identity.Person(T, member_id="m-own", name="Ian", role="owner")
SHEET = [(1, "Sheet: Prices\nProduct | Size | Price\nDay Bag | 9'6 | 2000\nDay Bag | 12' | 2400")]


@pytest.fixture(autouse=True)
def _uploads(tmp_path):
    open_chat.clear()
    with patch.object(settings, "upload_dir", tmp_path):
        yield tmp_path


def _parse(pages=SHEET):
    return patch("vula.ingestion.pipeline.DocumentParser.parse", AsyncMock(return_value=pages))


@pytest.mark.asyncio
async def test_a_file_is_read_and_kept_for_its_owner_only():
    with _parse():
        a = await ca.attach(T, ME, "prices.xlsx", b"xlsx-bytes")
    assert a["name"] == "prices.xlsx" and a["pages"] == 1 and a["truncated"] is False
    assert ca.get(T, ME, a["id"])["text"].startswith("Sheet: Prices")
    assert ca.get(T, "webadmin:m:someone-else", a["id"]) is None
    assert ca.get("other-business", ME, a["id"]) is None
    with pytest.raises(KeyError):
        ca.for_message(T, "webadmin:m:someone-else", [a["id"]])


@pytest.mark.asyncio
async def test_unreadable_oversize_and_odd_files_are_refused_with_the_reason():
    with pytest.raises(ca.AttachmentError, match="PDF, Word, Excel"):
        await ca.attach(T, ME, "tool.exe", b"x")
    with patch.object(settings, "max_file_mb", 1), pytest.raises(ca.AttachmentError, match="over 1 MB"):
        await ca.attach(T, ME, "big.pdf", b"x" * (1024 * 1024 + 1))
    with _parse(pages=[]), pytest.raises(ca.AttachmentError, match="couldn't read any text"):
        await ca.attach(T, ME, "blank.pdf", b"%PDF")


@pytest.mark.asyncio
async def test_after_a_day_a_file_is_gone_unless_it_was_kept(_uploads):
    with _parse():
        old = await ca.attach(T, ME, "old.csv", b"a,b")
        kept = await ca.attach(T, ME, "kept.csv", b"a,b")
    with patch("vula.commerce.profile_from_docs.file_into_kb", AsyncMock()):
        await ca.file_into(T, ME, kept["id"])
    later = time.time() + ca.TTL_S + 1
    assert ca.prune(T, now=later) == 2
    old_file = next(_uploads.rglob(f"{old['id']}-old.csv"), None)
    kept_file = next(_uploads.rglob(f"{kept['id']}-kept.csv"), None)
    assert old_file is None and kept_file is not None          # the kept one is in the documents


def test_the_prompt_block_is_fenced_and_says_when_it_was_cut():
    text = "row\n" * 5000
    block = ca.block([{"name": "big.csv", "text": text, "truncated": True}], budget=2000)
    assert "ATTACHED_FILE: big.csv" in block and "isn't shown" in block and len(block) < 2600


def test_history_keeps_the_names_never_the_text():
    assert ca.note("what's the 12' price?", [{"name": "prices.xlsx", "text": "secret rows"}]) == \
        "what's the 12' price?\n[Attached: prices.xlsx]"


@pytest.mark.asyncio
async def test_keep_in_our_documents_files_it_once():
    with _parse():
        a = await ca.attach(T, ME, "terms.pdf", b"%PDF")
    with patch("vula.commerce.profile_from_docs.file_into_kb", AsyncMock()) as filed:
        assert (await ca.file_into(T, ME, a["id"]))["filed"] is True
        await ca.file_into(T, ME, a["id"])
        assert await ca.file_into(T, "webadmin:m:other", a["id"]) is None
    filed.assert_awaited_once()
    assert filed.await_args.args[0] == T


# ── the chat route ──────────────────────────────────────────────────────────────────────────

def _req():
    r = MagicMock()
    r.headers = {"authorization": "Bearer tok"}
    return r


class _Skill:
    def __init__(self, name):
        self.name, self.seen = name, None

    async def __call__(self, inp):
        self.seen = inp
        return SkillOutput(answer=f"from {self.name}", skill_name=self.name)


async def _ask(message, att_ids, mode="open", db=None):
    db = db or FakeSupabase()
    skills = {"reasoning": _Skill("reasoning"), "commerce_admin": _Skill("commerce_admin")}
    with patch.object(service, "_client", return_value=db), \
         patch("vula.identity.person_for_login", AsyncMock(return_value=OWNER)), \
         patch("core.skills.loader.get_skill", side_effect=lambda n: skills[n]), \
         patch("vula.features.on", side_effect=lambda t, n: n == "open_chat"):
        out = await commerce.admin_assistant(T, commerce.AssistantRequest(
            message=message, mode=mode, attachments=att_ids), _req())
    return out, skills, db


@pytest.mark.asyncio
async def test_an_attached_sheet_reaches_the_open_chat_fenced_and_history_keeps_only_its_name():
    with _parse():
        a = await ca.attach(T, ME, "prices.xlsx", b"xlsx")
    out, skills, db = await _ask("What's the price of the 12' day bag?", [a["id"]])
    md = skills["reasoning"].seen.metadata
    assert out["answered_by"] == "open" and "ATTACHED_FILE: prices.xlsx" in md["attachments_block"]
    assert "Day Bag | 12' | 2400" in md["attachments_block"]
    saved = [m["content"] for m in db.rows["commerce_conversation_messages"] if m["role"] == "user"]
    assert saved == ["What's the price of the 12' day bag?\n[Attached: prices.xlsx]"]


@pytest.mark.asyncio
async def test_the_business_assistant_gets_a_shorter_excerpt_with_the_question():
    with _parse(pages=[(1, "row\n" * 20000)]):
        a = await ca.attach(T, ME, "stock.csv", b"csv")
    out, skills, _ = await _ask("Update our stock from this sheet", [a["id"]], mode="business")
    q = skills["commerce_admin"].seen.question
    assert out["answered_by"] == "business" and "ATTACHED_FILE: stock.csv" in q
    assert len(q) < ca.BUSINESS_CHARS + 1000


@pytest.mark.asyncio
async def test_someone_elses_or_an_expired_file_is_refused():
    with _parse():
        a = await ca.attach(T, "webadmin:m:someone-else", "theirs.pdf", b"%PDF")
    with pytest.raises(HTTPException) as e:
        await _ask("what's in it?", [a["id"]])
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_the_attach_route_reads_files_and_says_what_it_could_not():
    good = SimpleNamespace(filename="prices.xlsx", content_type="application/vnd.ms-excel", read=AsyncMock(return_value=b"x"))
    bad = SimpleNamespace(filename="tool.exe", content_type="application/octet-stream", read=AsyncMock(return_value=b"x"))
    with _parse(), patch("vula.identity.person_for_login", AsyncMock(return_value=OWNER)):
        out = await commerce.admin_assistant_attach(T, _req(), files=[good, bad])
    assert [a["name"] for a in out["attachments"]] == ["prices.xlsx"]
    assert "PDF, Word, Excel" in out["problems"][0]
    with patch("vula.identity.person_for_login", AsyncMock(return_value=None)), pytest.raises(HTTPException) as e:
        await commerce.admin_assistant_attach(T, _req(), files=[good])
    assert e.value.status_code == 401


# ── the reasoning skill answers from the file ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_with_a_file_attached_the_nothing_on_file_decline_does_not_fire():
    from core.skills.reasoning import ReasoningSkill
    seen = {}

    async def acompletion(**kw):
        seen.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="R2 400, from the sheet."))])
    block = ca.block([{"name": "invoice.pdf", "text": "Invoice INV-104 total R2 400"}], 4000)
    with patch("core.skills.reasoning.resolve_generation_route", AsyncMock(return_value=("openrouter/x", "k", "b"))), \
         patch("litellm.acompletion", acompletion), \
         patch("vula.ingestion.pipeline.VulaIngestionPipeline.query", AsyncMock(return_value=[])):
        out = await ReasoningSkill().run(SkillInput(
            question="What's the total on our invoice INV-104?", tenant_id=T,
            metadata={"open": True, "attachments_block": block}))
    assert out.answer == "R2 400, from the sheet."
    user = seen["messages"][1]["content"]
    assert user.index("ATTACHED_FILE: invoice.pdf") < user.index("Question:")
    assert "Answer from it first" in seen["messages"][0]["content"]
