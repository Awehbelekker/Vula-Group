"""KB retrieval C5 (2026-10-16, docs/kb-retrieval-audit.md): a customer's question must never be
grounded on a staff or owner document — supplier invoices with cost prices, bank statements,
wage rosters all live in the same tenant collection as the shop's FAQ."""
from unittest.mock import patch

import pytest

from evals.retrieval import offline
from vula.ingestion import pipeline as pl
from vula.ingestion.pipeline import VulaIngestionPipeline, audience_filter, default_audience

INVOICE = "Invoice from the fishermen co-op: 40 kg yellowtail at R 95 per kg. Settlement in 7 days."
FAQ = "Is your yellowtail fresh? Yes, our yellowtail is line caught and on the counter within a day."


def test_default_audience_is_conservative():
    assert default_audience("document") == "staff"
    assert default_audience(None) == "staff"
    assert default_audience("website") == "public"
    assert default_audience("starter") == "public"
    assert default_audience("learned_answer_approved") == "public"
    assert default_audience("document", "owner") == "owner"
    assert default_audience("document", "everyone") == "staff"   # unknown value → the default


def test_only_a_customer_is_filtered():
    assert audience_filter(None) == {} and audience_filter("staff") == {}
    assert audience_filter("public") == {
        "must_not": [{"key": "audience", "match": {"any": ["staff", "owner"]}}]}
    with patch.object(pl.settings, "kb_customer_audience_strict", True):
        assert audience_filter("public") == {"must": [{"key": "audience", "match": {"value": "public"}}]}


async def _seed(mem, tenant="shop"):
    p = VulaIngestionPipeline(tenant)
    await p.ingest_text(INVOICE, "Supplier invoice co-op.pdf")                  # staff by default
    await p.ingest_text(FAQ, "Fresh catch FAQ.md", source_type="website")       # public
    return p


@pytest.mark.asyncio
async def test_customer_query_never_gets_a_staff_document_by_vector_or_keyword():
    with offline() as mem:
        p = await _seed(mem)
        q = "yellowtail per kg price"           # terse → the keyword pass runs as well
        owner = {c["filename"] for c in await p.query(q, top_k=5)}
        customer = {c["filename"] for c in await p.query(q, top_k=5, audience="public")}
    assert "Supplier invoice co-op.pdf" in owner
    assert "Supplier invoice co-op.pdf" not in customer
    assert "Fresh catch FAQ.md" in customer


@pytest.mark.asyncio
async def test_untagged_legacy_points_stay_visible_until_strict_mode():
    with offline() as mem:
        p = await _seed(mem)
        col = mem.cols[p.store._collection_name("shop")]
        for _pid, (_v, payload) in col["points"].items():   # as if stored before this change
            payload.pop("audience", None)
        q = "yellowtail per kg price"
        lenient = {c["filename"] for c in await p.query(q, audience="public")}
        with patch.object(pl.settings, "kb_customer_audience_strict", True):
            strict = await p.query(q, audience="public")
    assert "Supplier invoice co-op.pdf" in lenient       # the known gap the backfill closes
    assert strict == []


@pytest.mark.asyncio
async def test_a_refused_audience_filter_fails_closed():
    """The old 400 fallback retried WITHOUT the filter — for a customer that would be the leak."""
    import httpx

    async def handler(req):
        if req.url.path.endswith("/points/search"):
            return httpx.Response(400, json={"status": {"error": "bad filter"}})
        return httpx.Response(200, json={"result": True})

    real = httpx.AsyncClient
    with patch("httpx.AsyncClient", lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)})):
        got = await pl.QdrantStore().search("shop", [0.1], audience="public")
    assert got == []


@pytest.mark.asyncio
async def test_the_customer_assistant_asks_as_public():
    from core.skills.base import SkillInput
    from core.skills.commerce_assistant import CommerceAssistantSkill
    with offline() as mem:
        await _seed(mem)
        block, sources = await CommerceAssistantSkill()._retrieve_kb(
            SkillInput(question="what do you pay for yellowtail per kg", tenant_id="shop"))
    assert "co-op" not in block and all("invoice" not in s["filename"].lower() for s in sources)


# ── 1b (2026-10-17): every customer- or public-facing path, not just commerce_assistant ─────────

from vula.ingestion.pipeline import kb_audience  # noqa: E402

CHATLOG = "Chat export: yellowtail per kg was R 80 last year, said the rep."


