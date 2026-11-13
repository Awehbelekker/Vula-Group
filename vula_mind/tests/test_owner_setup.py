"""Self-onboarding (12 Nov 2026): the owner sees what is left to do, with WhatsApp optional; sees
their plan and can pay; and Vula can send an invoice by email from the dashboard chat for a
business with no WhatsApp number."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills import commerce_admin as ca
from tests.fake_supabase import FakeSupabase
from vula.commerce import owner_setup, service

T = "solo-sam"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _checklist(**done):
    ids = ["created", "branding", "team", "whatsapp", "templates", "payments", "vat", "profile",
           "golive_test", "knowledge", "history", "first_invoice"]
    return {"steps": [{"id": i, "label": i, "done": bool(done.get(i)), "tab": "settings", "detail": "x"} for i in ids]}


# ── what is left to do ──────────────────────────────────────────────────────────────────────

def test_the_owner_sees_their_own_steps_in_plain_words_not_the_operators():
    v = owner_setup.owner_view(_checklist(created=True, branding=True))
    ids = [s["id"] for s in v["steps"]]
    assert ids == ["branding", "payments", "vat", "first_invoice", "profile", "knowledge", "team", "whatsapp"]
    assert not {"created", "golive_test", "history", "templates"} & set(ids)
    assert v["steps"][0]["label"] == "Add your logo and colour" and v["next"] == "payments"


def test_whatsapp_and_the_team_are_optional_so_an_invoicing_business_can_finish_without_them():
    v = owner_setup.owner_view(_checklist(branding=True, payments=True, vat=True, first_invoice=True,
                                          profile=True, knowledge=True))
    optional = {s["id"] for s in v["steps"] if s["optional"]}
    assert optional == {"team", "whatsapp"}
    assert v["complete"] is True and (v["done"], v["total"]) == (6, 6) and v["next"] is None
    assert v["whatsapp_connected"] is False
    assert "without one" in next(s["why"] for s in v["steps"] if s["id"] == "whatsapp")


def test_steps_this_business_does_not_have_are_left_out():
    checklist = {"steps": [s for s in _checklist()["steps"] if s["id"] not in ("first_invoice", "vat")]}
    v = owner_setup.owner_view(checklist)
    assert "first_invoice" not in [s["id"] for s in v["steps"]] and v["total"] == 4


# ── the plan ────────────────────────────────────────────────────────────────────────────────

def _plan_db(**billing):
    row = {"workspace_slug": T, "plan": "starter", "paid": False, "email": "sam@example.test",
           "contact_name": "Sam Nkosi", "trial_ends": (NOW + timedelta(days=9, hours=3)).isoformat(), **billing}
    return FakeSupabase({
        "vula_tenants": [row, {"workspace_slug": "someone-else", "plan": "business", "paid": True}],
        "vula_tenant_users": [{"id": 1, "tenant_id": T, "role": "owner"}, {"id": 2, "tenant_id": T, "role": "staff"},
                              {"id": 3, "tenant_id": "someone-else", "role": "owner"}],
        "vula_filed_documents": [{"id": i, "tenant_id": T} for i in range(7)],
    })


def _view(db, **kw):
    with patch.object(service, "_client", return_value=db), \
         patch("vula.api.tenants.get_config", return_value={"plan": "starter"}), \
         patch("vula.api.onboarding._payfast_url", return_value="https://pay.example.test/x") as link:
        return owner_setup.plan_view(T, now=NOW, **kw), link


def test_the_owner_sees_the_plan_the_trial_and_what_it_allows():
    v, _ = _view(_plan_db())
    assert (v["plan"], v["paid"], v["on_trial"], v["trial_over"]) == ("starter", False, True, False)
    assert v["trial_days_left"] == 10                               # 9 days and a bit: shown as 10 days left
    assert v["users"] == {"used": 2, "limit": 2} and v["documents"] == {"used": 7, "limit": 25}
    assert v["pay_url"] is None                                     # not asked for


def test_the_link_to_pay_is_only_built_when_allowed_and_only_while_unpaid():
    v, link = _view(_plan_db(), with_pay_link=True)
    assert v["pay_url"] == "https://pay.example.test/x"
    link.assert_called_once_with(T, "starter", "sam@example.test", "Sam Nkosi")
    v, link = _view(_plan_db(paid=True), with_pay_link=True)
    assert v["pay_url"] is None and v["on_trial"] is False
    link.assert_not_called()


def test_an_ended_trial_and_an_unreadable_count_are_shown_as_they_are():
    db = _plan_db(trial_ends=(NOW - timedelta(days=2)).isoformat())
    db.fail.add("vula_filed_documents")
    v, _ = _view(db)
    assert v["trial_over"] is True and v["trial_days_left"] == 0
    assert v["documents"] == {"used": None, "limit": 25}            # unknown, not zero


@pytest.mark.asyncio
async def test_a_restricted_member_sees_the_plan_but_gets_no_link_to_pay():
    from vula.api import me
    req = MagicMock()
    req.headers = {"authorization": "Bearer tok"}
    seen = {}

    def fake_view(tenant_id, with_pay_link=False, now=None):
        seen["with_pay_link"] = with_pay_link
        return {"plan": "starter"}

    with patch("vula.commerce.owner_setup.plan_view", fake_view):
        for scope, expected in (({"full": True, "access": []}, True), ({"full": False, "access": ["orders"]}, False),
                                (None, False)):
            with patch("vula.api.tenant_auth.member_scope", AsyncMock(return_value=scope)):
                await me.my_plan(T, req)
            assert seen["with_pay_link"] is expected, scope


# ── invoicing from the dashboard with no WhatsApp number ────────────────────────────────────

INV = {"id": "inv-1", "tenant_id": T, "invoice_number": "SAM-INV-00007", "customer_name": "Thabo Mokoena",
       "customer_phone": "", "customer_email": "thabo@example.test", "total_cents": 125000}


def _inv_db(**over):
    return FakeSupabase({"commerce_invoices": [{**INV, **over}]})


@pytest.mark.asyncio
async def test_an_invoice_with_only_an_email_address_is_previewed_then_emailed_and_read_back():
    db = _inv_db()

    async def fake_email(tenant_id, invoice_id, body=None):
        db.rows["commerce_invoices"][0].update({"sent_channel": "email", "sent_at": "now"})
        return {"sent": True, "to": "thabo@example.test", "status": "sent"}

    skill = ca.CommerceAdminSkill()
    with patch.object(service, "_client", return_value=db), patch.object(ca, "_has_whatsapp", return_value=False), \
         patch("vula.api.commerce.admin_send_invoice_email", fake_email), \
         patch("vula.api.commerce.admin_send_invoice_whatsapp", AsyncMock()) as wa:
        preview = await skill._send_invoice(T, "SAM-INV-00007")
        assert preview["preview"] is True and preview["via"] == "email" and preview["to"] == "thabo@example.test"
        assert preview["total"] == "R1,250.00"
        assert db.rows["commerce_invoices"][0].get("sent_channel") is None         # nothing sent on a preview
        out = await skill._send_invoice(T, "SAM-INV-00007", confirm=True)
    assert out == {"sent": True, "invoice_number": "SAM-INV-00007", "via": "email", "to": "thabo@example.test"}
    wa.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_business_with_whatsapp_still_sends_there_unless_told_otherwise():
    db = _inv_db(customer_phone="27821112222")
    skill = ca.CommerceAdminSkill()
    with patch.object(service, "_client", return_value=db), patch.object(ca, "_has_whatsapp", return_value=True):
        assert (await skill._send_invoice(T, "SAM-INV-00007"))["via"] == "WhatsApp"
        assert (await skill._send_invoice(T, "SAM-INV-00007", channel="email"))["via"] == "email"
    with patch.object(service, "_client", return_value=db), patch.object(ca, "_has_whatsapp", return_value=False):
        assert (await skill._send_invoice(T, "SAM-INV-00007"))["via"] == "email"    # no number: email


@pytest.mark.asyncio
async def test_missing_details_are_asked_for_and_a_failed_send_is_not_reported_as_sent():
    from fastapi import HTTPException
    skill = ca.CommerceAdminSkill()
    with patch.object(service, "_client", return_value=_inv_db(customer_email="")), \
         patch.object(ca, "_has_whatsapp", return_value=False):
        out = await skill._send_invoice(T, "SAM-INV-00007")
        assert out["status"] == "need_info" and "Where should I send it?" in out["message"]
        out = await skill._send_invoice(T, "SAM-INV-00007", channel="email")
        assert out["status"] == "need_info" and "email address" in out["message"]
        assert "error" in await skill._send_invoice(T, "NOPE-1")
    failing = AsyncMock(side_effect=HTTPException(status_code=503, detail="Email not sent — Resend is not configured."))
    with patch.object(service, "_client", return_value=_inv_db()), patch.object(ca, "_has_whatsapp", return_value=False), \
         patch("vula.api.commerce.admin_send_invoice_email", failing):
        out = await skill._send_invoice(T, "SAM-INV-00007", confirm=True)
    assert "sent" not in out and "was not sent" in out["error"] and "Resend is not configured" in out["error"]


def test_the_dashboard_chat_is_told_it_is_not_whatsapp_and_when_there_is_no_number():
    with patch.object(ca, "_has_whatsapp", return_value=True):
        note = ca._dashboard_note(T)
    assert "WEB DASHBOARD, not on WhatsApp" in note and "NOT connected" not in note
    with patch.object(ca, "_has_whatsapp", return_value=False):
        note = ca._dashboard_note(T)
    assert "has NOT connected a WhatsApp number" in note and "send by email" in note


def test_every_business_type_can_give_vula_documents():
    from vula.api.tenants import BUSINESS_TYPES
    for name, preset in BUSINESS_TYPES.items():
        assert "documents" in preset["modules"], name
    assert BUSINESS_TYPES["solo"]["modules"] == ["invoices", "payments", "finances", "documents", "crm", "reports"]
