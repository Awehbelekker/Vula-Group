"""Privacy fixes from the 13 Nov 2026 audit: cloud calls.

* Zero data retention reached 9 of 93 cloud call sites (only those that splatted
  cloud_generation_kwargs). The litellm wrapper now adds it to every OpenRouter call, and the
  cloud embeddings request carries it too.
* The cheap tier (every document analysis) and cloud vision went to the cloud without a routing
  decision, so "why did tenant data leave South Africa" missed them. They're logged now.
"""
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

from core import llm_router
from core.llm_router import install_ollama_auth


@pytest.fixture()
def _fake_litellm(monkeypatch):
    fake = types.ModuleType("litellm")
    orig = AsyncMock(return_value="ok")
    fake.acompletion = orig
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_router, "_ollama_auth_installed", False)
    monkeypatch.setattr(llm_router.settings, "openrouter_zdr", True)
    monkeypatch.setattr(llm_router, "_with_cloud_fallback", lambda orig_fn, kw: orig_fn(**kw))
    install_ollama_auth()
    return orig


@pytest.mark.asyncio
async def test_every_openrouter_call_asks_for_zero_retention(_fake_litellm):
    import litellm
    await litellm.acompletion(model="openrouter/google/gemini-2.5-flash", messages=[])
    assert _fake_litellm.call_args.kwargs["extra_body"]["provider"] == {"data_collection": "deny", "zdr": True}


@pytest.mark.asyncio
async def test_a_callers_own_provider_choices_are_kept(_fake_litellm):
    import litellm
    await litellm.acompletion(model="openrouter/meta-llama/llama-3.3-70b-instruct", messages=[],
                              extra_body={"provider": {"order": ["groq"]}, "transforms": ["x"]})
    eb = _fake_litellm.call_args.kwargs["extra_body"]
    assert eb["provider"] == {"order": ["groq"], "data_collection": "deny", "zdr": True}
    assert eb["transforms"] == ["x"]


@pytest.mark.asyncio
async def test_local_calls_get_no_cloud_routing(_fake_litellm):
    import litellm
    await litellm.acompletion(model="ollama_chat/llama3.1:8b", api_base="http://localhost:11434", messages=[])
    assert "extra_body" not in _fake_litellm.call_args.kwargs


@pytest.mark.asyncio
async def test_switching_zdr_off_is_respected(_fake_litellm, monkeypatch):
    import litellm
    monkeypatch.setattr(llm_router.settings, "openrouter_zdr", False)
    await litellm.acompletion(model="openrouter/google/gemini-2.5-flash", messages=[])
    assert "extra_body" not in _fake_litellm.call_args.kwargs


@pytest.mark.asyncio
async def test_the_cheap_tier_and_cloud_vision_are_logged(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_router, "_log_decision", lambda **k: seen.append(k))
    monkeypatch.setattr(llm_router.settings, "openrouter_api_key", "k")
    route = await llm_router.resolve_cheap_route(task_type="document_analysis")
    assert route[0].startswith("openrouter/")
    assert llm_router.resolve_cloud_vision_route()[0].startswith("openrouter/")
    assert [(d["task"], d["outcome"], d["reason"]) for d in seen] == [
        ("document_analysis", "cloud", "cheap_tier"), ("vision", "cloud", "cloud_vision")]
    assert all("prompt" not in d and "messages" not in d for d in seen)


@pytest.mark.asyncio
async def test_cloud_embeddings_ask_for_zero_retention(monkeypatch):
    from vula.ingestion import pipeline
    monkeypatch.setattr(pipeline.settings, "openrouter_zdr", True)
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2]}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.update(json or {})
            return _Resp()
    emb = pipeline.EmbeddingEngine.__new__(pipeline.EmbeddingEngine) if hasattr(pipeline, "EmbeddingEngine") else None
    if emb is None:
        pytest.skip("embedding class name changed")
    emb.model, emb._dim = "text-embedding-3-small", 0
    with patch.object(pipeline.httpx, "AsyncClient", _Client):
        assert await emb._embed_openai("hello") == [0.1, 0.2]
    assert sent["provider"] == {"data_collection": "deny", "zdr": True}


# ── opt-out on direct marketing ─────────────────────────────────────────────────────────────

def _consent(monkeypatch, rows=None, fail=False):
    from unittest.mock import MagicMock
    db = MagicMock()
    q = db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute
    if fail:
        q.side_effect = RuntimeError("db down")
    else:
        q.return_value.data = rows or []
    monkeypatch.setattr("vula.commerce.service._client", lambda: db)


