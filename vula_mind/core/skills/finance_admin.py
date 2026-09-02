"""
core/skills/finance_admin.py — answer money questions from the project finance ledger.

    "how much have we spent on HPC?"          → project_spend
    "what's left on the Bokaap budget?"        → budget_status
    "money in vs out this month?"              → money_in_out
    "who is account 805515?"                   → supplier_lookup

Reads vula_project_finances + vula_project_budgets (populated by filed invoices/payments).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from core.llm_router import resolve_generation_route, looks_degenerate, substitute_if_degenerate
from core.prompt_safety import fence
from core.skills.base import (
    BaseSkill, SkillInput, SkillOutput, behaviour_preamble, tool_source,
)

logger = logging.getLogger(__name__)
MAX_TOOL_ITERATIONS = 3

TOOL_SPECS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "project_spend", "description": "Total money in/out (and recent items) for a project.",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}}},
    {"type": "function", "function": {
        "name": "budget_status", "description": "Budget vs actual spend (remaining) for a project, or all projects.",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "money_in_out", "description": "Totals of money in vs out across projects, optionally for a period.",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["this_month", "last_30", "all"]}}}}},
    {"type": "function", "function": {
        "name": "supplier_lookup",
        "description": "Find a supplier/beneficiary by bank account number or name; shows their payments + projects.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "lookup_finance_knowledge",
        "description": ("Look up GENERAL finance/business knowledge (not this tenant's own "
                         "figures) — e.g. 'what's a typical professional fee percentage', "
                        "'what's a healthy profit margin', 'how does VAT work', pricing/margin "
                        "guidance. Use this instead of declining when the question is a general "
                        "how-does-this-work question rather than a request for this business's "
                        "own ledger data."),
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]
_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


def _is_empty_ledger_result(result: dict) -> bool:
    """True when a finance tool succeeded but the ledger simply holds nothing.

    2026-09-02: the not-found guard only recognised an `error` or `found: False`. A result like
    {"period": "all", "money_in": 0, "money_out": 0, "net": 0, "transactions": 0} is a
    SUCCESSFUL call over an empty ledger, so the guard never fired — and instead of one clean
    "nothing on file" answer the model rambled about its own tool calls and pasted raw tool JSON
    to the user. Reproduced on off-the-hook, whose project-finance ledger is genuinely empty
    (its money lives in the commerce tables), across three different real questions:

        "Sawubona! I couldn't find the amount spent at suppliers this month. The tool calls
         returned the following results: * Tool call 1: {"period": "all", "money_in": 0, ...}"

    `transactions == 0` is the load-bearing signal: a real zero with transactions behind it
    ("you're owed R0" because everything is paid) is a genuine answer and must NOT be treated as
    missing data.
    """
    if not isinstance(result, dict):
        return False
    if "transactions" not in result:
        return False
    try:
        if int(result.get("transactions") or 0) != 0:
            return False
    except (TypeError, ValueError):
        return False
    money_keys = ("money_in", "money_out", "net", "total", "spend", "amount")
    present = [k for k in money_keys if k in result]
    if not present:
        return False
    try:
        return all(float(result.get(k) or 0) == 0 for k in present)
    except (TypeError, ValueError):
        return False


class FinanceAdminSkill(BaseSkill):
    name = "finance_admin"
    description = "Answer money/budget/supplier questions from the project finance ledger."
    # 2026-08 accuracy audit: pure money-reporting with zero adversarial verification — this
    # skill's whole job is stating figures, and the anchor check (_verify_answer above) only
    # catches a number that doesn't match ANY tool result, not a subtler misreport (right
    # number, wrong project; right figure, wrong period). VRL's checker-framed second pass
    # (core/verification.py) catches that class of defect; wired into every skill's call path
    # already, just never turned on here.
    verification_policy = "adversarial"

    async def run(self, inp: SkillInput) -> SkillOutput:
        self._verified: List[float] = []  # every numeric value seen in a tool result this turn
        self._sources: List[Dict[str, Any]] = []
        self._any_tool_dispatched = False
        self._all_not_found = True
        lang = inp.metadata.get("preferred_language", "")
        try:
            answer = await self._loop(inp.conversation_history, inp.question, inp.tenant_id, lang)
        except Exception as exc:
            logger.warning("finance_admin failed: %s", exc)
            return SkillOutput(answer="", skill_name=self.name, confidence=0.0, error=str(exc))

        # 2026-08-24: every dispatched tool this turn came back not-found — nothing to anchor a
        # figure against, so don't let the model's free text assert one. Checked BEFORE the
        # generic "empty answer" fallback below: an exhausted tool-budget loop where every tool
        # came back empty often DOES end up with an empty final answer too, and that fallback
        # used to unconditionally report confidence=0.8 — misleadingly high for exactly the case
        # where nothing was actually found.
        if self._any_tool_dispatched and self._all_not_found:
            return SkillOutput(
                answer=("I couldn't find any financial records matching that — no invoices or "
                        "payments on file for it. Could you check the name/reference, or tell me "
                        "more so I can look again?"),
                skill_name=self.name, confidence=0.3, sources=self._sources)

        if not answer:
            return SkillOutput(answer="I couldn't find any financial records for that.",
                               skill_name=self.name, confidence=0.8, sources=self._sources)
        if looks_degenerate(answer):
            return SkillOutput(
                answer=substitute_if_degenerate(answer, skill=self.name, tenant_id=inp.tenant_id),
                skill_name=self.name, confidence=0.0)

        # 2026-08 accuracy audit: unlike calculations.py (anchor check) or commerce_admin.py's
        # mutating tools (post-write readback), nothing previously verified that this skill's
        # prose reply actually matches the money figures its own tools returned — the model
        # could transpose/misreport a real number with nothing catching it. Same soft
        # anchor-and-caveat shape as calculations.py, not a hard block.
        anchored, unmatched = self._verify_answer(answer)
        confidence = 0.8
        if anchored is False:
            confidence = 0.45
            answer += ("\n\n⚠️ Please confirm these figures — some of the numbers above "
                       "couldn't be matched to what the ledger actually returned.")
        return SkillOutput(answer=answer, skill_name=self.name, confidence=confidence,
                           sources=self._sources)

    def _system(self, lang: str = "") -> str:
        return ("You are Vula, answering questions about the business's money from its project "
                "finance ledger (invoices and payments filed from email/WhatsApp).\n\n"
                + behaviour_preamble(agentic=True, preferred_language=lang) +
                "\n- Use the tools to fetch real figures; never invent amounts.\n"
                "- NEVER mention your tool calls, quote raw tool output, or describe what a "
                "tool returned. Real replies sent to owners on 2026-09-02 included 'The tool "
                "calls returned the following results: * Tool call 1: {\"period\": \"all\", "
                "\"money_in\": 0...}' — that is internal machinery, and pasting it is worse "
                "than saying nothing. State the figure, or say plainly there's nothing on "
                "file.\n"
                "- Never say you couldn't find something and then state it anyway. If the "
                "ledger holds nothing for the question, say exactly that in one sentence and "
                "stop — don't narrate the attempt.\n"
                "- Format money as South African Rand (e.g. R18,000). Keep answers short and WhatsApp-friendly.\n"
                "- If a project name is fuzzy, pass the user's wording; the tools match loosely.\n"
                "- If the question is a GENERAL how-does-this-work question (e.g. 'what's a "
                "typical professional fee percentage', 'what's a healthy profit margin', 'how "
                "does VAT work') rather than a request for THIS business's own figures, call "
                "lookup_finance_knowledge instead of the ledger tools — don't tell the user "
                "you couldn't find financial records for a question that was never about their "
                "own ledger in the first place.")

    async def _loop(self, history: str, question: str, tenant_id: str, lang: str = "") -> str:
        import litellm
        from config import settings
        from core.llm_router import escalate_to_cloud, looks_unreliable, compute_confidence
        litellm.drop_params = True
        model, api_key, api_base = await resolve_generation_route()
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self._system(lang)}]
        if history:
            messages.append({"role": "user", "content": f"(Conversation so far)\n{history}"})
        messages.append({"role": "user", "content": question})

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await litellm.acompletion(model=model, messages=messages, tools=TOOL_SPECS,
                tool_choice="auto", temperature=0.2, max_tokens=700, api_key=api_key, api_base=api_base,
                # Unconditional — dropped silently wherever unsupported (cloud routes, older
                # Ollama builds) rather than erroring. See reasoning.py for the original wiring.
                logprobs=True, top_logprobs=1)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                inline = self._inline(msg.content or "")
                if inline:
                    name, args = inline
                    result = await self._dispatch(name, args, tenant_id)
                    self._record_dispatch(name, result)
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content":
                        f"[{name} returned]:{fence('TOOL_RESULT', json.dumps(result, default=str)[:1500])}\n"
                        "Answer the user in short plain language. No JSON."})
                    continue
                answer = (msg.content or "").strip()
                # 2026-08 accuracy audit: this skill previously had zero adoption of the
                # logprob-confidence escalation wired into reasoning.py/commerce_assistant.py
                # the same day — money-reporting with no low-confidence-local-answer check.
                if model.startswith("ollama/"):
                    logprob_conf = compute_confidence(resp)
                    if looks_unreliable(answer, confidence=logprob_conf,
                                        confidence_threshold=settings.local_confidence_threshold):
                        esc = escalate_to_cloud("local_unreliable", task_type="finance_admin")
                        if esc:
                            model, api_key, api_base = esc
                            resp = await litellm.acompletion(
                                model=model, messages=messages, temperature=0.2,
                                max_tokens=700, api_key=api_key, api_base=api_base,
                                logprobs=True, top_logprobs=1)
                            answer = (resp.choices[0].message.content or "").strip()
                return answer
            messages.append({"role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls]})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = await self._dispatch(tc.function.name, args, tenant_id)
                self._record_dispatch(tc.function.name, result)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.function.name,
                                 "content": fence('TOOL_RESULT', json.dumps(result, default=str)[:1800])})

        # See commerce_admin.py's _agent_loop for why this nudge exists (2026-08-22 real
        # fabricated-success incident, a different skill but the same exhausted-budget shape).
        messages.append({"role": "user", "content": (
            "You were not able to get a clear answer within the available attempts. Do NOT "
            "state a figure unless a tool result above actually returned it. Tell the user "
            "plainly that you couldn't find it instead."
        )})
        resp = await litellm.acompletion(model=model, messages=messages, temperature=0.2,
            max_tokens=400, api_key=api_key, api_base=api_base)
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

    # ── accuracy anchor (2026-08 audit) ──────────────────────────────────────────
    @staticmethod
    def _numbers(text: str) -> List[float]:
        return [float(m.replace(",", "")) for m in re.findall(r"-?\d[\d,]*\.?\d*", text or "")]

    def _record_dispatch(self, name: str, result: Any) -> None:
        """Bookkeeping shared by both dispatch call sites in _loop: anchor candidates, the
        SkillOutput.sources this turn (2026-08-24 — lets the adversarial verifier actually
        ground-check the reply instead of running blind), and whether every tool this turn
        came back empty (drives the post-tool decline guard in run()). Lazy-inits its
        bookkeeping attrs (same defensive pattern _verified already relied on) so a test or
        caller that invokes _loop() directly — bypassing run() — doesn't need to know about
        every internal attribute run() would normally set up first."""
        if not hasattr(self, "_sources"):
            self._sources: List[Dict[str, Any]] = []
        if not hasattr(self, "_verified"):
            self._verified: List[float] = []
        if not hasattr(self, "_all_not_found"):
            self._all_not_found = True
        self._any_tool_dispatched = True
        self._verified.extend(self._extract_candidates(result))
        self._sources.append(tool_source(name, result))
        not_found = isinstance(result, dict) and bool(
            result.get("error") or result.get("found") is False
            or _is_empty_ledger_result(result))
        if not not_found:
            self._all_not_found = False

    def _extract_candidates(self, result: Any) -> List[float]:
        # (see _is_empty_ledger_result below for the 2026-09-02 empty-ledger fix)
        """Every numeric value in a tool result, plus its /100 AND *100 forms —
        vula_project_finances stores rand directly (not cents, unlike the commerce_* tables),
        but this stays defensive of either convention rather than assuming one. 2026-08-24: the
        *100 direction was missing — only /100 was hedged, an asymmetric guard against the
        same ambiguity it claims to cover both ways."""
        raw = self._numbers(json.dumps(result, default=str))
        out = set()
        for v in raw:
            out.add(round(v, 2))
            out.add(round(v / 100, 2))
            out.add(round(v * 100, 2))
        return list(out)

    @staticmethod
    def _money_shaped_numbers(text: str) -> List[float]:
        """Numbers in a REPLY (not a tool result) that look like a stated Rand figure —
        2026-08-24: the original unfiltered version treated any digit string >=10 as money-
        shaped, which caught years, percentages, invoice/account numbers, and transaction
        counts, any of which could spuriously fail to match a ledger total and trigger an
        unwarranted low-confidence caveat. Excludes: a number immediately followed by '%' (a
        percentage, not money), and a bare 4-digit integer in a plausible year range with no
        adjacent currency marker (a whole Rand amount that happens to equal a year is rare
        enough, and 'R2026' patterns are still caught, that this trade-off favours fewer false
        positives)."""
        text = text or ""
        out = []
        for m in re.finditer(r"-?\d[\d,]*\.?\d*", text):
            raw = m.group(0)
            try:
                val = float(raw.replace(",", ""))
            except ValueError:
                continue
            if abs(val) < 10:
                continue
            if text[m.end():m.end() + 2].lstrip().startswith("%"):
                continue
            # Bare year check must compare the VALUE, not the raw matched text — [\d,]* can
            # greedily swallow a trailing sentence comma (e.g. "2026," before "you've"), so a
            # raw-string \d{4} fullmatch silently never fires and the year slips through.
            if val == int(val) and "." not in raw and 1900 <= val <= 2099:
                before = text[max(0, m.start() - 2):m.start()]
                if "R" not in before and "r" not in before:
                    continue
            out.append(val)
        return out

    def _verify_answer(self, answer: str):
        """Every money-shaped number in the reply must be anchored to a real tool-returned
        figure. Returns (all_anchored: True|False|None, unmatched_numbers)."""
        if not self._verified:
            return (None, [])
        ans_nums = self._money_shaped_numbers(answer)
        if not ans_nums:
            return (None, [])
        unmatched = []
        for a in ans_nums:
            tol = max(0.5, abs(a) * 0.01)
            if not any(abs(a - v) <= tol for v in self._verified):
                unmatched.append(a)
        return (len(unmatched) == 0, unmatched)

    # ── tools ──────────────────────────────────────────────────────────────────
    def _match_project(self, tenant_id: str, summary: dict, hint: str) -> str:
        hint_l = (hint or "").lower()
        for p in summary.get("projects", []):
            if p["project"] and p["project"].lower().replace("_", " ") in hint_l.replace("_", " "):
                return p["project"]
            toks = set(re.findall(r"[a-z0-9]{3,}", p["project"].lower()))
            if toks & set(re.findall(r"[a-z0-9]{3,}", hint_l)):
                return p["project"]
        return hint

    async def _dispatch(self, name: str, args: Dict[str, Any], tenant_id: str) -> Any:
        from vula.integrations.finances import finance_summary, _client
        try:
            if name == "budget_status" and not (args.get("project") or "").strip():
                # 2026-08-24: confirmed real bug — with no project hint, _match_project always
                # falls through and returns "" unchanged; finance_summary(tenant_id, "") then
                # treats the falsy project as "no filter" (vula/integrations/finances.py:204)
                # and returns ALL projects sorted by spend descending; the old code just took
                # the FIRST one (the single highest-spending project) and presented it as "the"
                # answer with no aggregate and no indication it was arbitrary. budget_status's
                # own tool description explicitly allows "for a project, or all projects" — so
                # no project now genuinely means all projects, aggregated.
                full = finance_summary(tenant_id)
                projects = full.get("projects", [])
                total_budget = sum(p.get("budget") or 0 for p in projects)
                total_out = sum(p.get("out") or 0 for p in projects)
                return {"scope": "all_projects", "project_count": len(projects),
                        "total_budget": round(total_budget, 2), "total_spent": round(total_out, 2),
                        "total_remaining": round(total_budget - total_out, 2) if total_budget else None,
                        "per_project": [{"project": p["project"], "budget": p.get("budget"),
                                         "spent": p["out"], "remaining": p.get("remaining")}
                                        for p in projects]}
            if name in ("project_spend", "budget_status"):
                full = finance_summary(tenant_id)
                proj = self._match_project(tenant_id, full, args.get("project", ""))
                one = finance_summary(tenant_id, proj)
                row = next((p for p in one["projects"]), None)
                if not row:
                    return {"project": proj, "found": False, "note": "no financial records for this project"}
                return {"project": row["project"], "money_in": row["in"], "money_out": row["out"],
                        "net": row["net"], "budget": row["budget"], "remaining": row["remaining"],
                        "transactions": row["count"]}
            if name == "money_in_out":
                period = args.get("period", "all")
                rows = (_client().table("vula_project_finances").select("amount,direction,occurred_at,created_at")
                        .eq("tenant_id", tenant_id).execute().data or [])
                if period in ("this_month", "last_30"):
                    if period == "last_30":
                        cut = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
                    else:
                        cut = datetime.now(timezone.utc).date().replace(day=1).isoformat()
                    rows = [r for r in rows if (r.get("occurred_at") or r.get("created_at", "")[:10]) >= cut]
                tin = round(sum(float(r["amount"]) for r in rows if r.get("direction") == "in"), 2)
                tout = round(sum(float(r["amount"]) for r in rows if r.get("direction") == "out"), 2)
                return {"period": period, "money_in": tin, "money_out": tout, "net": round(tin - tout, 2),
                        "transactions": len(rows)}
            if name == "supplier_lookup":
                q = (args.get("query") or "").strip()
                digits = re.sub(r"\D", "", q)
                rows = (_client().table("vula_project_finances").select("*")
                        .eq("tenant_id", tenant_id).execute().data or [])
                hits = [r for r in rows if (digits and digits in re.sub(r"\D", "", r.get("bank_account") or ""))
                        or (q.lower() in (r.get("counterparty") or "").lower())]
                if not hits:
                    return {"query": q, "found": False}
                total = round(sum(float(h["amount"]) for h in hits), 2)
                projects = sorted({h.get("project") for h in hits if h.get("project")})
                names = sorted({h.get("counterparty") for h in hits if h.get("counterparty")})
                return {"query": q, "found": True, "names": names, "account": next(
                    (h.get("bank_account") for h in hits if h.get("bank_account")), None),
                    "total": total, "payments": len(hits), "projects": projects}
            if name == "lookup_finance_knowledge":
                return await self._dispatch_finance_knowledge(args.get("query") or "")
        except Exception as exc:
            logger.warning("finance tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"unknown tool {name}"}

    async def _dispatch_finance_knowledge(self, query: str) -> Dict[str, Any]:
        """General (not this tenant's own figures) finance/business knowledge — the shared SA
        construction-rates KB (professional_fees.md etc.) and the shared general SA
        small-business KB (pricing_and_margins.md, vat_basics.md etc.), same "shared training
        KB" pattern already proven in architecture_planning.py / commerce_admin.py's
        lookup_business_info. 2026-08 audit: finance_admin previously had NO fallback for a
        general how-does-this-work question — every tool being ledger-lookups meant it declined
        with 'no financial records' even when a real, good answer existed in shared content."""
        if not query.strip():
            return {"found": False, "message": "No query given."}
        results: List[Dict[str, Any]] = []
        try:
            from vula.ingestion.pipeline import VulaIngestionPipeline
            from vula.training.content import TRAINING_TENANT_ID
            from vula.training.business_content import BUSINESS_TRAINING_TENANT_ID
            for tid, source in ((TRAINING_TENANT_ID, "construction_kb"),
                                 (BUSINESS_TRAINING_TENANT_ID, "general_sa_business")):
                try:
                    chunks = await VulaIngestionPipeline(tenant_id=tid).query(
                        query, top_k=3, authoritative_only=True)
                except Exception as exc:
                    logger.debug("finance knowledge lookup skipped for %s: %s", tid, exc)
                    continue
                for c in chunks:
                    results.append({
                        "source": source, "filename": c.get("filename", "?"),
                        "text": (c.get("text") or "")[:900],
                    })
        except Exception as exc:
            logger.debug("finance knowledge lookup failed: %s", exc)
        if not results:
            return {"found": False, "message": "Nothing in the knowledge base matches that."}
        return {"found": True, "results": results}
