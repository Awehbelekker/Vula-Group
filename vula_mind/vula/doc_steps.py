"""
vula/doc_steps.py — a fixed sequence of steps per document type, asked one question at a time.

Step 2 of the chat rework (Ian, 6 Oct: "lay out a job as fixed steps", built in Vula's own code,
no workflow framework). Each document type has an ordered list of steps. The next step is worked
out from the record itself (what is still unknown on the claim / bill / payment), so there is no
separate state to drift: answer one question and `next_question` returns the next one, or None
when the document is complete. Each question is recorded on the open-questions record
(vula/open_questions.py) against the exact document, so the reply lands on that document.

    bill     supplier confirmed (approval) → which project? (with "duplicate" offered)
    pop      which bill does it pay? → whose account paid it?
    expense  booked (confirm mode) → which project? → company card or own money?
             → what was it for? → odometer (petrol only)

Before this, a receipt asked project, paid-with and purpose all in one message and one reply had
to untangle them; a POP asked "does this pay STE00866?" and "is that your own money?" together.
The bill sequence was already chained (approvals._ask_project_next); it is listed here so every
sequence is in one place.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

SEQUENCES = {
    "bill": ("approval", "doc_project"),
    "pop": ("pop_match", "payer_account"),
    "expense": ("expense_confirm", "expense_project", "expense_paid_with", "expense_purpose",
                "expense_odometer"),
}


def _claim(tenant_id: str, claim_id: str) -> Optional[dict]:
    from vula.commerce import service
    try:
        rows = (service._client().table("commerce_expenses").select("*")
                .eq("tenant_id", tenant_id).eq("id", claim_id).limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as exc:
        logger.debug("claim lookup failed: %s", exc)
        return None


def expense_step(tenant_id: str, claim: dict) -> Optional[str]:
    """The first step of the expense sequence this claim still needs, or None when complete."""
    from vula.commerce import expenses
    if claim.get("status") == "unconfirmed":
        return "expense_confirm"
    if claim.get("project") is None and (claim["needs_project"] if "needs_project" in claim
                                         else bool(expenses.known_projects(tenant_id))):
        return "expense_project"
    if claim.get("paid_with") is None and expenses.list_cards(tenant_id):
        return "expense_paid_with"
    if not claim.get("purpose_category"):
        return "expense_purpose"
    if claim.get("purpose_category") == "petrol" and claim.get("odometer_km") is None:
        return "expense_odometer"
    return None


_EXPENSE_PROMPTS = {
    "expense_project": "📍 Which project/site is this for? Reply with the site name, or 'none'.",
    "expense_paid_with": "💳 Was this the *company card* or *your own money*? Reply 'company' or 'own'.",
    "expense_purpose": "🗂️ What was this for — fuel, a client visit/meal, or accommodation? "
                       "Reply with the category.",
    "expense_odometer": "🚗 What's the odometer reading at this fill-up? Reply with the number.",
}


def next_question(tenant_id: str, phone: str, claim_id: str, claim: Optional[dict] = None) -> Optional[str]:
    """Ask the next step this expense claim needs: record it as an open question about this
    claim and return the question text (the caller sends it). None when nothing is left —
    or when the claim is waiting on its Confirm tap, which is a button, not a question."""
    claim = claim or _claim(tenant_id, claim_id)
    if not claim or not phone:
        return None
    step = expense_step(tenant_id, claim)
    if step is None or step == "expense_confirm":
        return None
    from vula import open_questions
    if any(q.get("ref_id") == str(claim["id"]) for q in open_questions.open_for(tenant_id, phone)):
        return None              # a question about this receipt is still waiting for its answer
    amt = int(claim.get("amount_cents") or 0) / 100
    open_questions.ask(tenant_id, phone, step, claim["id"], f"{step}: R{amt:,.2f} receipt")
    if step == "expense_purpose":
        try:       # the older matcher still recognises the answer if the record is unavailable
            from vula.api.whatsapp import _note_purpose_prompt
            _note_purpose_prompt(phone)
        except Exception:
            pass
    return _EXPENSE_PROMPTS[step]
