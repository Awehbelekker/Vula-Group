"""A new business's WhatsApp templates in one tap, and no fish-shop reminders for a basin maker
(2026-10-12, onboarding SeedleMe). Vula's scheduled jobs look their template up as
"<tenant>_<job>" — "seedleme_unpaid_order_chase" — and until now the only way to submit the set
was a script with Off the Hook's names in it."""
import re

import pytest

from vula.commerce import job_config, wa_templates

RETAIL = {"products", "orders", "payments", "invoices", "inbox", "team"}


def _names(ts):
    return [t["name"] for t in ts]


def test_a_shop_gets_its_own_names_for_the_jobs_it_runs():
    names = _names(wa_templates.standard_templates("seedleme", RETAIL, "retail"))
    assert {"seedleme_unpaid_order_chase", "seedleme_order_update", "seedleme_sales_summary",
            "seedleme_delivery_briefing", "seedleme_low_stock_alert", "vula_notification", "vula_owner_checkin_nudge",
            "supplier_po_notice"} == set(names)
    assert not any("friday" in n or "pending_project" in n for n in names)


def test_off_the_hook_keeps_its_approved_prefix():
    assert job_config.template_name("off-the-hook", "sales_summary") == "oth_sales_summary"
    assert job_config.template_name("kelp-boardbags", "sales_summary") == "kelp_boardbags_sales_summary"
    from vula.api import server
    assert server._commerce_template_name("off-the-hook", "unpaid_order_chase") == "oth_unpaid_order_chase"


def test_wording_matches_what_the_jobs_send_and_what_meta_accepts():
    expected = {"unpaid_order_chase": 3, "order_update": 3, "sales_summary": 4, "delivery_briefing": 4,
                "low_stock_alert": 1, "pending_project_nudge": 1}
    for suffix, body, _cat, _needs in wa_templates.STANDARD_JOB_TEMPLATES:
        assert wa_templates._placeholder_count(body) == expected[suffix], suffix
        # Meta refuses a body that starts or ends with a variable (error_subcode 2388299)
        assert not re.match(r"^\s*\{\{", body) and not re.search(r"\}\}\s*[.!]?\s*$", body), suffix


@pytest.mark.asyncio
async def test_only_names_not_already_on_the_account_are_submitted(monkeypatch):
    sent = []

    async def creds(tid):
        return {"waba_id": "1343999077678607", "token": "t"}

    async def on_account(c):
        return {"vula_notification", "vula_owner_checkin_nudge", "supplier_po_notice", "oth_sales_summary"}

    async def create(tid, name, body, **k):
        sent.append(name)
        return {"name": name, "status": "PENDING"}

    monkeypatch.setattr(wa_templates, "_waba_creds", creds)
    monkeypatch.setattr(wa_templates, "_waba_template_names", on_account)
    monkeypatch.setattr(wa_templates, "create_template", create)
    monkeypatch.setattr(wa_templates, "_promo_sample_image", lambda tid: "https://cdn.shopify.com/basin.jpg")
    monkeypatch.setattr("vula.api.tenants.get_config",
                        lambda tid, fresh=False: {"business_type": "retail", "modules": sorted(RETAIL)})
    res = await wa_templates.submit_standard("seedleme")
    assert set(res["submitted"]) == {"seedleme_unpaid_order_chase", "seedleme_order_update", "seedleme_sales_summary",
                                     "seedleme_delivery_briefing", "seedleme_low_stock_alert",
                                     "seedleme_shop_promo"}     # the picture promotion, with its photo
    assert set(res["already_there"]) == {"vula_notification", "vula_owner_checkin_nudge", "supplier_po_notice"}
    assert sent == res["submitted"]


@pytest.mark.asyncio
async def test_no_whatsapp_number_yet_says_what_to_do(monkeypatch):
    async def none(tid):
        return None
    monkeypatch.setattr(wa_templates, "_waba_creds", none)
    assert "Connect this business's WhatsApp number" in (await wa_templates.submit_standard("seedleme"))["error"]


def test_the_weekly_catch_reminder_is_only_on_by_default_for_food(monkeypatch):
    types = {"seedleme": "retail", "off-the-hook": "food"}
    monkeypatch.setattr("vula.api.tenants.get_config",
                        lambda tid, fresh=False: {"business_type": types.get(tid)})
    assert job_config.effective_config("seedleme", "friday_catch_reminder", None)["enabled"] is False
    assert job_config.effective_config("off-the-hook", "friday_catch_reminder", None)["enabled"] is True
    # an owner who switches it on in the Scheduling tab keeps it on
    assert job_config.effective_config("seedleme", "friday_catch_reminder", {"enabled": True})["enabled"] is True
    assert job_config.effective_config("seedleme", "sales_summary", None)["enabled"] is True
