"""
core/skills/base.py — Base class for all Vula skills.

Every skill is an async callable that:
  - Receives a SkillInput (question + context + tenant_id)
  - Returns a SkillOutput (answer + confidence + sources + latency)

Skills are registered in core/skills/registry.json and loaded
dynamically by the HRM orchestrator.
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Vula AI behaviour policy ("the Soul") ────────────────────────────────────
# Composed preamble prepended to every answering skill so replies stay grounded,
# honest, ethical, and focused on what the user is actually asking.

CONVERSATION_RULES = (
    "How to respond:\n"
    "- Reply in the SAME language the person is using. South Africans write in English, "
    "Afrikaans, isiZulu, isiXhosa, Sesotho and more — mirror their language naturally; if unsure, "
    "use English. MIRROR only: never open with a greeting or phrase in a language this person "
    "hasn't used themselves. Real incident (2026-09-01): an off-the-hook customer whose whole "
    "conversation was in English got a reply starting 'Sawubona!'. Sprinkling a language they "
    "don't speak reads as a bot, not as local warmth.\n"
    "- Use the facts already established earlier in THIS conversation. If the user "
    "corrected or updated a value (e.g. an occupancy, dimension, or number), use the "
    "LATEST value and never revert to an earlier one.\n"
    "- Never invent missing facts (building type, occupancy, dimensions, project, "
    "budget, etc.). If an essential fact is missing to answer well, reply with ONE "
    "short clarifying question instead of assuming.\n"
    "- For any code/standard calculation (e.g. SANS 10400), name the clause you are "
    "applying and show the calculation from the user's stated numbers so it is checkable.\n"
    "- Stay consistent: do not contradict an earlier answer in this thread without "
    "briefly saying what changed.\n"
    "- Don't close every reply with a generic offer like 'is there anything else I can help "
    "you with?' — only ask a real follow-up question when there's an actual next step to "
    "take. A real person doesn't say this after every single message.\n"
    "- On WhatsApp, lead with a short summary (the headline number/total/what matters) before "
    "listing more than a few items — don't open with a raw table dump. Offer to send the full "
    "list as a document if there's a lot to show.\n"
    "- When sharing a link (checkout, invoice, storefront, anything), paste the raw URL on its "
    "own, e.g. https://example.com/pay-now — never markdown link syntax like [click here]"
    "(https://example.com/pay-now). WhatsApp doesn't render markdown; a formatted link shows as "
    "literal unclickable text instead of a real tappable link.\n"
)

ETHICS_RULES = (
    "Integrity:\n"
    "- You assist South African construction and business professionals. Never fabricate "
    "code clauses, legal facts, figures, rates, or citations. Accuracy beats sounding sure.\n"
    "- Flag life-safety and statutory items (fire, structural, NHBRC, zoning) clearly, and "
    "note the registered professional must verify and sign off — you assist, you do not "
    "certify or carry liability.\n"
    "- When you rely on a document or standard, cite it by file name. Only quote a "
    "specific clause/section NUMBER or an exact value (e.g. a width in mm, a ratio) if "
    "it actually appears in the provided context or the user gave it. Otherwise refer to "
    "the standard in general and say the exact clause/value must be confirmed against the "
    "official document — never present a clause number or figure from memory as fact.\n"
    "- Reference sources consistently: cite a standard by its reference (e.g. SANS 10400-T); "
    "cite a book or paper in Harvard style (Author, Year) when the author and year are known "
    "from the context. If you point a user to a reference you only have catalogued (not its "
    "full text), say it's a recommended reference, not a quote.\n"
)

HONESTY_RULES = (
    "Honesty:\n"
    "- If your provided context has nothing relevant, or you lack the facts to answer "
    "properly, say plainly that you don't have it and state exactly what you'd need. "
    "Do NOT fill the gap from unrelated past chats or general guesses.\n"
    "- Distinguish what the documents say from your own general reasoning, so the user "
    "knows how confident to be.\n"
    "- Never say something isn't available and then describe having found it. Real reply "
    "(2026-09-01): 'The cost of Creation 55 ref 0504 TWIST is not explicitly stated in the "
    "provided tool results. However, the results do provide information on various Gerflor "
    "products, including their prices.' That is useless to the person asking. If the tools "
    "returned relevant data, GIVE IT. If they returned near-misses, name what you did find "
    "('I have Creation 55 in 0503 and 0511 at Rx — not 0504') and say precisely what's "
    "missing. 'Not found' is only ever a whole answer when you genuinely found nothing.\n"
)

REASONING_RULES = (
    "Working:\n"
    "- For calculations or multi-step questions, reason it through step by step first, "
    "then give the answer and show the key working (formula, clause, the numbers used) so "
    "it can be checked. Don't dump raw chain-of-thought — show the clean working only.\n"
)

# 2026-08-08: generalized from commerce_admin.py's local `_GUARDRAILS`, added after a real
# WhatsApp-transcript review found an off-topic non-answer to a how-to question, a leaked
# internal tool name, and a hallucinated "exported to Xero" success claim with no tool call
# behind it. Investigating further found every OTHER tool-calling skill had the same gaps —
# none of them existed centrally anywhere. Opt-in via `agentic=True` (not every behaviour_
# preamble() caller has a tool-calling loop these rules make sense for).
AGENTIC_RULES = (
    "Working with tools:\n"
    "- If the message is a how-to/procedural question (e.g. 'how do I...', 'where do I...') "
    "rather than a request for data or an action: if you have a tool for looking up this "
    "business's own knowledge base or reference material, call it first — a real citation "
    "beats a guess. Only answer directly from general knowledge, in plain text, once that tool "
    "comes back with nothing relevant (or you have no such tool at all) — never call a tool "
    "just to have something to say.\n"
    "- If the message doesn't clearly map to any tool or data request, ask a short clarifying "
    "question instead of guessing the closest-sounding tool.\n"
    "- If you have a calculate tool, use it for EVERY sum — areas, quantities, line totals, VAT, "
    "discounts, margins — even one that looks trivial, and use its result verbatim. Never do "
    "arithmetic in your head: a quote with a wrong total costs someone real money.\n"
    "- If you have a remember_rule tool and the owner tells you how something should be handled "
    "from now on — a pricing policy, a discount, who signs off on what — SAVE IT and confirm "
    "what you saved, before answering. Real incident (2026-08-28): an owner dictated a full "
    "pricing policy and got 'I was unable to find the correct pricing structure' back; three "
    "days later the same question got the same empty answer, because nothing was kept. Being "
    "told something is not the same as being asked something.\n"
    "- Never mention internal tool/function names in a reply — describe what you did or found "
    "in plain business language.\n"
    "- Never say an action (exported, uploaded, sent, synced, created) succeeded unless a tool "
    "call actually performed it. If no tool exists for what's being asked, say so plainly "
    "instead of describing it as done.\n"
    "- If a tool returns status:'need_info', do NOT retry blindly — ask the user for exactly "
    "the items listed in 'missing', in one short message, then call it again once they've "
    "answered.\n"
    "- Never claim a retry, issue, or problem happened unless a tool call actually returned an "
    "error — a normal preview-then-wait-for-confirmation step is success, not something to "
    "narrate as broken. A clear short affirmative to the preview you just showed ('yes', "
    "'confirm', 'proceed', 'go ahead') is enough to act on the very first reply — don't show "
    "the same preview again and ask a second time.\n"
    "- If the user is rephrasing or repeating themselves — especially with visible frustration "
    "(ALL CAPS, 'that's not what I asked', 'you got it wrong', asking again shortly after your "
    "last reply) — do NOT assume your previous interpretation of what they wanted was correct. "
    "Re-read their CURRENT message on its own merits and pick the tool that actually fits it, "
    "even if that's different from the tool you used last turn. Never call the same tool again "
    "and repeat the same answer just because it matches what you said before — if you're not "
    "sure what they mean now, ask a short clarifying question instead of guessing again.\n"
)


def caller_block(caller_name: str = "", caller_role: str = "") -> str:
    """One line telling the model who it is actually speaking to, when that person is the
    tenant's own owner/manager/staff rather than a customer. Returns "" for an unknown caller
    or a plain customer, so every customer-facing path is unchanged.

    2026-09-17, confirmed live on DIGG: nothing on the knowledge/RAG path ever told the model
    who the sender was (its metadata literally called her `customer_phone`), and the chat
    history it read back labelled every one of her turns "Client:". So Judy — the practice
    owner — was handled as an outside client: her request to group her own supplier invoices
    got "Let me check with the team and get right back to you 🙏", her general fireplace
    question was answered out of a *client's* HOA guide, and the correction she took the
    trouble to research was recited back to her as if she'd asked it.

    The commerce-admin path already resolved caller_name/caller_role from vula_team_members and
    put it in its own system prompt; this centralizes the wording here (same precedent as
    `preferred_language` above) so every skill gets it by passing through what the caller
    already looked up, rather than each re-inventing it.
    """
    name, role = (caller_name or "").strip(), (caller_role or "").strip()
    if not name and not role:
        return ""
    who = f"{name} ({role})" if name and role else (name or role)
    return (
        f"You are talking to {who} — part of this business, NOT a customer or client of it. "
        f"Speak to them as the insider they are: their own records, projects, suppliers and "
        f"staff are 'ours', not 'the client's'. Never fob them off with customer-service "
        f"holding lines ('let me check with the team and come back to you') — on this side of "
        f"the business, checking is your job, so either answer, do the work, or say plainly "
        f"what you need from them.\n"
    )


def behaviour_preamble(persona: str = "", agentic: bool = False, preferred_language: str = "",
                       caller_name: str = "", caller_role: str = "") -> str:
    """Assemble the shared behaviour policy. `persona` (optional, per-tenant) sets the
    voice/style; the rest enforces integrity, honesty, reasoning, conversation, and
    untrusted-content rules. `agentic=True` also appends AGENTIC_RULES — pass this for any
    skill with its own tool-calling loop (TOOL_SPECS + tool_choice='auto').

    `caller_name`/`caller_role` (optional) — who is on the other end, when they're the tenant's
    own owner/manager/staff. See caller_block above for the real incident this exists for. Both
    empty (the default) means an unknown caller or an ordinary customer: no block is added and
    behaviour is exactly as it was before this existed.

    `preferred_language` (optional, e.g. "af") — 2026-08-17: CONVERSATION_RULES' generic
    "mirror their language" instruction wasn't reliable enough on its own (confirmed live: a
    real Afrikaans-speaking tenant owner kept getting English replies). commerce_assistant.py
    fixed this for itself months ago with a bespoke explicit-language block; centralized here so
    every skill gets the same fix by just passing through whatever language it already detected,
    instead of re-inventing it per skill. Pass "" (default) when nothing was detected — CONVERSATION_
    RULES' generic instruction still applies as the fallback, unchanged from before this existed."""
    from core.prompt_safety import UNTRUSTED_CONTENT_RULE
    head = (persona.strip() + "\n\n") if persona else ""
    lang_block = ""
    if preferred_language:
        try:
            from core.lang import language_name
            name = language_name(preferred_language)
            if name and name != "English":
                lang_block = (
                    f"This person usually writes in {name}. Reply in {name} by default, unless "
                    f"they clearly switch to another language in their latest message — then "
                    f"follow them.\n"
                )
        except Exception:
            pass
    parts = [ETHICS_RULES, HONESTY_RULES, REASONING_RULES, UNTRUSTED_CONTENT_RULE, CONVERSATION_RULES]
    if lang_block:
        parts.append(lang_block)
    who_block = caller_block(caller_name, caller_role)
    if who_block:
        parts.append(who_block)
    if agentic:
        parts.append(AGENTIC_RULES)
    return head + "\n".join(parts)


