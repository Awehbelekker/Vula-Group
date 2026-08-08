"""Tests for supplier auto-reorder POs (migration 123).

vula/commerce/purchase_orders.py drafts POs from products explicitly configured for auto-reorder
(reorder_threshold/reorder_qty/default_supplier_id all set) — draft only, never auto-sent — and
dispatches a confirmed draft via email and/or WhatsApp. Uses the same in-memory fake Supabase
client pattern as tests/test_ledger.py and tests/test_flows.py.
"""
import uuid

import pytest

import vula.commerce.purchase_orders as po_mod


class _Result:
    def __init__(self, data):
        self.data = data


class _NotProxy:
    def __init__(self, query):
        self.query = query

    def is_(self, key, _val):
        self.query.not_null_keys.append(key)
        return self.query


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self.not_null_keys = []
        self._limit = None
        self._order = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, key, val):
        self.filters.append((key, val))
        return self

    @property
    def not_(self):
        return _NotProxy(self)

    def order(self, key, desc=False):
        self._order = (key, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        if not all(row.get(k) == v for k, v in self.filters):
            return False
        if any(row.get(k) is None for k in self.not_null_keys):
            return False
        return True

    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if self._order:
            key, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(key) or "", reverse=desc)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)

    def insert(self, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("created_at", "2026-08-02T00:00:00Z")
        self.table.rows.append(row)
        return _ExecWrapper([row])

    def update(self, patch):
        self._patch = patch
        return self


class _ExecWrapper:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


class _FakeQueryWithUpdate(_FakeQuery):
    def execute(self):
        rows = [r for r in self.table.rows if self._matches(r)]
        if hasattr(self, "_patch"):
            for r in rows:
                r.update(self._patch)
        if self._limit:
            rows = rows[: self._limit]
        return _Result(rows)


class _FakeTable:
    def __init__(self, name, store):
        self.rows = store.setdefault(name, [])


class _FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _FakeQueryWithUpdate(_FakeTable(name, self.store))


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(po_mod, "_client", lambda: client)
    return client


TID = "test-tenant"
SUP_A = str(uuid.uuid4())
SUP_B = str(uuid.uuid4())


def _seed_supplier(client, sid, name):
    client.store.setdefault("commerce_suppliers", []).append(
        {"id": sid, "tenant_id": TID, "name": name, "contact_email": None, "contact_phone": None})


def _product(*, name, stock, threshold, qty, supplier_id):
    return {"id": str(uuid.uuid4()), "tenant_id": TID, "name": name, "stock_quantity": stock,
            "reorder_threshold": threshold, "reorder_qty": qty, "default_supplier_id": supplier_id,
            "price_cents": 0}


def _mock_reorder_sources(monkeypatch, products, suppliers=()):
    """get_reorder_suggestions() (and therefore draft_reorder_pos()) reads via
    service.list_products/list_variants/list_suppliers, not raw table queries — mock those."""
    async def _list_products(tid, **_kw):
        return list(products)

    async def _list_variants(tid, product_id, **_kw):
        return []  # no variant-level tests here — covered by the shared endpoint's own history

    async def _list_suppliers(tid):
        return list(suppliers)

    monkeypatch.setattr("vula.commerce.service.list_products", _list_products)
    monkeypatch.setattr("vula.commerce.service.list_variants", _list_variants)
    monkeypatch.setattr("vula.commerce.service.list_suppliers", _list_suppliers)


# ── draft_reorder_pos ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drafts_po_for_qualifying_low_stock_product(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch,
        [_product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A)],
        [{"id": SUP_A, "name": "Acme Supplies"}])

    drafted = await po_mod.draft_reorder_pos(TID)
    assert len(drafted) == 1
    assert drafted[0]["supplier_id"] == SUP_A
    assert drafted[0]["status"] == "draft"
    assert drafted[0]["items"][0]["name"] == "Widget"
    assert drafted[0]["items"][0]["quantity"] == 20


