"""
core/skills/draft_admin.py — draft business letters/proposals over WhatsApp (text or voice note),
onto the tenant's branded letterhead, delivered back as a WhatsApp document — and optionally
saved to the tenant's connected Google Drive.

    "draft a fee proposal for the Bokaap job, R85,000, and save it to my drive"
        → draft_letter(document_type="fee_proposal", ..., save_to_drive=True)
    "write a letter of appointment for the contractor on site 12"
        → draft_letter(document_type="appointment_letter", ...)

Reuses vula/api/draft.py's LLM generation (grounded in the tenant's own KB + shared SA
construction standards) and vula/commerce/pdf.py's branded-letterhead renderer — the same
branding a tenant's invoices already use. Delivery is WhatsApp-document by default; Drive
save is opt-in and silently skipped (with a note in the reply) if Google isn't connected.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from core.llm_router import resolve_generation_route
from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble

logger = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 3

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "draft_letter",
        "description": (
            "Draft a professional business letter/proposal, put it on the business's branded "
            "letterhead as a PDF, and send it back as a WhatsApp document. Optionally also save "
            "it to the user's connected Google Drive."
        ),
        "parameters": {"type": "object", "properties": {
            "document_type": {"type": "string", "enum": [
                "fee_proposal", "scope_of_works", "appointment_letter", "site_meeting_minutes",
                "tender_invitation", "project_programme", "payment_certificate"],
                "description": "fee_proposal | scope_of_works | appointment_letter | "
                               "site_meeting_minutes | tender_invitation | project_programme | "
                               "payment_certificate"},
            "brief": {"type": "string", "description": "What the letter should say — the fuller, the better."},
            "project_name": {"type": "string"},
            "client_name": {"type": "string"},
            "recipient": {"type": "string", "description": "Who it's addressed to (name/address block)."},
            "save_to_drive": {"type": "boolean", "description": "Also save a copy to Google Drive."},
            "project_value_zar": {"type": "number",
                "description": "fee_proposal only: project/construction value in ZAR, if known."},
            "floor_area_m2": {"type": "number",
                "description": "fee_proposal only: floor area in square metres, if known."},
            "work_stages": {"type": "string",
                "description": "fee_proposal only: which SACAP work stage(s) this covers "
                               "(e.g. 'concept and documentation'), if known."},
            "fee_basis": {"type": "string",
                "description": "fee_proposal only: how the fee is charged — a percentage of "
                               "construction cost, an hourly rate, or a fixed fee — if known."},
        }, "required": ["document_type", "brief"]}}},
]
_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}

# fee_proposal completeness gate (2026-07-29) — draft_letter used to silently draft with
# "[PLACEHOLDER]" for whatever wasn't given, an unenforced soft prompt instruction. Never guess
# these four; ask instead. Checks the structured args first, falls back to scanning the free-text
# brief for the same four signals (a rand amount, an m² figure, a recognizable stage name, a
# %/hourly/fixed-fee mention) since the orchestrating model won't always populate the new fields.
_VALUE_RE = re.compile(r"r\s?\d[\d,]*(\.\d+)?|\d+\s?(million|mil\b|k\b)", re.IGNORECASE)
_AREA_RE = re.compile(r"\d+(\.\d+)?\s?(m2|m²|sqm|square met)", re.IGNORECASE)
_STAGE_RE = re.compile(
    r"\b(inception|concept|viability|design development|documentation|procurement|"
    r"construction admin|construction|close.?out|stage\s*[1-5]|sacap stage)\b", re.IGNORECASE)
_FEE_BASIS_RE = re.compile(
    r"(\d+(\.\d+)?\s?%|percent|per\s?cent|hourly|per\s?hour|fixed fee|lump sum)", re.IGNORECASE)


def _fee_proposal_gaps(args: Dict[str, Any]) -> List[str]:
    brief = args.get("brief") or ""
    gaps: List[str] = []
    if not args.get("project_value_zar") and not _VALUE_RE.search(brief):
        gaps.append("the project value / construction cost")
    if not args.get("floor_area_m2") and not _AREA_RE.search(brief):
        gaps.append("the floor area (m²)")
    if not args.get("work_stages") and not _STAGE_RE.search(brief):
        gaps.append("which work stage(s) this covers (e.g. concept, documentation, construction)")
    if not args.get("fee_basis") and not _FEE_BASIS_RE.search(brief):
        gaps.append("the fee basis (% of construction cost, hourly rate, or fixed fee)")
    return gaps


async def draft_letter(args: Dict[str, Any], tenant_id: str, phone: str) -> dict:
    """Generate a letter (grounded in the tenant's KB + shared standards), render it onto the
    tenant's branded letterhead, send it back as a WhatsApp document, and optionally upload to
    Drive. Standalone so both draft_admin (knowledge-mode tenants) and commerce_admin
    (commerce-mode tenant owners) can call the identical draft_letter tool."""
    from vula.api.draft import DOCUMENT_TYPES, _retrieve_context, _generate_document, _store
    import hashlib
    from datetime import datetime

    doc_type = args.get("document_type") or "fee_proposal"
    base_config = DOCUMENT_TYPES.get(doc_type)
    if not base_config:
        return {"error": f"unknown document_type '{doc_type}'. Valid: {list(DOCUMENT_TYPES)}"}
    brief = (args.get("brief") or "").strip()
    if not brief:
        return {"error": "brief is required"}

    if doc_type == "fee_proposal":
        gaps = _fee_proposal_gaps(args)
        if gaps:
            return {
                "status": "need_info",
                "missing": gaps,
                "message": "Before I draft this, I still need: " + "; ".join(gaps) + ".",
            }

    # This path renders onto the tenant's REAL letterhead (render_letter_pdf) — drop the
    # generated placeholder header/letterhead section so it isn't duplicated under the
    # actual logo/address, and tell the model not to invent one either.
    doc_config = dict(base_config)
    doc_config["output_sections"] = [s for s in base_config["output_sections"]
                                     if s not in ("header", "letterhead", "certificate_header")]
    doc_config["system_prompt"] = base_config["system_prompt"] + (
        "\nDo NOT include a letterhead, company header, [placeholder] logo/address block, date "
        "line, recipient/'To' block, or subject line — all of those are placed automatically "
        "above your content on the business's real letterhead. Start directly with the first "
        "substantive section (e.g. the opening paragraph or project description).")

    context, sources = await _retrieve_context(tenant_id, brief, doc_type, None)
    content, model_used = await _generate_document(
        doc_config=doc_config, brief=brief, context=context,
        project_name=args.get("project_name"), client_name=args.get("client_name"),
        project_value=args.get("project_value_zar"), output_format="markdown",
    )
    # Output-side safety net: [PLACEHOLDER] is the model's own escape hatch for values it
    # wasn't given (vula/api/draft.py's generation prompt) — the completeness gate above should
    # catch the fee_proposal case, but this covers every other document_type and any gap the
    # gate's regex heuristics miss. Never silently ship a client-ready PDF with invented-looking
    # gaps unflagged.
    has_placeholders = "[PLACEHOLDER]" in content
    word_count = len(content.split())
    draft_id = hashlib.md5(
        f"{tenant_id}:{doc_type}:{brief[:50]}:{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:16]
    _store.save(tenant_id=tenant_id, draft_id=draft_id, doc_type=doc_type, brief=brief,
               content=content, word_count=word_count, model=model_used, sources=sources)

    from vula.commerce.pdf import render_letter_pdf, merge_branding
    from vula.commerce import service as commerce_service
    settings_row = await commerce_service.get_invoice_settings(tenant_id)
    branding = merge_branding(tenant_id, settings_row)
    pdf_bytes = render_letter_pdf(
        tenant_id=tenant_id, body_markdown=content, doc_label=doc_config["label"],
        recipient=args.get("recipient"), subject=args.get("project_name"),
        tenant_profile=branding,
    )

    filename = f"{doc_config['label'].replace(' ', '_')}.pdf"
    result: Dict[str, Any] = {"draft_id": draft_id, "document_type": doc_type,
                              "word_count": word_count, "has_placeholders": has_placeholders}

    sent = False
    if phone:
        from vula.api.whatsapp import _send_invoice_document
        sent = await _send_invoice_document(
            phone, pdf_bytes, filename, caption=doc_config["label"], tenant_id=tenant_id,
        )
    result["sent_via_whatsapp"] = sent

    if args.get("save_to_drive"):
        try:
            from vula.google import service as google_service
            uploaded = await google_service.drive_upload(tenant_id, filename, "application/pdf", pdf_bytes)
            result["drive"] = {"saved": True, "link": uploaded.get("webViewLink")}
        except Exception as exc:
            result["drive"] = {"saved": False, "reason": "Google not connected" if
                               type(exc).__name__ == "GoogleNotConnected" else str(exc)}

    return result


class DraftAdminSkill(BaseSkill):
    name = "draft_admin"
    description = "Draft letters/proposals onto the business's letterhead and deliver as a WhatsApp PDF."

    async def run(self, inp: SkillInput) -> SkillOutput:
        phone = inp.metadata.get("customer_phone") or ""
        try:
            answer = await self._loop(inp.conversation_history, inp.question, inp.tenant_id, phone)
            return SkillOutput(answer=answer or "Done.", skill_name=self.name, confidence=0.8)
        except Exception as exc:
            logger.warning("draft_admin failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

    def _system(self) -> str:
        return ("You are Vula, drafting professional business letters for a South African "
                "construction/professional-services business.\n\n" + behaviour_preamble() +
                "\n- Call draft_letter with a BRIEF that captures everything the user told you "
                "(project, amounts, dates, scope) — the drafting model only sees what you pass in "
                "brief/project_name/client_name, not this conversation. For a fee_proposal, also "
                "pass project_value_zar/floor_area_m2/work_stages/fee_basis whenever the user has "
                "given you any of them.\n"
                "- If draft_letter returns status:'need_info', do NOT retry blindly — ask the user "
                "for exactly the items listed in 'missing', in one short message, then call "
                "draft_letter again once they've answered.\n"
                "- If draft_letter's result has has_placeholders:true, tell the user the draft has "
                "some values it couldn't fill in and to double-check the [PLACEHOLDER] markers "
                "before sending it on — don't present it as complete.\n"
                "- After drafting, tell the user it's been sent as a WhatsApp document (and, if "
                "requested, saved to Drive) — never claim you emailed it unless a send actually "
                "happened.\nKeep replies short and WhatsApp-friendly.")

    async def _loop(self, history: str, question: str, tenant_id: str, phone: str) -> str:
        import litellm
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system()}]
        if history:
            messages.append({"role": "user", "content": f"(Conversation so far)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(model=model, messages=messages, tools=TOOL_SPECS,
                tool_choice="auto", temperature=0.2, max_tokens=800, api_key=api_key, api_base=api_base)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                inline = self._inline(msg.content or "")
                if inline:
                    name, args = inline
                    result = await self._dispatch(name, args, tenant_id, phone)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content":
                        f"[{name} returned]: {json.dumps(result, default=str)[:1500]}\n"
                        "Reply to the user in short plain language. No JSON."})
                    continue
                return (msg.content or "").strip()
            messages.append({"role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls]})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = await self._dispatch(tc.function.name, args, tenant_id, phone)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                                 "content": json.dumps(result, default=str)[:1800]})

        resp = await litellm.acompletion(model=model, messages=messages, temperature=0.2,
            max_tokens=500, api_key=api_key, api_base=api_base)
        return (resp.choices[0].message.content or "").strip()

    def _inline(self, content: str):
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        name = obj.get("function") or obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
        return (name, args) if name in _TOOL_NAMES and isinstance(args, dict) else None

    async def _dispatch(self, name: str, args: Dict[str, Any], tenant_id: str, phone: str) -> Any:
        if name != "draft_letter":
            return {"error": f"unknown tool {name}"}
        try:
            return await draft_letter(args, tenant_id, phone)
        except Exception as exc:
            logger.warning("draft_letter failed: %s", exc)
            return {"error": str(exc)}