# 2026-08-24 chat-accuracy audit: commerce_admin.py/commerce_assistant.py/finance_admin.py all
# set (or will set) verification_policy="adversarial" but never populated SkillOutput.sources —
# core/verification.py::apply() only builds grounding context from sources whose type contains
# "kb", so the checker ran blind for every tool-calling skill regardless of policy. Every
# tool-calling agent loop should append one of these per dispatched tool call and pass the list
# through as `sources` — 900-char cap matches the existing KB-source truncation convention.
def tool_source(name: str, result: Any) -> Dict[str, Any]:
    text = json.dumps(result, default=str)
    return {"type": "tool", "name": name, "text": text[:900]}


# 2026-08-31: real incident, gerflor — a rep asked about vinyl roll pricing, the model called a
# free-text KB/web-search tool (lookup_business_info), and stated a specific price (R129.90/m²)
# that appeared nowhere in what that tool actually returned — a real price list was sitting in
# the KB and simply wasn't consulted correctly. The adversarial verifier (a fuzzy LLM pass) had
# the tool's real text as grounding context and STILL passed the fabricated figure as accepted —
# a confirmed false negative. This is a deterministic backstop for exactly that failure class:
# structured/DB-backed tools (sales_summary, stock_status, ...) are already ground truth by
# construction and don't need this: the risk is specifically a free-text KB/web-search tool
# whose prose the model has to extract a number FROM, which is exactly where invention creeps in.
#
# 2026-09-22: real incident, digg-demo — an owner asked for a jackhammer invoice breakdown; the
# model correctly called find_document/email_thread_summary AND lookup_business_info in the same
# turn, and the real invoice amount (sourced from find_document) got discarded anyway. Cause:
# `grounding_tools` here only ever listed lookup_business_info/competitor_check, so when a caller
# also passes find_document/email_thread_summary the check still only builds `relevant_text` from
# the (irrelevant, in this turn) lookup_business_info result — the genuinely-grounded price never
# had a chance to match. Callers should include any tool whose result the model might quote a
# price from, structured ones included: a structured tool's own numbers are trustworthy grounding
# text, and including it here doesn't newly expose it to false flags — it can only ever help a
# correct answer be found (or correctly catch the model mis-transcribing even a structured figure).
_PRICE_RE = re.compile(r"R\s?\d[\d,]*(?:\.\d{1,2})?")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _amount(s: str) -> Optional[float]:
    try:
        return round(float(s.replace(",", "")), 2)
    except ValueError:
        return None


