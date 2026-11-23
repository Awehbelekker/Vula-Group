"""23 Nov 2026 full audit, things that went wrong without anyone being told: STOP said "Done" when
it didn't save; "what's the catch today?" got the supplier reply; renaming a storefront page wiped
it; adding a product dropped its category; Field Ops sign-off and chase failed every time; a 0%
recurring invoice came out at 15%. Made-up data."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.fake_supabase import FakeSupabase
from vula.api import commerce, server
from vula.api import whatsapp as wa
from vula.commerce import service

T = "deli"


@pytest.fixture
def db(monkeypatch):
    d = FakeSupabase({"commerce_consent": [], "commerce_categories": [
        {"tenant_id": T, "key": "baked_goods", "label": "Baked goods", "sort_order": 1},
        {"tenant_id": "other", "key": "secret_cat", "label": "Not ours", "sort_order": 1}]})
    monkeypatch.setattr(service, "_client", lambda: d)
    return d


# ── STOP is saved before "Done" ──────────────────────────────────────────────

def test_an_opt_out_is_read_back(db):
    assert commerce.record_opt_out(T, "082 555 0101") is True
    assert db.rows["commerce_consent"][0]["status"] == "opted_out"


def test_an_opt_out_that_fails_says_so(monkeypatch):
    def broken():
        raise RuntimeError("database down")
    monkeypatch.setattr(service, "_client", broken)
    assert commerce.record_opt_out(T, "0825550101") is False


@pytest.mark.asyncio
async def test_stop_never_says_done_when_it_did_not_save(monkeypatch):
    sent, told = [], []

    async def reply(phone, text, tid=None, **kw):
        sent.append(text)
        return True

    async def team(tid, event, msg, **kw):
        told.append(msg)
        return 1

    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr("vula.api.commerce.record_opt_out", lambda *a, **k: False)
    monkeypatch.setattr("vula.integrations.notify.notify_team", team)
    await wa._handle_opt_out("27825550101", T)
    assert sent and "Done" not in sent[0] and "STOP again" in sent[0]
    assert told and "didn't save" in told[0]


@pytest.mark.asyncio
async def test_stop_says_done_when_it_saved(monkeypatch):
    sent = []

    async def reply(phone, text, tid=None, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(wa, "_send_reply", reply)
    monkeypatch.setattr("vula.api.commerce.record_opt_out", lambda *a, **k: True)
    monkeypatch.setattr("vula.staff_notices.set_opt_out", lambda *a, **k: None)
    await wa._handle_opt_out("27825550101", T)
    assert sent and sent[0].startswith("✅ Done")


# ── who is a supplier ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,supplier", [
    ("what's the catch today?", False),
    ("any good catch this week", False),
    ("do you sell prawns?", False),
    ("what do you sell", False),
    ("I sell fresh line fish from Kalk Bay", True),
    ("we supply packaging to restaurants", True),
    ("I'd like to become your supplier", True),
    ("ek wil verskaf", True),
    ("I can supply you with linefish", True),
    ("we would like to sell to your shop", True),
])
def test_only_someone_offering_goods_is_a_supplier(text, supplier):
    customer_asking = __import__("re").compile(
        r"\byou\s+(?:guys\s+)?(?:sell|supply|have|stock)\b|\bsupply\s+(?:me|us)\b|\bsell\s+(?:me|us|to me)\b")
    t = text.lower()
    assert (not customer_asking.search(t) and bool(wa._SUPPLIER_OFFER_RE.search(t))) is supplier


# ── storefront pages ─────────────────────────────────────────────────────────

def test_renaming_a_page_keeps_its_content(db):
    db.rows["vula_pages"] = []
    c = TestClient(server.app)
    content = {"content": [{"type": "Hero", "props": {"title": "Fresh bread daily"}}], "root": {}}
    assert c.put(f"/v1/commerce/{T}/admin/pages/home", json={
        "title": "Home", "status": "published", "puck_data": content, "seo": {"title": "Deli"}}).status_code == 200
    assert c.put(f"/v1/commerce/{T}/admin/pages/home", json={"title": "Welcome", "status": "published"}).status_code == 200
    page = [p for p in db.rows["vula_pages"] if p["slug"] == "home"][0]
    assert page["title"] == "Welcome" and page["puck_data"] == content and page["seo"] == {"title": "Deli"}


# ── products keep their category ─────────────────────────────────────────────

@pytest.mark.parametrize("given,kept", [
    ("baked_goods", "baked_goods"),      # the business's own
    ("fresh_fish", "fresh_fish"),        # built in
    ("secret_cat", "extras"),            # another business's
    ("other", "extras"),                 # unknown (WhatsApp's old default)
    ("", "extras"),
])
def test_a_new_product_keeps_its_own_category(db, given, kept):
    assert commerce._known_category(T, given) == kept


# ── Field Ops sign-off ───────────────────────────────────────────────────────

class _FieldDb:
    def __init__(self, status="awaiting_sign_off"):
        self.task = SimpleNamespace(id="t1", tenant_id=T, title="Tile the bathroom", status=status,
                                    assigned_to="c1", project_id="p1")
        self.signed = None

    def get_task(self, task_id):
        return self.task if task_id == "t1" else None

    def record_sign_off(self, task_id, who, status, notes=""):
        self.signed = SimpleNamespace(status=status, approver_phone=who, notes=notes, approved_at="now")
        self.task.status = "complete" if status == "approved" else "rejected"

    def get_sign_off(self, task_id):
        return self.signed

    def get_contractor(self, cid):
        return SimpleNamespace(phone="27825550190", tenant_id=T, name="Sipho")


@pytest.mark.parametrize("status,body,code", [
    ("awaiting_sign_off", {"decision": "approved"}, 200),
    ("awaiting_sign_off", {"decision": "rejected"}, 400),              # say what needs fixing
    ("awaiting_sign_off", {"decision": "rejected", "notes": "Grout is cracked"}, 200),
    ("in_progress", {"decision": "approved"}, 409),
    ("awaiting_sign_off", {"decision": "maybe"}, 400),
])
def test_a_task_can_be_signed_off_from_the_dashboard(monkeypatch, status, body, code):
    from vula.api import field_ops
    fdb = _FieldDb(status)
    monkeypatch.setattr(field_ops, "get_field_ops_db", lambda: fdb)
    monkeypatch.setattr(field_ops, "_send_reply", AsyncMock(return_value=True))
    r = TestClient(server.app).post("/v1/field/task/t1/sign-off", json=body)
    assert r.status_code == code, r.text
    if code == 200:
        assert fdb.signed.status == body["decision"] and r.json()["whatsapp_sent"] is True


def test_a_chase_names_its_task_in_the_path(monkeypatch):
    from vula.api import field_ops
    fdb = _FieldDb("in_progress")
    monkeypatch.setattr(field_ops, "get_field_ops_db", lambda: fdb)
    monkeypatch.setattr(field_ops, "_send_wa_template", AsyncMock(return_value=True))
    r = TestClient(server.app).post("/v1/field/task/t1/complete-request", json={})
    assert r.status_code == 200 and r.json()["whatsapp_sent"] is True
