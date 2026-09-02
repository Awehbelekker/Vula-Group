"""A supplier's invoice must not consume the tenant's own invoice number.

2026-09-02, real DIGG data: inbound documents (supplier invoices filed from email) drew from the
SAME counter as DIGG's own outgoing invoices. 74 of the 77 issued DIG-INV numbers had gone to
other companies' invoices:

    DIG-INV-00074  outbound  team BCM                  (ours)
    DIG-INV-00075  inbound   Digg                      <- supplier bill
    DIG-INV-00076  inbound   IonConcrete (Pty) Ltd     <- supplier bill
    ...
    DIG-INV-00083  inbound   Caisson (Pty) Ltd         <- supplier bill

leaving DIGG's own series reading 51 -> 61 -> 74 -> 85. SARS expects an unbroken sequential
tax-invoice series, so this made their books look like dozens of invoices had been issued and
voided. Existing rows deliberately keep their numbers — renumbering issued documents would be
worse than the gaps.
"""
from unittest.mock import patch

import pytest

from vula.commerce import service


class _Rpc:
    def __init__(self, sink, value=7):
        self.sink, self.value = sink, value

    def rpc(self, name, params):
        self.sink.append(params)
        return self

    def execute(self):
        return type("R", (), {"data": self.value})()


@pytest.mark.asyncio
async def test_outbound_keeps_the_tenants_own_invoice_series():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        num = await service._next_invoice_number("digg-demo", "invoice")
    assert num == "DIG-INV-00007"
    assert sink[0]["p_counter_key"] == "invoice"


@pytest.mark.asyncio
async def test_inbound_uses_a_separate_counter_and_code():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        num = await service._next_invoice_number("digg-demo", "invoice", direction="inbound")
    assert num == "DIG-BILL-00007", "a supplier bill must not look like our own invoice"
    assert sink[0]["p_counter_key"] == "inbound_invoice", "must not share the outgoing counter"


@pytest.mark.asyncio
async def test_the_two_directions_never_share_a_counter():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        await service._next_invoice_number("digg-demo", "invoice")
        await service._next_invoice_number("digg-demo", "invoice", direction="inbound")
    keys = [p["p_counter_key"] for p in sink]
    assert len(set(keys)) == 2, f"inbound and outbound shared a counter: {keys}"


@pytest.mark.asyncio
async def test_quotes_keep_their_own_code_when_outbound():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        num = await service._next_invoice_number("digg-demo", "quote")
    assert num.startswith("DIG-QTE-")


@pytest.mark.asyncio
async def test_an_inbound_quote_is_also_a_bill_reference():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        num = await service._next_invoice_number("digg-demo", "quote", direction="inbound")
    assert num.startswith("DIG-BILL-")
    assert sink[0]["p_counter_key"] == "inbound_quote"


@pytest.mark.asyncio
async def test_direction_defaults_to_outbound_so_existing_callers_are_unchanged():
    sink = []
    with patch.object(service, "_client", lambda: _Rpc(sink)):
        a = await service._next_invoice_number("off-the-hook", "invoice")
        b = await service._next_invoice_number("off-the-hook", "invoice", direction="outbound")
    assert a == b == "OFF-INV-00007"


def test_the_scan_commit_path_marks_its_number_inbound():
    """Guards the exact regression at the write site."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "vula" / "commerce" / "service.py"
    text = src.read_text(encoding="utf-8")
    assert 'direction="inbound")' in text
    assert '"direction": "inbound", "doc_type": doc_type,\n' in text


# ── the Invoices tab must not hide most of the tenant's documents ───────────────
# "All the old invoices are not showing" (DIGG, 2026-09-02): the list endpoint defaulted to
# direction="outbound", so the tab showed 6 of 134 documents with no indication the other 128
# existed. Safe to show both now that inbound carries a distinct BILL reference.

def test_the_list_endpoint_defaults_to_showing_both_directions():
    """Asserted on the declared default: calling the handler directly would hand us FastAPI's
    Query object rather than the value it resolves to at request time."""
    import inspect
    from vula.api import commerce as api
    default = inspect.signature(api.admin_list_invoices).parameters["direction"].default
    resolved = getattr(default, "default", default)
    assert resolved is None, "a default of 'outbound' hides supplier bills entirely"


@pytest.mark.asyncio
async def test_an_explicit_direction_is_still_honoured():
    from vula.api import commerce as api
    seen = {}

    async def _fake(tenant_id, **kw):
        seen.update(kw)
        return []

    with patch.object(api.service, "list_invoices", _fake):
        await api.admin_list_invoices("digg-demo", direction="outbound")
    assert seen["direction"] == "outbound"


@pytest.mark.asyncio
async def test_no_direction_filter_is_applied_when_none():
    """service.list_invoices already treats None as 'no filter' — lock that in."""
    captured = {}

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, col, val):
            captured[col] = val
            return self
        def order(self, *a, **k): return self
        def range(self, *a, **k): return self
        def execute(self): return type("R", (), {"data": []})()

    with patch.object(service, "_client", lambda: type("C", (), {"table": lambda s, n: _Q()})()):
        await service.list_invoices("digg-demo", direction=None)
    assert "direction" not in captured