@pytest.mark.asyncio
async def test_ignores_products_without_default_supplier(fake_client, monkeypatch):
    # Low stock, but no default supplier — falls into the "unassigned" group, which
    # draft_reorder_pos deliberately skips (nowhere to send a PO); still visible via
    # get_reorder_suggestions for the owner to handle manually, same as before this feature.
    _mock_reorder_sources(monkeypatch,
        [_product(name="Unconfigured", stock=1, threshold=5, qty=10, supplier_id=None)])
    drafted = await po_mod.draft_reorder_pos(TID)
    assert drafted == []


@pytest.mark.asyncio
async def test_ignores_products_with_no_reorder_threshold(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch,
        [_product(name="No threshold set", stock=1, threshold=None, qty=None, supplier_id=SUP_A)],
        [{"id": SUP_A, "name": "Acme Supplies"}])
    drafted = await po_mod.draft_reorder_pos(TID)
    assert drafted == []


@pytest.mark.asyncio
async def test_ignores_products_above_threshold(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch,
        [_product(name="Well stocked", stock=50, threshold=5, qty=20, supplier_id=SUP_A)],
        [{"id": SUP_A, "name": "Acme Supplies"}])
    drafted = await po_mod.draft_reorder_pos(TID)
    assert drafted == []


@pytest.mark.asyncio
async def test_groups_multiple_low_stock_products_by_supplier(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch, [
        _product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A),
        _product(name="Gadget", stock=1, threshold=3, qty=10, supplier_id=SUP_A),
    ], [{"id": SUP_A, "name": "Acme Supplies"}])
    drafted = await po_mod.draft_reorder_pos(TID)
    assert len(drafted) == 1
    assert {it["name"] for it in drafted[0]["items"]} == {"Widget", "Gadget"}


@pytest.mark.asyncio
async def test_separate_suppliers_get_separate_pos(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch, [
        _product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A),
        _product(name="Gizmo", stock=1, threshold=3, qty=15, supplier_id=SUP_B),
    ], [{"id": SUP_A, "name": "Acme Supplies"}, {"id": SUP_B, "name": "Beta Traders"}])
    drafted = await po_mod.draft_reorder_pos(TID)
    assert len(drafted) == 2
    assert {d["supplier_id"] for d in drafted} == {SUP_A, SUP_B}


@pytest.mark.asyncio
async def test_repeated_run_does_not_duplicate_the_draft(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch,
        [_product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A)],
        [{"id": SUP_A, "name": "Acme Supplies"}])

    await po_mod.draft_reorder_pos(TID)
    second_run = await po_mod.draft_reorder_pos(TID)
    # Nothing NEW qualifies on the second run (same product, same low stock) — no new draft,
    # no duplicate line, so the merge path finds nothing to add and returns nothing for it.
    assert second_run == []
    all_pos = [p for p in fake_client.store["commerce_purchase_orders"] if p["tenant_id"] == TID]
    assert len(all_pos) == 1
    assert len(all_pos[0]["items"]) == 1


@pytest.mark.asyncio
async def test_second_low_stock_product_merges_into_existing_draft(fake_client, monkeypatch):
    _mock_reorder_sources(monkeypatch,
        [_product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A)],
        [{"id": SUP_A, "name": "Acme Supplies"}])
    await po_mod.draft_reorder_pos(TID)

    _mock_reorder_sources(monkeypatch, [
        _product(name="Widget", stock=2, threshold=5, qty=20, supplier_id=SUP_A),
        _product(name="Gadget", stock=1, threshold=3, qty=10, supplier_id=SUP_A),
    ], [{"id": SUP_A, "name": "Acme Supplies"}])
    drafted = await po_mod.draft_reorder_pos(TID)

    assert len(drafted) == 1
    all_pos = [p for p in fake_client.store["commerce_purchase_orders"] if p["tenant_id"] == TID]
    assert len(all_pos) == 1  # still one PO for this supplier, not a second one
    assert {it["name"] for it in all_pos[0]["items"]} == {"Widget", "Gadget"}


# ── render_po_email ──────────────────────────────────────────────────────────────

def test_render_po_email_lists_items(fake_client, monkeypatch):
    monkeypatch.setattr("vula.api.tenants.get_config", lambda tid: {"display_name": "Off The Hook"})
    po = {"id": "abc12345-full-uuid", "items": [{"name": "Widget", "quantity": 20}]}
    subject, body = po_mod.render_po_email(TID, po)
    assert "Off The Hook" in subject
    assert "Widget x 20" in body
    assert "confirm pricing" in body.lower()


