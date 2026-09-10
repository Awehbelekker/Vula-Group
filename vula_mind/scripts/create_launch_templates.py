"""scripts/create_launch_templates.py — submit the WhatsApp templates the platform needs.

Meta only allows free-form text within 24h of a customer's last message. Every proactive send
Vula makes — order alerts, delivery briefings, escalation nudges, supplier POs — is by nature
outside that window and fails with error 131047 unless it goes as a pre-approved template.
This submits the whole set in one go. Meta review is ~24-48h; check status with:

    python -c "import asyncio; from vula.commerce import wa_templates as t; \
               print(asyncio.run(t.list_templates('off-the-hook')))"

Run against production (needs SUPABASE + a connected WABA per tenant in vula_whatsapp_accounts):

    cd vula_mind && python scripts/create_launch_templates.py                 # OTH + shared
    cd vula_mind && python scripts/create_launch_templates.py --tenant digg-demo --set fieldops

The body text and {{n}} placeholder counts here MUST match what the code sends — see the
inline call-site references. If you edit the wording in Meta's console instead, keep the
placeholder count identical.
"""
from __future__ import annotations

import argparse
import asyncio

# (name, body_text, category, buttons)  — placeholder count is derived from the body.
SHARED = [
    # settings.whatsapp_notify_template  ·  vula/api/whatsapp.py::_send_notify_template  ·  1 param
    ("vula_notification",
     "You have a new update from {{1}}. Open Vula to see the details.",
     "UTILITY", None),
    # vula/api/whatsapp.py::_maybe_owner_checkin_nudge  ·  2 params (first name, shop name)
    ("vula_owner_checkin_nudge",
     "Hi {{1}}, a few updates for {{2}} are waiting in Vula. Reply here to keep the chat open, "
     "or open the dashboard when you have a moment.",
     "UTILITY", None),
]

OTH_COMMERCE = [
    # server.py::_send_oth_delivery_briefing  ·  4 params: total, date, paid, to-collect
    ("oth_delivery_briefing",
     "Delivery briefing for {{2}}: {{1}} orders — {{3}} paid, {{4}} to collect on delivery. "
     "Open Vula for the full list.",
     "UTILITY", None),
    # server.py::_send_low_stock_alert  ·  1 param: count of low products
    ("oth_low_stock_alert",
     "{{1}} product(s) are low on stock. Open Vula to review and reorder.",
     "UTILITY", None),
    # server.py::_send_oth_sales_summary  ·  4 params: date, orders, paid, revenue
    ("oth_sales_summary",
     "Sales summary for {{1}}: {{2}} orders, {{3}} paid, R{{4}} in revenue. "
     "Open Vula for the breakdown.",
     "UTILITY", None),
    # server.py::_send_friday_catch_reminder  ·  0 params
    ("oth_friday_catch_reminder",
     "Time to set this week's specials in Vula so customers see them when they order.",
     "UTILITY", None),
    # server.py::_chase_unpaid_orders  ·  3 params: name, order id, amount
    ("oth_unpaid_order_chase",
     "Hi {{1}}, your order {{2}} for {{3}} is still awaiting payment. "
     "Reply here and we'll send you the payment link again.",
     "UTILITY", None),
    # server.py::_send_pending_project_nudge  ·  1 param: count
    ("oth_pending_project_nudge",
     "{{1}} filed document(s) are waiting to be assigned to a project in Vula.",
     "UTILITY", None),
]

# vula/commerce/purchase_orders.py  ·  3 params: PO id, item count, total
SUPPLIER = [
    ("supplier_po_notice",
     "New purchase order {{1}} — {{2}} item(s), total {{3}}. Reply here to confirm or query it.",
     "UTILITY", None),
]

# vula/api/field_ops.py  (DIGG contractor flow)
FIELDOPS = [
    ("digg_task_assigned",
     "New task assigned: {{1}}. Details in the message that follows. Reply DONE when complete.",
     "UTILITY", None),
    ("digg_daily_tasks",
     "Good morning — you have {{1}} task(s) due today on {{2}}. Reply for the list.",
     "UTILITY", None),
    ("digg_walkthrough_approved",
     "Your site walkthrough for {{1}} has been approved. Nothing further needed.",
     "UTILITY", None),
    ("digg_walkthrough_rejected",
     "Your site walkthrough for {{1}} needs attention: {{2}}. Please review and resubmit.",
     "UTILITY", None),
]

SETS = {
    "shared": SHARED,
    "commerce": SHARED + OTH_COMMERCE + SUPPLIER,
    "fieldops": FIELDOPS,
}


async def _run(tenant: str, which: str) -> None:
    from vula.commerce import wa_templates

    templates = SETS.get(which)
    if templates is None:
        raise SystemExit(f"unknown set {which!r} — choose from {list(SETS)}")

    print(f"Submitting {len(templates)} template(s) for tenant {tenant!r}\n")
    for name, body, category, buttons in templates:
        res = await wa_templates.create_template(
            tenant, name, body, category=category, buttons=buttons,
            created_by="create_launch_templates.py",
        )
        if res.get("error"):
            print(f"  ✗ {name:28s} {res['error']}")
        else:
            print(f"  ✓ {name:28s} {res.get('status', 'PENDING')}  (meta_id={res.get('meta_id')})")
    print("\nMeta review takes ~24-48h. Once a template shows APPROVED, set the matching env var")
    print("if it has one (e.g. WHATSAPP_NOTIFY_TEMPLATE=vula_notification) and `railway up`.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", default="off-the-hook",
                    help="tenant slug whose connected WABA to submit under (default: off-the-hook)")
    ap.add_argument("--set", dest="which", default="commerce", choices=list(SETS),
                    help="which template set to submit (default: commerce = shared + OTH + supplier)")
    args = ap.parse_args()
    asyncio.run(_run(args.tenant, args.which))


if __name__ == "__main__":
    main()
