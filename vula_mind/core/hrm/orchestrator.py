"""HRM orchestrator — routes tasks, never generates answers."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from config import settings
from core.skills.base import looks_like_supplier_history_question
from core.thinkmesh.graph import (
    DeviceRole,
    GraphStatus,
    MergeStrategy,
    ModelTier,
    TaskGraph,
)

log = logging.getLogger(__name__)

SKILL_REGISTRY_PATH = Path(__file__).parent.parent / "skills" / "registry.json"

COMPLEXITY_KEYWORDS = {
    3: ["analyze", "analyse", "compare", "design", "architect", "synthesize", "evaluate", "critique"],
    2: ["explain", "summarize", "summarise", "calculate", "plan", "estimate", "research"],
    1: ["what", "who", "when", "where", "list", "define", "show"],
}

# Keyword hits (and a miss, None) that a supplier spend/materials-history question may be
# re-routed away from — see _route_with_reason.
_SUPPLIER_HISTORY_OVERRIDABLE = {None, "commerce_assistant", "finance_admin", "calculations"}

SKILL_KEYWORDS: dict[str, list[str]] = {
    # ClickUp first — explicit task-management phrasing only, so it never shadows
    # construction/field-ops queries. The skill itself defers if ClickUp isn't connected.
    "clickup_admin":        ["clickup", "click up", "add a task", "create a task", "new task",
                             "my tasks", "list tasks", "task list", "to-do", "to do list", "todo",
                             "remind me", "set a reminder", "reminder",
                             # meeting phrases — use specific forms, not bare "meeting"
                             # so "read the meeting notes email" doesn't get stolen
                             "schedule a meeting", "set up a meeting", "book a meeting",
                             "schedule meeting", "meet with", "set a meeting",
                             # "schedule a" alone is kept for "schedule a call / demo / interview"
                             "schedule a",
                             "note to self", "make a note", "put on the list", "task for",
                             "assign to", "assign a task",
                             # follow-up: specific task-creation forms only.
                             # Bare "follow up" / "follow-up" are intentionally absent
                             # so "follow up email" can still reach email_admin.
                             "follow up with", "follow-up with",
                             "follow up on", "follow-up on",
                             "follow up task", "follow-up task",
                             "create a follow", "add a follow"],
    # Letters/proposals on letterhead — specific document-type phrasing only, placed BEFORE
    # email_admin so "draft a fee proposal" doesn't get stolen by email_admin's generic
    # "draft a "/"draft me"/"compose" (those stay email-shaped: "draft a reply", "compose an email").
    "draft_admin":          ["fee proposal", "scope of works", "letter of appointment",
                             "appointment letter", "tender invitation", "site meeting minutes",
                             "meeting minutes", "project programme", "payment certificate",
                             "on letterhead", "on our letterhead", "draft a letter", "draft a proposal",
                             "draft a fee proposal", "draft me a letter", "draft me a proposal",
                             "write a letter", "write me a letter"],
    # Commerce next — clear ordering intent, plus appointment-booking intent (the skill's
    # BOOKING_TOOL_SPECS, gated per-tenant by _tenant_has_bookings). Phrased to never collide
    # with clickup_admin's internal-task "book/schedule a meeting" keywords above (customer
    # booking an appointment vs. staff scheduling a meeting are different actions).
    "commerce_assistant":   ["order", "buy", "cart", "checkout", "stock", "in stock", "menu",
                             "catalog", "catalogue", "track order", "add to cart",
                             "product", "fish", "seafood", "catch",
                             "book an appointment", "book a slot", "book a session",
                             "book a consultation", "make an appointment",
                             "cancel my appointment", "cancel my booking",
                             "reschedule my appointment", "available slots",
                             "available times", "check availability"],
    # Microsoft OneDrive + Outlook (draft-only email). Defers if not connected.
    "microsoft_admin":      ["onedrive", "one drive", "outlook", "sharepoint"],
    # Generic IMAP/SMTP mailbox (GoDaddy/cPanel/Zoho/etc.) — owns generic email phrasing.
    # Money/budget/supplier questions answered from the finance ledger. BEFORE calculations
    # (2026-08-24: confirmed real collision — calculations' generic "how much is"/"how much
    # does" was intercepting "how much is left on the budget for Stage 3" before finance_admin
    # ever got a chance, even though finance_admin has the more specific "budget for"/"left on
    # the budget" match for exactly this phrasing).
    "finance_admin":        ["spent on", "how much have we spent", "how much did we spend",
                             "money in", "money out", "money in and out", "in vs out",
                             "budget left", "budget remaining", "left on the budget",
                             "whats left on the", "what's left on the", "budget for",
                             "who is account", "supplier paid", "what have we paid", "total invoiced",
                             "cash in", "cash out", "the ledger"],
    "email_admin":          ["email", "my mail", "my emails", "inbox", "draft a reply",
                             "draft an email", "draft email", "check my email", "check email",
                             "reply to the email", "file the attachment", "email attachment",
                             "draft a ", "draft me", "compose", "reply to", "summarise the email",
                             "read the email", "read the latest",
                             "waiting on me", "to reply", "need a reply", "needs a reply",
                             # bare "follow up" / "follow-up" intentionally removed here —
                             # they are handled by clickup_admin (higher priority in dict).
                             # Email-specific follow-up forms remain:
                             "follow up email", "follow-up email", "follow up on that email",
                             "outstanding emails", "awaiting reply",
                             "gmail", "google mail"],
    # Google Drive + Gmail — provider-named only (generic 'email' goes to email_admin).
    "google_admin":         ["google drive", "my drive", "in my drive", "google doc", "from drive"],
    # Explicit standard/code lookup → cited search of the code library. BEFORE calculations
    # (2026-08-24: same collision class as finance_admin above — calculations' generic
    # "occupancy load"/"occupant load" was intercepting "what standard covers occupancy load
    # calculations" before standards_lookup's more specific "standard cover" match ever fired).
    "standards_lookup":     ["look up", "which standard", "which code", "what standard",
                             "what does sans", "code library", "sans clause", "which sans",
                             "standard cover", "applicable standard"],
    # Calculation intent BEFORE architecture_planning so "how many seats / what width /
    # occupancy" get deterministic arithmetic (computed, not guessed) — but AFTER finance_admin
    # and standards_lookup (see their own comments above) so their more specific phrasing wins
    # over calculations' intentionally generic "how much is"/"occupancy load" — those are meant
    # to catch a genuine arithmetic question, not a budget lookup or a standards citation that
    # merely happens to use similar words.
    "calculations":         ["how many seats", "how many people", "how many can", "how many bays",
                             "how many parking", "parking bays", "occupant load", "occupancy load",
                             "maximum occupancy", "max occupancy", "what width", "minimum width",
                             "how wide", "calculate", "floor area", "how many units",
                             "what does it cost", "what would it cost", "how much does", "how much will",
                             "how much is", "using our rates", "cost of", "rate for", "what do we charge",
                             "estimate the cost", "total cost", "what will it cost"],
    # Architecture/construction BEFORE file_parse so "Stage 4 documentation",
    # "fees", "SACAP" etc consult the SA construction KB (not just tenant docs).
    # 2026-09-18: this used to also include generic words ("fee", "plan", "design",
    # "commercial", "structure"...) that would misroute an unrelated tenant's ordinary question
    # (e.g. a healthcare practice asking about a "plan" or a "fee") into architecture_planning.
    # Those generic terms moved to _ARCHITECTURE_WEAK_KEYWORDS below, gated by business_type —
    # only unambiguous AEC jargon stays unconditional here.
    "architecture_planning":["sacap", "nhbrc", "jbcc", "nec ", "sans", "cidb", "procsa",
                             "bbbee", "b-bbee", "heritage", "sahra", "boq", "bill of quantities",
                             "occupation certificate", "quantity surveyor", "preliminaries",
                             "provisional sum", "retention", "practical completion", "snag"],
    "web_search":           ["search", "find online", "latest", "current", "news", "tender alert",
                             "research", "look up", "google"],
    "code_execution":       ["run", "execute", "compute", "code", "script"],
    "memory_recall":        ["remember", "previous", "history", "last time", "before", "we discussed"],
    "file_parse":           ["this file", "this document", "pdf", "parse", "extract from", "summarise this"],
    "image_analysis":       ["image", "photo", "picture", "screenshot", "diagram"],
    "financial_reasoning":  ["revenue", "profit", "budget", "cashflow"],
    "reasoning":            [],  # fallback
}

# Split out of architecture_planning's own keyword list (2026-09-18) — these generic words only
# mean "this is architecture/construction" in an AEC context; anywhere else they're just
# ordinary English. Checked only for a tenant whose business_type suggests professional/trades
# services (see _keyword_skill), never unconditionally. Known residual imprecision: "services"
# also covers legal/accounting/marketing agencies, not just architecture — a law firm asking
# about "fees" can still misroute — but it's the only vertical signal that persists past
# onboarding today, and this is a real improvement over matching every tenant unconditionally.
_ARCHITECTURE_WEAK_KEYWORDS = [
    "zoning", "town planning", "fee", "fees", "stage 1", "stage 2", "stage 3", "stage 4",
    "stage 5", "work stage", "documentation", "tender", "contractor", "subcontract",
    "municipal", "building plan", "residential", "commercial", "fitout", "construction",
    "architect", "design", "drawing", "elevation", "structure", "infrastructure", "plan",
]
_ARCHITECTURE_WEAK_BUSINESS_TYPES = {"services", "trades"}


class HRMOrchestrator:
    def __init__(
        self,
        ollama_url: str | None = None,
        model: str = "qwen2.5:3b",
    ):
        self.ollama_url = ollama_url or settings.ollama_base
        self.model = model
        self._skill_registry: dict[str, Any] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """NOT consulted for live routing — SKILL_KEYWORDS + the keyword-miss LLM fallback
        below are the real routing logic; registry.json's own top-level "description" field
        says the same. This load exists so tests/test_orchestrator.py's
        test_skill_registry_matches_real_implemented_skills can catch registry.json drifting
        stale against core/skills/loader.py's actually-implemented skill list — that's the
        one real consumer of self._skill_registry. Don't delete this thinking it's dead code;
        delete registry.json's regression guard too if you do, on purpose."""
        try:
            with open(SKILL_REGISTRY_PATH) as f:
                data = json.load(f)
                # registry.json uses "name" as the unique key
                self._skill_registry = {s["name"]: s for s in data.get("skills", [])}
            log.debug("Loaded %d skills from registry", len(self._skill_registry))
        except FileNotFoundError:
            log.warning("Skill registry not found at %s", SKILL_REGISTRY_PATH)
        except (KeyError, json.JSONDecodeError) as exc:
            log.error("Skill registry parse error: %s", exc)

    def _keyword_complexity(self, prompt: str) -> int:
        lower = prompt.lower()
        for level in (3, 2, 1):
            if any(kw in lower for kw in COMPLEXITY_KEYWORDS[level]):
                return level
        return 1

    def _llm_complexity(self, prompt: str) -> int:
        """Ask local LLM to score complexity 1–3. Falls back to keyword heuristic."""
        try:
            resp = httpx.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": (
                        "Rate this task complexity 1 (simple) to 3 (complex). "
                        f"Reply with only the number.\nTask: {prompt}"
                    ),
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=10,
            )
            score = int(resp.json().get("response", "1").strip()[0])
            return max(1, min(3, score))
        except Exception:
            return self._keyword_complexity(prompt)

    def _architecture_weak_ok(self, tenant_id: str | None) -> bool:
        """True when this tenant's business_type suggests professional/trades services — the
        only case _ARCHITECTURE_WEAK_KEYWORDS are allowed to match. No tenant_id, no config, or
        any lookup failure all fail toward False (never matching), the safe direction for a
        keyword class that exists specifically to stop over-matching."""
        if not tenant_id:
            return False
        try:
            from vula.api.tenants import get_config
            return (get_config(tenant_id) or {}).get("business_type") in _ARCHITECTURE_WEAK_BUSINESS_TYPES
        except Exception:
            return False

    def _keyword_skill(self, prompt: str, tenant_id: str | None = None) -> str | None:
        """The keyword-table match only — None if nothing matched."""
        lower = prompt.lower()
        for skill_name, keywords in SKILL_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return skill_name
            if skill_name == "architecture_planning" and self._architecture_weak_ok(tenant_id):
                if any(kw in lower for kw in _ARCHITECTURE_WEAK_KEYWORDS):
                    return skill_name
        return None

    def _match_skill(self, prompt: str, tenant_id: str | None = None) -> str:
        return self._route_with_reason(prompt, tenant_id)[0]

    def _has_connected_mailbox(self, tenant_id: str | None) -> bool:
        if not tenant_id:
            return False
        try:
            from vula.email_imap.credentials import get_email_creds
            return bool(get_email_creds(tenant_id))
        except Exception:
            return False

    def _route_with_reason(self, prompt: str, tenant_id: str | None = None) -> tuple[str, str]:
        """(skill, matched_by) where matched_by is 'keyword' | 'llm_fallback' |
        'mailbox_fallback' | 'default' — the reason is emitted as routing telemetry so
        misroutes, and how often routing falls through to the generic 'reasoning' skill, are
        measurable."""
        kw = self._keyword_skill(prompt, tenant_id)
        # 2026-09-23 (DIGG): a supplier spend/materials-history question belongs to
        # email_admin's find_document (full invoice list, server-side total, materials roll-up)
        # — not commerce_assistant (matched "buy", the CUSTOMER shopping skill) or finance_admin
        # (matched "how much have we spent", the ledger, which doesn't see filed-but-unbooked
        # supplier invoices). Only overrides those keyword hits or a miss, never an explicit
        # match like "draft an email"/"remind me"; and only with a connected mailbox, since
        # email_admin declines to run without one. See looks_like_supplier_history_question.
        if (kw in _SUPPLIER_HISTORY_OVERRIDABLE
                and looks_like_supplier_history_question(prompt)
                and self._has_connected_mailbox(tenant_id)):
            return "email_admin", "supplier_history"
        if kw:
            return kw, "keyword"
        # No keyword matched — before silently defaulting to the least-specialized skill,
        # try one cheap local-model classification pass (2026-07-27: this exact fallthrough
        # is what routed a real supplier-quotation question to generic reasoning instead of
        # a document-grounded skill). Fails open to "reasoning" on any error or disagreement.
        if settings.skill_llm_fallback_enabled:
            classified = self._llm_classify_skill(prompt)
            if classified:
                return classified, "llm_fallback"
        # 2026-09-22 real incident (DIGG, knowledge-mode tenant): "I want a breakdown on what
        # has been spend at jackhammer" and "Please check expenses from Jack Hammer" both
        # matched no keyword, and the small local classifier model also missed (or is
        # disabled) — the previous behaviour was a silent default to 'reasoning', a zero-tool
        # skill that can only answer from whatever the KB semantic search happens to match,
        # with no ability to check filed documents or search the mailbox. It answered from an
        # unrelated chunk rather than admitting it didn't know. A tenant-data-shaped question
        # (see looks_like_tenant_data_question — invoice/expense/spend/etc) for a tenant with
        # a connected mailbox should reach email_admin instead: find_document, then its own
        # live-mailbox fallback (2026-09-22), give it a real chance to look the answer up.
        if self._has_connected_mailbox(tenant_id):
            from core.skills.base import looks_like_tenant_data_question
            if looks_like_tenant_data_question(prompt):
                return "email_admin", "mailbox_fallback"
        return "reasoning", "default"

    def _llm_classify_skill(self, prompt: str) -> str | None:
        """One cheap local-model pass, keyword-miss path only. Returns a skill name from
        SKILL_KEYWORDS, or None (caller falls back to "reasoning") on any failure/non-match."""
        try:
            options = ", ".join(SKILL_KEYWORDS.keys())
            resp = httpx.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": (
                        f"Pick the ONE best-fitting skill for this request from this exact "
                        f"list: {options}. Reply with only the skill name, nothing else.\n"
                        f"Request: {prompt}"
                    ),
                    "stream": False,
                    "options": {"num_predict": 10},
                },
                timeout=8,
            )
            answer = (resp.json().get("response") or "").strip().lower()
            for skill_name in SKILL_KEYWORDS:
                if skill_name in answer:
                    return skill_name
        except Exception as exc:
            log.debug("LLM skill classification failed, falling back to reasoning: %s", exc)
        return None

    def _select_model(self, complexity: int, routing_hints: dict,
                      tenant_id: str = "", skill_name: str = "") -> ModelTier:
        # Was "preferred_tier" — a key ReflectionAgent.get_routing_hints() never produces (it
        # returns "winning_tier"), so this always read None and the reflection loop silently
        # never influenced routing despite being fully computed, stored, and fetched back.
        hint = routing_hints.get("winning_tier")
        if hint:
            try:
                return ModelTier(hint)
            except ValueError:
                pass

        # Mass Mind cold-start fallback (2026-09-15): routing_hints is empty whenever this
        # tenant's own history has nothing similar to say for the current request — either a
        # genuinely new tenant (empty for every request), or an established one hitting a
        # question unlike anything in its own past. Either way, "what generally works well for
        # a business shaped like this one, running this skill" is strictly better than guessing
        # off complexity alone, and never overrides real tenant-specific signal (that branch
        # already returned above). Never blocks routing — any failure falls through silently.
        if tenant_id and skill_name:
            try:
                from core.mass_mind import patterns as mm_patterns
                suggested = mm_patterns.suggest_tier(tenant_id, skill_name)
                if suggested:
                    return ModelTier(suggested)
            except Exception:
                pass

        return {1: ModelTier.WORKER, 2: ModelTier.WORKER, 3: ModelTier.REASONER}[complexity]

    def _select_merge(self, complexity: int, skill_name: str) -> MergeStrategy:
        if complexity == 3:
            return MergeStrategy.SYNTHESIZE
        if skill_name in ("financial_reasoning", "code_execution"):
            return MergeStrategy.BEST_CONFIDENCE
        return MergeStrategy.FASTEST

    def plan(self, graph: TaskGraph, use_llm_scoring: bool = False) -> TaskGraph:
        graph.status = GraphStatus.PLANNING
        prompt = graph.original_prompt

        complexity = (
            self._llm_complexity(prompt) if use_llm_scoring
            else self._keyword_complexity(prompt)
        )
        graph.complexity = complexity

        skill_name, matched_by = self._route_with_reason(prompt, tenant_id=graph.tenant_id)
        model_tier = self._select_model(complexity, graph.routing_hints,
                                        tenant_id=graph.tenant_id, skill_name=skill_name)
        merge = self._select_merge(complexity, skill_name)
        graph.merge_strategy = merge

        # Routing telemetry — POPIA-safe (prompt hash, never raw text). Lets us measure how
        # often keyword routing misses and falls through to the LLM classifier or bare
        # 'reasoning', which is the single biggest source of "Vula gave a wrong answer".
        try:
            import hashlib as _h
            from core.reasoning_telemetry import emit as _emit_route
            _emit_route(system="vula-skill-routing",
                        task="hash:" + _h.sha256(prompt.encode("utf-8")).hexdigest()[:12],
                        outcome=skill_name, reason=matched_by,
                        escalated=(matched_by == "default"),
                        extra={"complexity": complexity})
        except Exception:
            pass

        branch_count = {1: 1, 2: 2, 3: 3}[complexity]

        for i in range(branch_count):
            role = DeviceRole.PRIMARY if i == 0 else DeviceRole.SECONDARY
            graph.add_branch(
                device_role=role,
                model_tier=model_tier,
                skill_id=skill_name,
                prompt=prompt,
            )

        log.info(
            "HRM planned task=%s complexity=%d skill=%s branches=%d merge=%s",
            graph.task_id[:8], complexity, skill_name, branch_count, merge.value,
        )
        return graph