def unverified_prices(answer: str, sources: List[Dict[str, Any]], grounding_tools: set) -> List[str]:
    """Any R-prefixed price stated in `answer` whose numeric value doesn't match (as a rounded
    float, so "92.0" and "92.00" agree) any number appearing anywhere in the combined text of
    `sources` whose tool name is in `grounding_tools`. Returns [] when none of those tools were
    even called this turn — nothing to check a structured-tool answer against, and nothing to
    falsely flag. Compares parsed amounts rather than raw digit substrings so a source number
    doesn't have to appear character-for-character the way the model chose to format it."""
    relevant_text = " ".join(s.get("text", "") for s in sources
                             if s.get("name") in grounding_tools and s.get("text"))
    if not relevant_text:
        return []
    source_amounts = {a for a in (_amount(n) for n in _NUM_RE.findall(relevant_text)) if a is not None}
    bad = []
    for m in _PRICE_RE.finditer(answer):
        amount = _amount(re.sub(r"[^\d.,]", "", m.group(0)))
        if amount is not None and amount not in source_amounts:
            bad.append(m.group(0))
    return bad


# Arithmetic stated in an answer, e.g. "11.8 x 18.2 = 215.56" or "214.76 × R198.00 = R42,522.48".
# Currency symbols, thousands separators and the various multiplication glyphs a model reaches
# for (x, ×, *) are all tolerated.
_ARITHMETIC_RE = re.compile(
    r"(?<![\d.])R?\s?(\d[\d\s,]*(?:\.\d+)?)\s*([x×*+\-/])\s*R?\s?(\d[\d\s,]*(?:\.\d+)?)\s*="
    r"\s*R?\s?(\d[\d\s,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


def _as_number(raw: str) -> Optional[float]:
    try:
        return float(re.sub(r"[\s,]", "", raw))
    except Exception:
        return None


def wrong_arithmetic(answer: str, tolerance: float = 0.02) -> List[Dict[str, Any]]:
    """Every "A op B = C" claim in `answer` where C is not actually A op B.

    2026-08-31, real incident on a live Gerflor quote: the model wrote "11.8 × 18.2 = 215.56"
    (correct: 214.76) and then "215.56 × 198.00 = R42,731.08" — a total that didn't even follow
    from its own wrong intermediate (the correct chain gives R42,522.48). Nothing checked it,
    so a wrong price went to a real customer.

    Prompt instructions demonstrably do not fix this — commerce_admin was already told to use
    tools and still did the sums in its head. This is the same deterministic-backstop pattern as
    unverified_prices() above: recompute what was actually claimed and compare. A tolerance of
    2 cents absorbs honest display rounding without letting a real error through.
    """
    bad: List[Dict[str, Any]] = []
    for m in _ARITHMETIC_RE.finditer(answer or ""):
        left, op, right, stated = (_as_number(m.group(1)), m.group(2),
                                   _as_number(m.group(3)), _as_number(m.group(4)))
        if left is None or right is None or stated is None:
            continue
        try:
            if op in ("x", "×", "*", "X"):
                actual = left * right
            elif op == "+":
                actual = left + right
            elif op == "-":
                actual = left - right
            elif op == "/":
                if right == 0:
                    continue
                actual = left / right
            else:
                continue
        except Exception:
            continue
        # Compare against the stated value at its own precision, so "= 214.76" isn't flagged
        # against 214.759999. Anything beyond the tolerance is a real arithmetic error.
        if abs(actual - stated) > tolerance:
            bad.append({"claim": m.group(0).strip(), "stated": stated,
                        "actual": round(actual, 2)})
    return bad


# Shared hard-decline guard: a question shaped like "what does MY invoice/BOQ/payment say" that
# no retrieved context or tool result can back up should be declined BEFORE generating an
# answer, not generated and hoped-to-be-caught by a prompt instruction. Added to reasoning.py
# 2026-08-18 after a confirmed real hallucination (a fabricated "R70,400 logged" claim with zero
# backing tool call); centralized here 2026-08-24 after the same class of gap was found
# unpatched in architecture_planning.py (which OWNS exactly these tenant-record-shaped questions
# per its orchestrator routing keywords) and the regex itself was found English-only despite
# Vula's explicit Afrikaans/isiZulu/isiXhosa/Sesotho promise (CONVERSATION_RULES above).
_TENANT_DATA_MARKERS = re.compile(
    r"\b("
    # English — reasoning.py's original list (2026-08-18), plus total/outstanding/account
    # (the original code comment promised "total" but the regex never actually included it)
    r"invoice|expenses?|receipt|boq|bill of quantities|project|order|payment|logged?|"
    r"created?|saved?|allocat\w*|owe|owing|balance|quote|quotation|supplier|paid|deposit|"
    r"total|outstanding|account|retention|provisional sum|practical completion|fees?|"
    r"contractor|subcontract\w*|contract|certificate|"
    # 2026-09-22 real incident (DIGG): "a breakdown on what has been spend at jackhammer" —
    # ungrammatical but real phrasing, matched no marker at all, so this looked like general
    # knowledge and got answered from an unrelated KB chunk instead of being routed to a skill
    # that could actually look the spend up (see HRMOrchestrator._match_skill's mailbox
    # fallback, which gates on this same function).
    r"spend\w*|spent|breakdown|"
    # Afrikaans
    r"faktuur|onkoste|kwitansie|projek|betaal|betaling|rekening|skuld|"
    # isiZulu
    r"inikwota|inkokhelo|i-akhawunti|isikweletu|"
    # isiXhosa
    r"iinvoyisi|intlawulo|ityala|"
    # Sesotho
    r"tefiso|akhaonto|sekoloto"
    r")\b", re.IGNORECASE)

# A "my/our/this project's" shape — distinguishes "what's a typical retention percentage on a
# JBCC contract" (general knowledge, fine to answer from training/parametric knowledge) from
# "what's the retention on OUR Riverside contract" (a specific record, must be backed by a
# real retrieved/tool-returned fact or declined). Only architecture_planning.py uses this second
# regex today — reasoning.py has zero tools/KB of its own for tenant records, so ANY match on
# _TENANT_DATA_MARKERS with empty context is enough for it to decline.
_POSSESSIVE_RE = re.compile(
    r"\b(my|our|ons|we|this project|hierdie projek|the client|die klant)\b", re.IGNORECASE)


def looks_like_tenant_data_question(text: str, require_possessive: bool = False) -> bool:
    """True if `text` is shaped like a question about the tenant's OWN records (an invoice,
    expense, BOQ, project, payment) rather than general knowledge. `require_possessive=True`
    (architecture_planning.py's narrower use) additionally requires a "my/our/this project's"
    marker, so a general "what's a typical X" question isn't declined just because 'project'
    or 'invoice' appears in it."""
    if not _TENANT_DATA_MARKERS.search(text or ""):
        return False
    if require_possessive:
        return bool(_POSSESSIVE_RE.search(text or ""))
    return True


# 2026-09-23, DIGG (knowledge-mode tenant, owner reaches skills via the HRM keyword router):
# "What materials did we buy from Jack Hammer?" matched commerce_assistant's "buy" (the CUSTOMER
# shopping skill) and "How much have we spent with Jack Hammer" matched finance_admin's "how much
# have we spent" (the ledger, which doesn't see filed-but-unbooked supplier invoices). Neither
# can reach find_document, the only tool that returns the full invoice list, server-side total
# and materials roll-up for a supplier. Deliberately narrow — it needs a supplier-shaped anchor
# ("spent WITH/AT X", "invoices FROM X", "all ... invoices", "materials we bought"), so a budget
# question ("how much have we spent ON Stage 3") stays with finance_admin, and a pricing
# question ("what does X charge") never matches.
_SUPPLIER_HISTORY_RE = re.compile(
    r"\b(spent|spend|spending|paid|pay|bought|buy|purchased)\s+(with|at|from)\b|"
    r"\b(expenses?|purchases?|spend|spending)\s+(from|with|at)\b|"
    r"\binvoices?\s+(from|by)\b|"
    r"\b(all|every)\s+(of\s+)?(the\s+|our\s+|my\s+)?([\w'-]+\s+){0,3}invoices?\b|"
    r"\b(what|which)\s+(materials?|items?|stuff|products?)\s+(have|has|did|were)\s+"
    r"(we|i|you)?\s*(been\s+)?(buy|bought|get|got|order|ordered|purchase|purchased)\b|"
    r"\b(summary|list|breakdown)\s+of\s+(the\s+|all\s+)?(our\s+)?materials?\b|"
    r"\bmaterials?\s+(from|bought|purchased|we\s+(bought|got|ordered))\b|"
    # 2026-09-23 real digg-demo phrasings that fell through to `reasoning`:
    # "See if you can find invoices gardening gardens area",
    # "For gardens handiman full list of spend and material".
    r"\b(find|search|show|get|pull|fetch|look\s+up)\b[^.?!]{0,30}?\binvoices?\b|"
    r"\b(list|summary|breakdown|total)\s+of\s+(the\s+|all\s+|our\s+)?"
    r"(spend|spending|purchases|materials?)\b",
    re.IGNORECASE)
# Invoice questions that are about what's OWED (receivables/payables status), not a supplier's
# history — those stay with finance_admin / commerce skills.
_NOT_SUPPLIER_HISTORY_RE = re.compile(
    r"\b(unpaid|outstanding|overdue|owe|owed|owing|due|create|make|draft|send|issue)\b",
    re.IGNORECASE)
_SUPPLIER_PRICING_RE = re.compile(
    r"\b(charge|charges|charging|price\s*list|pricing|quote\s+me|sell|sells|selling|"
    r"catalog(ue)?)\b", re.IGNORECASE)


def looks_like_supplier_history_question(text: str) -> bool:
    """True if `text` asks what the business has actually bought from / spent with / been
    invoiced by a supplier (totals or materials), as opposed to a budget, pricing or shopping
    question. See the incident note above."""
    t = text or ""
    return (bool(_SUPPLIER_HISTORY_RE.search(t)) and not _SUPPLIER_PRICING_RE.search(t)
            and not _NOT_SUPPLIER_HISTORY_RE.search(t))


async def format_kb_chunks(tenant_id: str, chunks: List[Dict[str, Any]]) -> str:
    """Join RAG chunks into a "[filename]: text" grounding block, the same shape reasoning.py/
    architecture_planning.py already built inline in three near-identical places — but now with
    each chunk's source document cross-referenced against vula_filed_documents (via
    vula.commerce.service.filed_amounts_by_filename) so a chunk whose document was ALSO filed
    normally with a real extracted amount carries that verified figure in the tag, e.g.
    "[invoice.pdf (filed: R92.00 — Gardens Handiman Centre)]: ...", instead of leaving the model
    to read a number off fuzzy chunk text. 2026-09-21: this is the generalisation of the same
    fix applied to commerce_admin's find_document tool — the R70,400 "logged" fabrication
    incident (see looks_like_tenant_data_question) is exactly the failure mode a verified figure
    in the context is meant to prevent. Centralised here rather than duplicated per skill, same
    precedent as caller_block()/behaviour_preamble()."""
    if not chunks:
        return ""
    try:
        from vula.commerce.service import filed_amounts_by_filename
        filed = await filed_amounts_by_filename(
            tenant_id, [c.get("filename") for c in chunks if c.get("filename")])
    except Exception:
        filed = {}
    lines = []
    for c in chunks:
        fname = c.get("filename") or "doc"
        extra = filed.get(fname)
        tag = fname
        if extra and extra.get("amount") is not None:
            party = f" — {extra['party']}" if extra.get("party") else ""
            tag = f"{fname} (filed: R{extra['amount']:.2f}{party})"
        lines.append(f"[{tag}]: {c.get('text','')[:900]}")
    return "\n\n".join(lines)


def need_info_message(result: Any) -> Optional[str]:
    """If a tool result is the shared {"status": "need_info", "message": ...} shape (used by
    commerce_admin's create_invoice, draft_admin's draft_letter, email_admin's send), return the
    message to ask the user — else None.

    2026-08-22: AGENTIC_RULES already told the model "don't retry need_info blindly, ask the
    user" — a real transcript showed that instruction get ignored: the SAME broken tool call was
    retried 3 times unchanged, burned the whole iteration budget, and the loop's final forced
    text-only pass then fabricated a full success claim (a real-looking invoice number, never
    actually created) rather than admitting nothing worked. A prompt-only instruction wasn't
    enough — every agentic loop should call this right after dispatching a tool and return
    immediately when it fires, rather than feeding need_info back in and hoping the model asks."""
    if isinstance(result, dict) and result.get("status") == "need_info":
        msg = result.get("message")
        if msg:
            return str(msg)
    return None


@dataclass
class SkillInput:
    question: str
    tenant_id: str
    context: str = ""
    conversation_history: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 1024      # generation cap — keep tight for WhatsApp (~500)
    top_k: int = 4              # KB chunks to retrieve


@dataclass
class SkillOutput:
    answer: str
    skill_name: str
    confidence: float = 1.0          # 0.0 – 1.0
    sources: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    error: Optional[str] = None
    # Set by the skill (deterministic self-check, e.g. calculations) or by the verification
    # hook (adversarial pass): {"verifier", "outcome", "escalated", "extra", ...}.
    verification: Optional[Dict[str, Any]] = None
    # 2026-08-14: a real product photo to send alongside `answer` on channels that support it
    # (WhatsApp) — set by commerce_assistant.py when a tool result (list_products, add_to_cart)
    # carries a real image_url. Optional and unused by most skills; None means text-only, same
    # as before this field existed.
    media_url: Optional[str] = None
    # 2026-08-25: a pending confirmation an owner needs to approve/reject via real WhatsApp
    # reply buttons rather than free text — {"id", "summary", "confirm_label", "cancel_label"}.
    # Set when a skill's tool call returned {"preview": True, ...} (see core.skills.commerce_
    # admin.ConfirmationRequired). Buttons remove the exact ambiguity ("yes"/"confirm"/"proceed"
    # misread, blind retries, an eventual fabricated success) confirmed in a real transcript.
    # None means no confirmation pending — reply normally with `answer`.
    confirm_request: Optional[Dict[str, Any]] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.answer)


