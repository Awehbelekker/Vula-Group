"""When Vula can't tell whose document it is, it must ASK — not file it quietly.

2026-09-03. The scan path sees only who ISSUED a document; there is no bill-to field. When the
issuer is neither this business nor a known supplier, the direction is a coin flip — a new
supplier, or a client whose invoice is being imported in bulk (Ian: tenants will import old
paperwork mixing clients, suppliers and expenses).

Setting needs_review looked like a safeguard but was a no-op for this case: the only consumer of
that flag requires a supplier match, which an unrecognised party by definition does not have. So
the flag would sit in a column nobody reads while every ambiguous document was silently filed as
a supplier bill — the same failure that put 51 of off-the-hook's own sales invoices, R32,307.97,
on the wrong side of the books.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vula.api.whatsapp as wa

TENANT = "off-the-hook"
INV = "inv-123"


# ── asking ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_owner_is_offered_all_three_choices():
    sent = {}

    async def _buttons(creds, number, body, buttons):
        sent["body"], sent["buttons"] = body, buttons
        return True

    with patch("vula.commerce.approvals.tenant_admin_approvers",
               AsyncMock(return_value=[{"phone": "27737815979", "name": "Staci"}])), \
         patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value={"token": "t", "phone_id": "p"})), \
         patch.object(wa, "_send_wa_buttons", _buttons):
        ok = await wa.ask_document_kind(TENANT, INV, "Bloggs Architects", 452000)

    assert ok is True
    titles = [b["title"] for b in sent["buttons"]]
    assert titles == ["Supplier bill", "Our invoice", "An expense"]
    assert all(b["id"].endswith(INV) for b in sent["buttons"])
    assert "Bloggs Architects" in sent["body"]
    assert "R4,520.00" in sent["body"]


@pytest.mark.asyncio
async def test_it_says_plainly_why_it_is_asking():
    sent = {}

    async def _buttons(creds, number, body, buttons):
        sent["body"] = body
        return True

    with patch("vula.commerce.approvals.tenant_admin_approvers",
               AsyncMock(return_value=[{"phone": "27737815979"}])), \
         patch.object(wa, "_get_tenant_wa_creds", AsyncMock(return_value={"token": "t", "phone_id": "p"})), \
         patch.object(wa, "_send_wa_buttons", _buttons):
        await wa.ask_document_kind(TENANT, INV, "Bloggs Architects", 452000)

    assert "don't recognise them as a supplier" in sent["body"]
    assert "wasn't issued by you" in sent["body"]


@pytest.mark.asyncio
async def test_no_one_to_ask_reports_false_so_the_caller_keeps_its_fallback():
    with patch("vula.commerce.approvals.tenant_admin_approvers", AsyncMock(return_value=[])):
        assert await wa.ask_document_kind(TENANT, INV, "Someone", 100) is False


# ── answering ───────────────────────────────────────────────────────────────────

def _db_with(row):
    updates = []

    class _Q:
        def select(self, *a, **k): return self
        def update(self, patch_):
            updates.append(patch_)
            return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return MagicMock(data=[row] if row else [])
    return MagicMock(table=MagicMock(return_value=_Q())), updates


ROW = {"id": INV, "supplier": "Bloggs Architects", "total_cents": 452000}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected_direction", [
    ("supplier", "inbound"),
    ("client", "outbound"),
])
async def test_the_answer_files_it_on_the_right_side(kind, expected_direction):
    from vula.commerce import service as cs
    db, updates = _db_with(ROW)
    with patch.object(cs, "_client", lambda: db), \
         patch.object(wa, "_send_reply", AsyncMock()) as reply:
        await wa._handle_document_kind_reply("27737815979", f"docdir:{kind}:{INV}", TENANT)
    assert updates[0]["direction"] == expected_direction
    assert updates[0]["needs_review"] is False
    assert "R4,520.00" in reply.await_args[0][1]


@pytest.mark.asyncio
async def test_expense_is_flagged_not_silently_moved():
    """Moving money between an invoice and an expense record changes the books — a button tap
    must not do that invisibly, which is the whole failure this pass has been correcting."""
    from vula.commerce import service as cs
    db, updates = _db_with(ROW)
    with patch.object(cs, "_client", lambda: db), \
         patch.object(wa, "_send_reply", AsyncMock()) as reply:
        await wa._handle_document_kind_reply("27737815979", f"docdir:expense:{INV}", TENANT)
    assert updates[0]["needs_review"] is True
    assert "direction" not in updates[0], "must not re-file it as either side"
    assert "rather than moving it myself" in reply.await_args[0][1]


@pytest.mark.asyncio
async def test_a_missing_document_says_so_rather_than_erroring():
    from vula.commerce import service as cs
    db, _ = _db_with(None)
    with patch.object(cs, "_client", lambda: db), \
         patch.object(wa, "_send_reply", AsyncMock()) as reply:
        await wa._handle_document_kind_reply("27737815979", f"docdir:supplier:{INV}", TENANT)
    assert "isn't around" in reply.await_args[0][1]


@pytest.mark.asyncio
async def test_a_malformed_reply_id_is_ignored_safely():
    with patch.object(wa, "_send_reply", AsyncMock()) as reply:
        await wa._handle_document_kind_reply("27737815979", "docdir:broken", TENANT)
    reply.assert_not_awaited()


def test_the_button_ids_are_routed_in_the_webhook():
    import inspect
    src = inspect.getsource(wa)
    assert 'reply_id.startswith("docdir:")' in src
    assert "_handle_document_kind_reply(phone, reply_id, route_tenant)" in src
