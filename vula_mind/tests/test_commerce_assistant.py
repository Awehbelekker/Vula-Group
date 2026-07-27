"""Tests for the commerce_assistant skill and its WhatsApp handler wiring."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.base import SkillInput, SkillOutput
from core.skills.commerce_assistant import CommerceAssistantSkill

TENANT = "off-the-hook"
CTX = {"tenant_id": TENANT, "session_id": "+27821234567", "customer_phone": "+27821234567"}


def _msg(content="", tool_calls=None):
    """Build a fake litellm choices[0].message."""
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    return resp


# ── Tool executors / dispatch ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_list_products_formats_prices():
    skill = CommerceAssistantSkill()
    products = [{"slug": "snoek", "name": "Fresh Snoek", "price_cents": 18500, "category": "linefish", "description": ""}]
    with patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=products)):
        out = await skill._dispatch_tool("list_products", {}, CTX)
    assert out == [{"slug": "snoek", "name": "Fresh Snoek", "price": "R185.00", "category": "linefish"}]


@pytest.mark.asyncio
async def test_dispatch_add_to_cart_by_slug():
    skill = CommerceAssistantSkill()
    product = {"id": "p1", "name": "Fresh Snoek", "price_cents": 18500}
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=product)),
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1"})),
        patch("core.skills.commerce_assistant.service.add_to_cart", new=AsyncMock()) as mock_add,
    ):
        out = await skill._dispatch_tool("add_to_cart", {"product": "snoek", "quantity": 2}, CTX)
    mock_add.assert_awaited_once_with(TENANT, "c1", "p1", 2.0, variant_id=None)
    # quantity is a display string ("2" not 2) and line_total is included — both from the
    # per-kg pricing support (_fmt_qty/_line_cents), which apply even to non-kg products.
    assert out == {"added": "Fresh Snoek", "quantity": "2", "unit_price": "R185.00", "line_total": "R370.00"}


@pytest.mark.asyncio
async def test_dispatch_add_to_cart_unknown_product():
    skill = CommerceAssistantSkill()
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=None)),
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
    ):
        out = await skill._dispatch_tool("add_to_cart", {"product": "unicorn"}, CTX)
    assert "error" in out


@pytest.mark.asyncio
async def test_dispatch_track_order_found():
    skill = CommerceAssistantSkill()
    orders = [{"display_id": "OTH-00042", "status": "dispatched", "total_cents": 25000}]
    with patch("core.skills.commerce_assistant.service.list_orders", new=AsyncMock(return_value=orders)):
        out = await skill._dispatch_tool("track_order", {"order_id": "oth-00042"}, CTX)
    assert out == {"order_id": "OTH-00042", "status": "dispatched", "total": "R250.00"}


@pytest.mark.asyncio
async def test_dispatch_create_quote_from_explicit_items():
    skill = CommerceAssistantSkill()
    product = {"id": "p1", "name": "Fresh Snoek", "price_cents": 18500}
    created = {"invoice_number": "OFF-QTE-00001", "total_cents": 37000}
    with (
        patch("core.skills.commerce_assistant.service.get_product_by_slug", new=AsyncMock(return_value=product)),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock(return_value=created)) as mock_create,
    ):
        out = await skill._dispatch_tool(
            "create_quote", {"items": [{"product": "snoek", "quantity": 2}]}, CTX
        )
    assert out == {"quote_number": "OFF-QTE-00001", "items": 1, "total": "R370.00"}
    payload = mock_create.await_args.args[1]
    assert payload["doc_type"] == "quote"
    assert payload["customer_phone"] == CTX["customer_phone"]
    assert payload["line_items"][0] == {
        "description": "Fresh Snoek",
        "quantity": 2,
        "unit_price_cents": 18500,
        "product_id": "p1",
    }


@pytest.mark.asyncio
async def test_dispatch_create_quote_from_cart_when_no_items():
    skill = CommerceAssistantSkill()
    cart = {
        "id": "c1",
        "commerce_cart_items": [
            {"product_id": "p1", "quantity": 3, "unit_price_cents": 18500,
             "commerce_products": {"name": "Fresh Snoek"}},
        ],
    }
    created = {"invoice_number": "OFF-QTE-00002", "total_cents": 55500}
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value=cart)),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock(return_value=created)) as mock_create,
    ):
        out = await skill._dispatch_tool("create_quote", {}, CTX)
    assert out["quote_number"] == "OFF-QTE-00002"
    assert out["items"] == 1
    assert mock_create.await_args.args[1]["line_items"][0]["quantity"] == 3


@pytest.mark.asyncio
async def test_dispatch_create_quote_empty_returns_error():
    skill = CommerceAssistantSkill()
    with (
        patch("core.skills.commerce_assistant.service.get_or_create_cart", new=AsyncMock(return_value={"id": "c1", "commerce_cart_items": []})),
        patch("core.skills.commerce_assistant.service.create_invoice", new=AsyncMock()) as mock_create,
    ):
        out = await skill._dispatch_tool("create_quote", {}, CTX)
    assert "error" in out
    mock_create.assert_not_awaited()


def test_start_checkout_uses_store_url():
    skill = CommerceAssistantSkill()
    with patch("core.skills.commerce_assistant.settings") as mock_settings:
        mock_settings.store_urls = {TENANT: "https://offthehook.co.za"}
        out = skill._exec_start_checkout(TENANT)
    assert out == {"checkout_url": "https://offthehook.co.za/cart"}


# ── Agent loop + fallback ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_returns_content_without_tool_calls():
    skill = CommerceAssistantSkill()
    with patch("litellm.acompletion", new=AsyncMock(return_value=_msg("Howzit! 🐟"))):
        answer = await skill._agent_loop("sys", "", "hi", CTX)
    assert answer == "Howzit! 🐟"


@pytest.mark.asyncio
async def test_run_falls_back_when_loop_fails():
    skill = CommerceAssistantSkill()
    inp = SkillInput(question="what fish do you have?", tenant_id=TENANT, metadata=CTX)
    with (
        patch.object(skill, "_retrieve_kb", new=AsyncMock(return_value=("", []))),
        patch.object(skill, "_agent_loop", new=AsyncMock(side_effect=RuntimeError("no tools"))),
        patch("core.skills.commerce_assistant.service.list_products", new=AsyncMock(return_value=[])),
        patch("litellm.acompletion", new=AsyncMock(return_value=_msg("We have fresh snoek today."))),
    ):
        out = await skill.run(inp)
    assert out.success
    assert out.answer == "We have fresh snoek today."
    assert out.confidence < 0.6  # fallback confidence band


# ── KB retrieval (verification-context reuse) ─────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_kb_sources_carry_chunk_text():
    """sources must carry retrieved text (not just filename/score) so
    core/verification.py's context-aware adversarial check has something to
    ground its check against — added alongside reasoning.py/architecture_planning.py."""
    skill = CommerceAssistantSkill()
    mock_pipeline = MagicMock()
    mock_pipeline.query = AsyncMock(return_value=[
        {"filename": "menu.pdf", "text": "Fresh snoek R185/kg", "score": 0.6},
    ])
    with patch("vula.ingestion.pipeline.VulaIngestionPipeline", return_value=mock_pipeline):
        _, sources = await skill._retrieve_kb(SkillInput(question="snoek price", tenant_id=TENANT))
    assert sources[0]["text"] == "Fresh snoek R185/kg"
    assert sources[0]["type"] == "kb"


# ── Booking-focused conditioning (health/services tenants, no storefront) ──────

from core.skills.commerce_assistant import _ASK_TEAM_ONLY, _is_booking_focused


@pytest.mark.parametrize("business_type,has_bookings,expected", [
    ("health", True, True),
    ("services", True, True),
    ("health", False, False),   # bookings module not actually enabled — no tools to use
    ("food", True, False),      # a storefront that also takes bookings stays storefront-first
    ("retail", True, False),
    ("", True, False),
])
def test_is_booking_focused(business_type, has_bookings, expected):
    with (
        patch("core.skills.commerce_assistant._tenant_has_bookings", return_value=has_bookings),
        patch("core.skills.commerce_assistant._tenant_business_type", return_value=business_type),
    ):
        assert _is_booking_focused(TENANT) is expected


def test_booking_focused_prompt_has_no_storefront_copy():
    skill = CommerceAssistantSkill()
    prompt = skill._system_prompt(TENANT, kb_context="", booking_focused=True)
    assert "fish" not in prompt.lower()
    assert "cart" not in prompt.lower()
    assert "list_availability" in prompt
    assert "book_appointment" in prompt
    assert "cancel_appointment" in prompt


def test_storefront_prompt_unchanged_by_default():
    skill = CommerceAssistantSkill()
    prompt = skill._system_prompt(TENANT, kb_context="")
    assert "fresh fish and chicken delivery business" in prompt


@pytest.mark.asyncio
async def test_agent_loop_uses_ask_team_only_tools_when_booking_focused():
    skill = CommerceAssistantSkill()
    captured = {}

    async def _fake_completion(*a, **kw):
        # Only the first (tool-offering) call includes "tools" — a same-turn escalation
        # retry (looks_unreliable) omits it; capture just the one we care about.
        if "tools" in kw:
            captured["tools"] = kw["tools"]
        return _msg("A confident, complete-looking answer with plenty of content in it.")

    with (
        patch("litellm.acompletion", new=_fake_completion),
        patch("core.skills.commerce_assistant.resolve_generation_route",
              new=AsyncMock(return_value=("ollama/test", None, "http://localhost:11434"))),
    ):
        await skill._agent_loop("sys", "", "book me a slot",
                                {"tenant_id": TENANT, "session_id": "s", "customer_phone": "s",
                                 "bookings": True, "booking_focused": True, "current_message": "book me a slot"})

    tool_names = {t["function"]["name"] for t in captured["tools"]}
    expected_ask_team = {t["function"]["name"] for t in _ASK_TEAM_ONLY}
    assert tool_names == expected_ask_team | {"list_availability", "book_appointment", "cancel_appointment"}
    assert "list_products" not in tool_names


# ── WhatsApp handler wiring ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_commerce_assistant_persists_both_turns():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_skill = AsyncMock(return_value=SkillOutput(answer="Fresh snoek is R185.", skill_name="commerce_assistant"))
    with (
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.commerce.service.get_or_create_session", new=AsyncMock(return_value={"id": "sess-1"})),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("vula.commerce.service.append_message", new=AsyncMock()) as mock_append,
        patch("vula.api.whatsapp._send_reply", new=AsyncMock(return_value=True)) as mock_send,
    ):
        handled = await _run_commerce_assistant("+27821234567", "snoek price?", TENANT)

    assert handled is True
    mock_send.assert_awaited_once_with("+27821234567", "Fresh snoek is R185.", TENANT)
    assert mock_append.await_count == 2


@pytest.mark.asyncio
async def test_run_commerce_assistant_returns_false_on_empty_answer():
    from vula.api.whatsapp import _run_commerce_assistant

    mock_skill = AsyncMock(return_value=SkillOutput(answer="", skill_name="commerce_assistant", error="boom"))
    with (
        patch("core.skills.loader.get_skill", return_value=mock_skill),
        patch("vula.commerce.service.get_or_create_session", new=AsyncMock(return_value={"id": "sess-1"})),
        patch("vula.commerce.service.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("vula.api.whatsapp._send_reply", new=AsyncMock()) as mock_send,
    ):
        handled = await _run_commerce_assistant("+27821234567", "snoek price?", TENANT)

    assert handled is False
    mock_send.assert_not_awaited()



# ── PDF renderer unit tests ────────────────────────────────────────────────────

_SAMPLE_INVOICE = {
    "id": "inv-001",
    "tenant_id": "off-the-hook",
    "doc_type": "invoice",
    "invoice_number": "OTH-INV-00001",
    "customer_name": "Cape Fish Market",
    "customer_email": "orders@capefishmarket.co.za",
    "customer_phone": "+27211234567",
    "customer_address": "12 Harbour Rd, Cape Town",
    "line_items": [
        {"description": "Fresh Snoek (per kg)", "quantity": 5, "unit_price_cents": 18500, "total_cents": 92500},
        {"description": "Yellowtail (per kg)", "quantity": 3, "unit_price_cents": 22000, "total_cents": 66000},
    ],
    "subtotal_cents": 158500,
    "vat_rate": 15.0,
    "vat_cents": 23775,
    "total_cents": 182275,
    "status": "draft",
    "issue_date": "2026-06-03",
    "due_date": "2026-06-17",
    "notes": "Please EFT before delivery.",
}


def test_render_invoice_pdf_returns_bytes():
    """render_invoice_pdf should produce a non-empty bytes object."""
    try:
        from weasyprint import HTML  # noqa: F401  — skip if not installed
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")

    from vula.commerce.pdf import render_invoice_pdf
    pdf = render_invoice_pdf(_SAMPLE_INVOICE)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 1024, "PDF is unexpectedly small"
    assert pdf[:4] == b"%PDF", "Output is not a valid PDF"


def test_render_quote_pdf_label():
    """Quote doc_type should produce a 'Quotation' title in the HTML."""
    from vula.commerce.pdf import _DOC_HTML, _DOC_LABELS
    assert _DOC_LABELS["quote"] == "Quotation"
    assert _DOC_LABELS["invoice"] == "Tax Invoice"
    assert _DOC_LABELS["proforma"] == "Pro Forma Invoice"
    # The shared skeleton must expose the CSS slot the renderer substitutes.
    assert "__TEMPLATE_CSS__" in _DOC_HTML


def test_render_invoice_pdf_integer_cents():
    """Totals in the invoice must all be integers — never floats."""
    invoice = dict(_SAMPLE_INVOICE)
    # Verify the renderer doesn't mutate types to floats
    from vula.commerce.pdf import render_invoice_pdf
    try:
        from weasyprint import HTML  # noqa: F401
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")

    render_invoice_pdf(invoice)
    # The original dict should be untouched (we work on a copy of line_items)
    assert isinstance(invoice["total_cents"], int)
    assert isinstance(invoice["subtotal_cents"], int)
    assert isinstance(invoice["vat_cents"], int)


def test_line_items_total_cents_auto_computed():
    """Items missing total_cents should have it computed during render."""
    try:
        from weasyprint import HTML  # noqa: F401
    except ImportError:
        pytest.skip("weasyprint not installed in test environment")

    from vula.commerce.pdf import render_invoice_pdf
    invoice = dict(_SAMPLE_INVOICE)
    # Strip total_cents from line items to test auto-computation
    invoice["line_items"] = [
        {"description": "Snoek", "quantity": 2, "unit_price_cents": 18500},
    ]
    invoice["subtotal_cents"] = 37000
    invoice["vat_cents"] = 5550
    invoice["total_cents"] = 42550
    pdf = render_invoice_pdf(invoice)
    assert isinstance(pdf, bytes) and len(pdf) > 1024


# ── Invoice email delivery ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_invoice_email_base64_encodes_attachment():
    """send_invoice_email should base64-encode the PDF and pass it to _send."""
    import base64

    from vula.api import email as email_mod

    pdf_bytes = b"%PDF-1.4 fake pdf bytes"
    with patch.object(email_mod, "_send", new=AsyncMock(return_value=True)) as mock_send:
        ok = await email_mod.send_invoice_email(
            "orders@capefishmarket.co.za", _SAMPLE_INVOICE, pdf_bytes, "Off the Hook"
        )
    assert ok is True
    kwargs = mock_send.await_args.kwargs
    attachments = kwargs["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "OTH-INV-00001.pdf"
    assert base64.b64decode(attachments[0]["content"]) == pdf_bytes
    # Recipient + tenant-branded subject
    assert mock_send.await_args.args[0] == "orders@capefishmarket.co.za"
    assert "Off the Hook" in mock_send.await_args.args[1]


@pytest.mark.asyncio
async def test_send_invoice_email_quote_label_in_subject():
    """A quote doc_type should be labelled 'Quotation' in the subject line."""
    from vula.api import email as email_mod

    quote = dict(_SAMPLE_INVOICE)
    quote["doc_type"] = "quote"
    quote["invoice_number"] = "OTH-QTE-00007"
    with patch.object(email_mod, "_send", new=AsyncMock(return_value=True)) as mock_send:
        await email_mod.send_invoice_email("a@b.co.za", quote, b"%PDF", "Off the Hook")
    assert "Quotation" in mock_send.await_args.args[1]
    assert "OTH-QTE-00007" in mock_send.await_args.args[1]