# ── send_purchase_order ──────────────────────────────────────────────────────────

def _seed_po(client, *, supplier_id, items=None):
    row = {"id": str(uuid.uuid4()), "tenant_id": TID, "supplier_id": supplier_id,
           "status": "draft", "items": items or [{"name": "Widget", "quantity": 20}],
           "total_cents": 0, "created_at": "2026-08-02T00:00:00Z"}
    client.store.setdefault("commerce_purchase_orders", []).append(row)
    return row


@pytest.mark.asyncio
async def test_send_purchase_order_fails_without_supplier_email(fake_client):
    _seed_supplier(fake_client, SUP_A, "Acme Supplies")  # no contact_email
    po = _seed_po(fake_client, supplier_id=SUP_A)
    result = await po_mod.send_purchase_order(TID, po["id"], "email")
    assert "error" in result
    assert "contact_email" in result["error"]


@pytest.mark.asyncio
async def test_send_purchase_order_email_success(fake_client, monkeypatch):
    fake_client.store["commerce_suppliers"] = [
        {"id": SUP_A, "tenant_id": TID, "name": "Acme", "contact_email": "acme@example.com", "contact_phone": None}]
    po = _seed_po(fake_client, supplier_id=SUP_A)

    monkeypatch.setattr("vula.email_imap.credentials.get_email_creds", lambda tid: {"email": "shop@example.com"})
    sent = {}

    async def _fake_send(creds, to, subject, body):
        sent["to"] = to
        sent["subject"] = subject
        return {"sent": True}
    monkeypatch.setattr("vula.email_imap.service.send", _fake_send)

    result = await po_mod.send_purchase_order(TID, po["id"], "email")
    assert result["ok"] is True
    assert result["sent_via"] == ["email"]
    assert sent["to"] == "acme@example.com"

    updated = [p for p in fake_client.store["commerce_purchase_orders"] if p["id"] == po["id"]][0]
    assert updated["status"] == "sent"
    assert updated["sent_channel"] == "email"
    assert updated["sent_at"]


@pytest.mark.asyncio
async def test_send_purchase_order_both_channels_partial_failure_still_marks_sent(fake_client, monkeypatch):
    fake_client.store["commerce_suppliers"] = [
        {"id": SUP_A, "tenant_id": TID, "name": "Acme", "contact_email": "acme@example.com",
         "contact_phone": "27821234567"}]
    po = _seed_po(fake_client, supplier_id=SUP_A)

    monkeypatch.setattr("vula.email_imap.credentials.get_email_creds", lambda tid: {"email": "shop@example.com"})

    async def _fake_send(creds, to, subject, body):
        return {"sent": True}
    monkeypatch.setattr("vula.email_imap.service.send", _fake_send)

    async def _fake_wa_template(*a, **kw):
        return False  # template doesn't exist / isn't approved
    monkeypatch.setattr("vula.api.whatsapp._send_wa_template", _fake_wa_template)

    result = await po_mod.send_purchase_order(TID, po["id"], "both")
    assert result["ok"] is True
    assert result["sent_via"] == ["email"]
    assert "warnings" in result and any("supplier_po_notice" in w for w in result["warnings"])

    updated = [p for p in fake_client.store["commerce_purchase_orders"] if p["id"] == po["id"]][0]
    assert updated["sent_channel"] == "email"


@pytest.mark.asyncio
async def test_send_purchase_order_all_channels_fail_returns_error_not_sent(fake_client, monkeypatch):
    fake_client.store["commerce_suppliers"] = [
        {"id": SUP_A, "tenant_id": TID, "name": "Acme", "contact_email": None, "contact_phone": None}]
    po = _seed_po(fake_client, supplier_id=SUP_A)

    result = await po_mod.send_purchase_order(TID, po["id"], "both")
    assert "error" in result
    updated = [p for p in fake_client.store["commerce_purchase_orders"] if p["id"] == po["id"]][0]
    assert updated["status"] == "draft"  # unchanged — never falsely marked sent
