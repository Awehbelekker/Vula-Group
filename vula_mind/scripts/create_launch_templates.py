"""scripts/create_launch_templates.py — submit the WhatsApp templates the platform needs.

Meta only allows free-form text within 24h of a customer's last message. Every proactive send
Vula makes — order alerts, delivery briefings, escalation nudges, supplier POs — is by nature
outside that window and fails with error 131047 unless it goes as a pre-approved template.
This submits the whole set in one go. Meta review is ~24-48h; check status with:

    python -c "import asyncio; from vula.commerce import wa_templates as t; \
               print(asyncio.run(t.list_templates('off-the-hook')))"

Run against production (needs SUPABASE + a connected WABA per tenant in vula_whatsapp_accounts):

    cd vula_mind && python scripts/create_launch_templates.py                 # OTH + shared
    cd vula_mind && python scripts/create_launch_templates.py --tenant <business> --set fieldops

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
    # vula/commerce/call_sheet.py's weekly sales-rep call sheet  ·  0 params
    # 2026-09-15: confirmed live this was referenced by real code but never actually submitted —
    # every WhatsApp-channel call sheet had been silently falling back to email-or-nothing.
    ("call_sheet_ready",
     "Your weekly call sheet is ready in Vula — check your dashboard or email for the details.",
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
    # 2026-09-15: Meta rejects a body that starts (or ends) with a variable — "Leading or
    # Trailing Params Not Allowed" (error_subcode 2388299), confirmed live submitting this one.
    ("oth_pending_project_nudge",
     "You have {{1}} filed document(s) waiting to be assigned to a project in Vula.",
     "UTILITY", None),
]

# vula/commerce/purchase_orders.py  ·  3 params: PO id, item count, total
SUPPLIER = [
    ("supplier_po_notice",
     "New purchase order {{1}} — {{2}} item(s), total {{3}}. Reply here to confirm or query it.",
     "UTILITY", None),
]

# vula/api/field_ops.py::FIELD_TEMPLATES  (contractor flow, any business with Field Ops)
# 24 Nov 2026: neutral names, and the bodies/placeholder counts the code actually sends (this list
# had drifted: 1 placeholder where the code sends 3). digg-demo's approved `digg_…` copies keep
# working until these are approved for it (field_ops.field_template).
FIELDOPS = [
    ("field_task_assigned",       # name, project, task
     "Hi {{1}}, you've been assigned a task on project {{2}}: {{3}}. Reply DONE when complete, "
     "or send photos as evidence. Reply with any questions and I'll help.",
     "UTILITY", None),
    ("field_task_chase",          # name, task
     "Hi {{1}}, just checking in on task {{2}}. Is it complete? Reply DONE to confirm, "
     "or send photos if sign-off is required.",
     "UTILITY", None),
    ("field_walkthrough_start",   # name, walkthrough
     "Hi {{1}}, please do a site walkthrough for {{2}}. Check Vula for the checklist and send "
     "photos for each item. Reply DONE when all photos are sent.",
     "UTILITY", None),
    ("field_walkthrough_approved",  # walkthrough
     "Walkthrough {{1}} has been approved. Great work!",
     "UTILITY", None),
    ("field_walkthrough_rejected",  # walkthrough, reason
     "Walkthrough {{1}} needs attention: {{2}}. Please address and re-submit.",
     "UTILITY", None),
    ("field_daily_tasks",         # name, count
     "Good morning {{1}}! You have {{2}} task(s) today. Check Vula for the full list. "
     "Reply DONE when each task is complete, or send photos as evidence.",
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