@pytest.mark.asyncio
async def test_keyword_pass_honours_authoritative_only():
    """An authoritative_only query could still return a chat log through the literal pass."""
    with offline() as mem:
        p = VulaIngestionPipeline("t-kw")
        await p.ingest_text(CHATLOG, "chat.txt", source_type="conversation")
        hits = await p.store.keyword_search("t-kw", ["yellowtail", "rep"], limit=5,
                                            exclude_source_types=["conversation", "learned"])
        assert hits == []
        assert await p.store.keyword_search("t-kw", ["yellowtail", "rep"], limit=5)
        got = await p.query("yellowtail rep", authoritative_only=True)
        assert all(c.get("source_type") != "conversation" for c in got)


@pytest.mark.asyncio
async def test_a_public_turn_forces_every_query_public():
    with offline() as mem:
        p = await _seed(mem)
        with kb_audience("public"):
            got = {c["filename"] for c in await p.query("yellowtail per kg price", top_k=5, audience="staff")}
        assert "Supplier invoice co-op.pdf" not in got
        with kb_audience(None):
            got = {c["filename"] for c in await p.query("yellowtail per kg price", top_k=5)}
        assert "Supplier invoice co-op.pdf" in got


def test_an_outsider_on_a_knowledge_line_is_public(monkeypatch):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(wa, "_is_tenant_owner", lambda t, ph: ph == "27820000001")
    assert wa._kb_audience_for("digg-demo", "27829999999", None) == "public"     # a client
    assert wa._kb_audience_for("digg-demo", "27829999999", "staff") is None       # team member
    assert wa._kb_audience_for("digg-demo", "27820000001", None) is None          # the owner
    assert wa._kb_audience_for("digg-demo", "", None) is None                     # dashboard chat
    with patch.object(pl.settings, "kb_outsiders_public_only", False):
        assert wa._kb_audience_for("digg-demo", "27829999999", None) is None


@pytest.mark.asyncio
async def test_rag_reply_runs_an_outsiders_turn_as_public(monkeypatch):
    from vula.api import whatsapp as wa
    from vula.ingestion.pipeline import _KB_AUDIENCE
    seen = []

    async def _inner(*a, **kw):
        seen.append(_KB_AUDIENCE.get())
        return "ok"

    monkeypatch.setattr(wa, "_rag_reply_inner", _inner)
    monkeypatch.setattr(wa, "_is_tenant_owner", lambda t, ph: False)
    await wa._rag_reply("digg-demo", "q", phone="27829999999")
    await wa._rag_reply("digg-demo", "q", phone="27829999999", caller_role="owner")
    assert seen == ["public", None]


@pytest.mark.asyncio
async def test_public_generation_paths_ask_as_public():
    """The public web page copy and a customer broadcast draft: both write text customers read."""
    seen = []

    async def _query(self, q, top_k=5, authoritative_only=False, category=None, expand=True, audience=None):
        seen.append(pl.effective_audience(audience))
        return []

    with patch.object(VulaIngestionPipeline, "query", _query):
        from vula.commerce import page_copy
        await page_copy._grounding("shop", "fresh fish shop")
        from vula.api import commerce as commerce_api
        try:
            await commerce_api.admin_draft_broadcast("shop", {"details": "yellowtail 180"})
        except Exception:
            pass                                           # no model here — the KB read is what counts
    assert seen == ["public", "public"], seen


# ── Dashboard: badges, the owner's switch, and the customer preview (2026-10-17) ────────────────

@pytest.mark.asyncio
async def test_owner_switch_is_verified_survives_reupload_and_shows_in_the_preview():
    from vula.api import server
    with offline():
        p = await _seed(None)
        docs = {d["filename"]: d for d in (await server.list_kb_documents("shop"))["documents"]}
        assert docs["Supplier invoice co-op.pdf"]["audience"] == "staff"
        assert docs["Fresh catch FAQ.md"]["audience"] == "public"

        menu = await p.ingest_text("Winter menu: yellowtail fillet, hake and chips.", "Winter menu.pdf")
        res = await server.set_kb_document_audience("shop", menu.doc_id, server.AudienceIn(audience="public"))
        assert res["verified"] and res["chunks"] == 1

        view = await server.customer_view_preview("shop", q="yellowtail")
        assert {d["filename"] for d in view["visible_documents"]} == {"Fresh catch FAQ.md", "Winter menu.pdf"}
        assert all(h["filename"] != "Supplier invoice co-op.pdf" for h in view["results"])

        # a re-upload / re-sync of the same file keeps the owner's choice
        await p.ingest_text("Winter menu v2: yellowtail, hake, calamari.", "Winter menu.pdf", doc_id=menu.doc_id)
        docs = {d["filename"]: d for d in (await server.list_kb_documents("shop"))["documents"]}
        assert docs["Winter menu.pdf"]["audience"] == "public" and docs["Winter menu.pdf"]["owner_set"]

        with pytest.raises(Exception):
            await server.set_kb_document_audience("shop", menu.doc_id, server.AudienceIn(audience="everyone"))