def test_opted_out_matches_any_number_format_and_fails_closed(monkeypatch):
    from vula.consent import opted_out
    _consent(monkeypatch, [{"phone": "+27 82 000 1111", "status": "opted_out"}])
    assert opted_out("t", "0820001111") and not opted_out("t", "0820002222")
    _consent(monkeypatch, fail=True)
    assert opted_out("t", "0820002222") is True


@pytest.mark.asyncio
async def test_a_listing_is_never_shared_with_someone_who_opted_out(monkeypatch):
    from vula.listings import sharing
    _consent(monkeypatch, [{"phone": "27820001111", "status": "opted_out"}])
    with patch("vula.api.whatsapp._resolve_wa", new=AsyncMock(side_effect=AssertionError("must not send"))):
        out = await sharing.send_listings("t", "0820001111", [{"id": "p1", "slug": "p1"}], intro="hi")
    assert out["ok"] is False and "opted out" in out["reason"]


@pytest.mark.asyncio
async def test_wish_list_matches_leave_out_people_who_opted_out(monkeypatch):
    from unittest.mock import MagicMock
    from vula.listings import sharing
    wishes = [{"phone": "27820001111"}, {"phone": "27820002222"}]
    db = MagicMock()

    def table(name):
        t = MagicMock()
        if name == sharing.WISHES:
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = wishes
        else:
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        return t
    db.table.side_effect = table
    monkeypatch.setattr(sharing, "_client", lambda: db)
    monkeypatch.setattr(sharing, "wish_fits", lambda w, p: True)
    monkeypatch.setattr("vula.consent.opted_out", lambda t, p: p == "27820001111")
    prop = {"id": "p1", "status": next(iter(sharing.ls.PUBLIC_STATUSES))}
    assert [w["phone"] for w in await sharing.matches_for("t", prop)] == ["27820002222"]


@pytest.mark.asyncio
async def test_a_private_statement_is_recognised_before_any_model_reads_it(tmp_path, monkeypatch):
    from vula.commerce import bank_accounts, bank_rec
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(bank_rec, "get_statement_password", lambda t: None)
    monkeypatch.setattr(bank_rec, "extract_pdf_text", lambda p, pwd: "Nedbank statement account 1234567890 ...")
    monkeypatch.setattr(bank_rec, "looks_like_supplier_document", lambda t: False)
    monkeypatch.setattr(bank_rec, "looks_like_own_bank_statement", lambda t: True)
    monkeypatch.setattr(bank_accounts, "account_number", lambda t: "1234567890")
    monkeypatch.setattr(bank_rec, "_private_owner", lambda t, a: "m-richard" if a == "1234567890" else None)
    monkeypatch.setattr(bank_rec, "extract_transactions", AsyncMock(side_effect=AssertionError("sent to a model")))
    out = await bank_rec.ingest_statement("digg-demo", pdf, source_file="s.pdf")
    assert out["private"] is True and out["private_to"] == "m-richard"


def test_delete_my_data_now_covers_messages_sent_contacts_and_flows(monkeypatch):
    from unittest.mock import MagicMock
    from vula import privacy_data
    db = MagicMock()
    monkeypatch.setattr(privacy_data, "_client", lambda: db)
    labels = [label for label, _ in privacy_data.ERASE_STEPS]
    for want in ("messages_sent", "contact_record", "broadcasts_received", "delivery_failures",
                 "flow_progress", "pending_confirmations"):
        assert want in labels
    step = dict(privacy_data.ERASE_STEPS)["messages_sent"]
    step("off-the-hook", ["27820001111", "0820001111"])
    db.table.assert_called_with("vula_wa_outbound")
    chain = db.table.return_value.delete.return_value
    chain.eq.assert_called_with("tenant_id", "off-the-hook")                  # this business only
    chain.eq.return_value.in_.assert_called_with("to_phone", ["27820001111", "0820001111"])


@pytest.mark.parametrize("name,skipped", [
    ("Personal", True), ("INBOX/Private", True), ("Medical aid", True), ("Family", True),
    ("Persoonlik", True), ("Holiday 2025", True),
    ("Payment Certificates", False), ("HPC", False), ("Archive", False), ("Clients/Homestead Estates", False),
    ("Invoices", False), ("Suppliers", False),
])
def test_personal_folders_are_never_read(name, skipped):
    from vula.email_imap import sync
    assert sync._folder_skipped(set(), name, set()) is skipped