class BaseSkill(ABC):
    name: str = "base"
    description: str = ""
    # Per-skill verification policy: "none" | "deterministic" | "adversarial".
    # Overridable per skill at runtime via VERIFICATION_POLICY_OVERRIDES (core/verification.py).
    verification_policy: str = "none"

    @abstractmethod
    async def run(self, inp: SkillInput) -> SkillOutput:
        """Execute the skill and return a SkillOutput."""

    async def __call__(self, inp: SkillInput) -> SkillOutput:
        started = time.monotonic()
        try:
            result = await self.run(inp)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            result.skill_name = self.name
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            return SkillOutput(
                answer="",
                skill_name=self.name,
                confidence=0.0,
                latency_ms=latency,
                error=str(exc),
            )
        # Verification hook — policy "none" is a strict no-op; a hook failure must never
        # break the answer (core/verification.py fails open and swallows its own errors).
        try:
            from core import verification as _verification
            await _verification.apply(self, inp, result)
        except Exception:
            pass
        return result


# ── Per-request skill state ───────────────────────────────────────────────────
# Skills are process-wide singletons (core/skills/loader.py::_SKILLS), so a plain `self._x = ...`
# set during run() is shared by every request in flight. 2026-09-25 review: finance_admin kept
# each turn's verified figures / sources on self, so two tenants answering at the same time
# could have one tenant's numbers "verify" (or leak as sources into) the other's reply.
# turn_local keeps the attribute syntax but stores the value per asyncio task (contextvars).
_TURN_STATE: ContextVar[Optional[dict]] = ContextVar("vula_skill_turn_state", default=None)


def begin_turn() -> None:
    """Give the current task its own state dict. Call at the top of run(): a task inherits its
    parent's context, so without this two sibling tasks would share the parent's dict object."""
    _TURN_STATE.set(dict(_TURN_STATE.get() or {}))


class turn_local:
    """Descriptor: a skill attribute whose value is private to the current asyncio task.
    Reading one that was never set this turn raises AttributeError, so hasattr()/getattr()
    defaults behave exactly as they did for a plain instance attribute."""

    def __set_name__(self, owner, name):
        self.key = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        state = _TURN_STATE.get()
        try:
            return state[(id(obj), self.key)]
        except (TypeError, KeyError):
            raise AttributeError(self.key) from None

    def __set__(self, obj, value):
        state = _TURN_STATE.get()
        if state is None:
            state = {}
            _TURN_STATE.set(state)
        state[(id(obj), self.key)] = value
