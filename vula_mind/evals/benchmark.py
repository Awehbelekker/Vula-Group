"""
evals/benchmark.py — Vula's capability benchmark: every component, on real tenants, scored.

Ian, 2026-09-29: "run a benchmark that will test every component and feature of Vula to see how it
actually performs on tasks and processes and agent capabilities — to find gaps." Real tenants,
read-only; graded by rules, then by an AI judge.

Two layers, one scorecard:

  deterministic  no model. Routing (every labelled question), the WhatsApp commands and
                 detectors, and filing accuracy measured on the tenant's own filed documents
                 (does the resolver put a document where it actually lives?).
  agents         each case is a real question to a real tenant, run through the same entry the
                 WhatsApp path uses (owner → skill picker or admin agent, rep → rep agent,
                 customer → shop assistant), inside core.dry_run: tools that only read run for
                 real, everything else is recorded and not performed, and no message or email
                 leaves. Checked by rules (right skill, right tool, forbidden tool, required facts
                 — some computed live from the tenant's data — no leaked tool plumbing, no claim
                 of an action that didn't happen, every rand figure present in what the tools
                 returned), then scored 1–5 by a separate judge model.

Runs on the server (the model keys live there): Master › Benchmark → POST /v1/master/benchmark.
A case passes when every rule passes and the judge gives 4 or 5.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

CASES = Path(__file__).parent / "cases" / "benchmark.yaml"
JUDGE_PASS = 4
_RAND_RE = re.compile(r"R\s?(\d{1,3}(?:[ ,]\d{3})+|\d+)(?:[.,](\d{2}))?\b")


def load_cases(path: Path = CASES) -> List[Dict[str, Any]]:
    return yaml.safe_load(path.read_text()) or []


# ── Truth computed live from the tenant's data ────────────────────────────────

async def _truth_supplier_invoices(tenant_id: str, supplier: str) -> List[str]:
    """The invoice count the answer must state for this supplier."""
    from vula.commerce import service
    res = await service.find_filed_document(tenant_id, supplier, category="Invoice")
    n = res.get("total_matches")
    return [f"{n} "] if n else []


async def _truth_project_names(tenant_id: str, _arg: str = "") -> List[str]:
    from vula.commerce.project_programme import running_projects
    return running_projects(tenant_id)[:3]


TRUTH: Dict[str, Callable[[str, str], Awaitable[List[str]]]] = {
    "supplier_invoices": _truth_supplier_invoices,
    "project_names": _truth_project_names,
}


# ── Rule checks ───────────────────────────────────────────────────────────────

def _amounts(text: str) -> List[int]:
    out = []
    for m in _RAND_RE.finditer(text or ""):
        whole = int(re.sub(r"[ ,]", "", m.group(1)))
        out.append(whole * 100 + int(m.group(2) or 0))
    return out


def ungrounded_amounts(answer: str, evidence: str) -> List[str]:
    """Rand figures in the answer that appear nowhere in what the tools returned (as cents,
    rands, or the formatted figure). Figures under R10 are ignored (counts, units)."""
    ev_digits = set(re.findall(r"\d+(?:\.\d+)?", (evidence or "").replace(",", "")))
    missing = []
    for cents in _amounts(answer):
        if cents < 1000:
            continue
        rands = cents / 100
        forms = {str(cents), f"{rands:.2f}", f"{rands:.1f}", str(int(rands)) if rands == int(rands) else ""}
        if not (forms - {""}) & ev_digits:
            missing.append(f"R{rands:,.2f}")
    return missing


def rule_checks(case: Dict[str, Any], *, skill: str, answer: str, calls: List[Dict[str, Any]],
                truth: List[str], offered: List[str], error: Optional[str]) -> Dict[str, Any]:
    from core.skills.base import leaked_tool_output, unbacked_action_claim
    low = (answer or "").lower()
    names = [c["tool"] for c in calls]
    checks: Dict[str, Any] = {}
    checks["answered"] = bool(answer and answer.strip()) and not error
    if case.get("expect_skill"):
        want = case["expect_skill"]
        checks["skill"] = skill in (want if isinstance(want, list) else [want])
    exp = case.get("expect_tools")
    if exp is not None:
        exp = exp if isinstance(exp, list) else [exp]
        checks["tool"] = (not names) if exp == ["none"] else any(t in names for t in exp)
    if case.get("forbid_tools"):
        checks["no_forbidden_tool"] = not any(t in names for t in case["forbid_tools"])
    need = list(case.get("must_contain") or []) + list(truth or [])
    if need:
        checks["facts"] = all(s.lower() in low for s in need)
    if case.get("must_not_contain"):
        checks["no_bad_content"] = not any(s.lower() in low for s in case["must_not_contain"])
    checks["no_leaked_plumbing"] = not leaked_tool_output(answer or "", offered)
    sources = [{"tool": c["tool"], "result": c.get("result"), "executed": c.get("executed")} for c in calls]
    checks["no_false_action_claim"] = not unbacked_action_claim(answer or "", sources)
    evidence = " ".join(str(c.get("result") or "") for c in calls if c.get("executed"))
    if evidence:
        missing = ungrounded_amounts(answer, evidence + " " + case.get("prompt", ""))
        checks["figures_grounded"] = not missing
        if missing:
            checks["_ungrounded"] = missing
    return checks


# ── The judge ─────────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = (
    "You grade replies from Vula, a WhatsApp business assistant for South African small "
    "businesses. You see the business, who asked, the question, what Vula's tools returned "
    "(the only facts it had), and Vula's reply. A benchmark dry run blocks actions: a tool marked "
    "NOT PERFORMED was correctly attempted — the reply should say what it would do, never claim "
    "it's done. Score 1–5: 5 = correct, complete, grounded in the tool results, right tone for "
    "WhatsApp; 4 = correct with minor gaps; 3 = partly right or vague; 2 = mostly wrong or "
    "unhelpful; 1 = wrong, invented facts, or harmful. Reply with JSON only: "
    '{"score": <1-5>, "reason": "<one sentence>"}')


async def judge(case: Dict[str, Any], profile: Dict[str, Any], answer: str,
                calls: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    import litellm
    from config import settings
    from core.llm_router import OPENROUTER_BASE
    litellm.drop_params = True
    tools = "\n".join(
        f"- {c['tool']}({json.dumps(c.get('args') or {}, default=str)[:300]}) → "
        + ("NOT PERFORMED (dry run)" if not c.get("executed") else str(c.get("result") or c.get("error"))[:1500])
        for c in calls) or "(no tools called)"
    user = (f"Business: {profile.get('display_name')} ({profile.get('business_type')})\n"
            f"Asked by: {case.get('entry', 'owner')}\n"
            f"Question: {case['prompt']}\n"
            + (f"Earlier in the chat:\n{case['history']}\n" if case.get("history") else "")
            + f"What a good reply does: {case.get('judge') or 'answers the question correctly from the tool results'}\n"
            f"Tool results:\n{tools}\n\nVula's reply:\n{answer}")
    key = settings.openrouter_api_key if model.startswith("openrouter/") else None
    base = OPENROUTER_BASE if model.startswith("openrouter/") else settings.ollama_base
    try:
        resp = await litellm.acompletion(
            model=model, api_key=key, api_base=base, temperature=0, max_tokens=200,
            messages=[{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}])
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.S)
        got = json.loads(m.group(0)) if m else {}
        score = int(got.get("score"))
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
        except Exception:
            cost = None
        return {"score": max(1, min(5, score)), "reason": str(got.get("reason") or "")[:300], "cost_usd": cost}
    except Exception as exc:  # noqa: BLE001 — a judge failure never fails the case by itself
        return {"score": None, "reason": f"judge unavailable: {type(exc).__name__}: {str(exc)[:120]}"}


# ── Running one agent case ────────────────────────────────────────────────────

def _entry_skill(case: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """The skill the WhatsApp path would give this message, and the caller it runs as."""
    entry = case.get("entry", "owner")
    tid = case["tenant"]
    if entry == "customer":
        return "commerce_assistant", {"caller_role": "customer"}
    if entry == "rep":
        return "commerce_admin", {"caller_role": "sales_rep", "caller_name": case.get("caller_name", "Rep")}
    role = {"caller_role": "owner", "caller_name": case.get("caller_name", "Owner")}
    if case.get("route_mode", "knowledge") == "commerce":
        return "commerce_admin", role
    from core.skills.base import looks_like_owner_admin_question
    if looks_like_owner_admin_question(case["prompt"]):      # vula/api/whatsapp.py::_rag_reply 0b
        return "commerce_admin", role
    from core.hrm.orchestrator import HRMOrchestrator
    skill, _why = HRMOrchestrator()._route_with_reason(case["prompt"], tenant_id=tid)
    return skill, role


def _offered_tools(skill_obj: Any) -> List[str]:
    import sys
    mod = sys.modules.get(type(skill_obj).__module__)
    names: List[str] = []
    for v in vars(mod).values() if mod else []:
        if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("type") == "function":
            names += [t["function"]["name"] for t in v]
    return names


async def run_agent_case(case: Dict[str, Any], judge_model: Optional[str]) -> Dict[str, Any]:
    from core import dry_run
    from core.skills.base import SkillInput
    from core.skills.loader import get_skill
    from vula.api.tenants import tenant_profile
    from vula.integrations.metering import set_request_tenant

    tid = case["tenant"]
    profile = tenant_profile(tid)
    skill_name, caller = _entry_skill(case)
    row: Dict[str, Any] = {"id": case["id"], "component": case["component"], "tenant": tid,
                           "prompt": case["prompt"], "skill": skill_name}
    truth: List[str] = []
    for spec in case.get("truth") or []:
        fn = TRUTH.get(spec.get("kind"))
        if fn:
            try:
                truth += await fn(tid, spec.get("arg", ""))
            except Exception as exc:  # noqa: BLE001
                row.setdefault("notes", []).append(f"truth {spec.get('kind')} unavailable: {exc}")
    skill = get_skill(skill_name)
    t0 = time.monotonic()
    answer, error = "", None
    set_request_tenant("benchmark")        # model spend is Vula's, not the tenant's
    with dry_run.session() as st:
        try:
            out = await skill(SkillInput(
                question=case["prompt"], tenant_id=tid,
                conversation_history=case.get("history") or "",
                metadata={**caller, "customer_phone": "", "session_id": f"benchmark:{case['id']}",
                          "preferred_language": "en"},
                max_tokens=600))
            answer, error = out.answer or "", out.error
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        calls, sent = list(st["calls"]), list(st["sent"])
    row["secs"] = round(time.monotonic() - t0, 2)
    row.update(answer=(answer or "")[:1500], error=error,
               tools=[{"tool": c["tool"], "performed": c.get("executed")} for c in calls],
               would_send=len(sent))
    checks = rule_checks(case, skill=skill_name, answer=answer, calls=calls, truth=truth,
                         offered=_offered_tools(skill), error=error)
    row["checks"] = checks
    rules_ok = all(v for k, v in checks.items() if not k.startswith("_"))
    j = await judge(case, profile, answer, calls, judge_model) if (judge_model and answer) else {"score": None}
    row["judge"] = j
    row["ok"] = rules_ok and (j.get("score") is None or j["score"] >= JUDGE_PASS)
    row["why"] = [k for k, v in checks.items() if not k.startswith("_") and not v]
    if j.get("score") is not None and j["score"] < JUDGE_PASS:
        row["why"].append(f"judge {j['score']}/5: {j.get('reason')}")
    return row


# ── Deterministic layer ───────────────────────────────────────────────────────

def run_routing() -> List[Dict[str, Any]]:
    from evals import harness
    rep = harness.run_routing()
    return [{"id": f"routing:{i}", "component": "Routing · which skill answers", "prompt": r["prompt"],
             "ok": r["ok"], "why": [] if r["ok"] else [f"went to {r['got']}, expected {r['expect']}"]}
            for i, r in enumerate(rep["rows"])]


_DETECTORS = [
    # (component, callable name, input, expected)
    ("WhatsApp commands", "project_answer", "Belladonna", True),
    ("WhatsApp commands", "project_answer", "What data are you using to reference cost", False),
    ("WhatsApp commands", "cost_basis", "What data are you using to reference cost", True),
    ("WhatsApp commands", "supplier_history", "Can you summarize Jack hammer invoices", True),
    ("WhatsApp commands", "supplier_history", "Show me unpaid invoices", False),
    ("WhatsApp commands", "add_staff", "add staff Edison Maunganidze 0821234567 plumber", True),
    ("WhatsApp commands", "retry", "try again", True),
]


def run_detectors() -> List[Dict[str, Any]]:
    from core.skills.base import looks_like_supplier_history_question
    from vula.commerce import job_costing, project_programme
    from vula.integrations import doc_filing
    fns = {
        "project_answer": doc_filing.looks_like_project_answer,
        "cost_basis": job_costing.looks_like_cost_basis_question,
        "supplier_history": looks_like_supplier_history_question,
        "add_staff": lambda t: bool(project_programme.parse_add_staff(t)),
    }
    try:
        from vula.api.whatsapp import _RETRY_RE
        fns["retry"] = lambda t: bool(_RETRY_RE.search(t))
    except Exception:
        pass
    rows = []
    for comp, name, text, want in _DETECTORS:
        fn = fns.get(name)
        if not fn:
            continue
        got = bool(fn(text))
        rows.append({"id": f"detector:{name}:{text[:24]}", "component": comp, "prompt": text,
                     "ok": got == want, "why": [] if got == want else [f"{name} said {got}, expected {want}"]})
    return rows


def run_filing_accuracy(tenant_id: str, limit: int = 60) -> List[Dict[str, Any]]:
    """Of the tenant's documents that live under a project, how many would the resolver put
    there today from their own content? Measured on real filed documents."""
    from vula.commerce import service
    from vula.commerce.service import project_key
    from vula.integrations import project_resolver
    rows = (service._client().table("vula_filed_documents")
            .select("id,filename,category,summary,fields,project").eq("tenant_id", tenant_id)
            .not_.is_("project", "null").order("created_at", desc=True).limit(limit).execute().data or [])
    out = []
    for d in rows:
        fields = d.get("fields") or {}
        text = " ".join(str(x) for x in (d.get("filename"), d.get("summary"), fields.get("supplier")) if x)
        try:
            got = (project_resolver.resolve(tenant_id, fields, text) or {}).get("project")
        except Exception as exc:  # noqa: BLE001
            got = None
            log.debug("filing accuracy: resolve failed: %s", exc)
        ok = bool(got) and project_key(got) == project_key(d["project"])
        why = [] if ok else [f"resolver said {got or 'nothing'}, filed under {d['project']}"]
        out.append({"id": f"filing:{d['id']}", "component": f"Document filing · {tenant_id}",
                    "prompt": (d.get("filename") or "")[:80], "ok": ok, "why": why,
                    "unsure": not got})
    return out


# ── Scorecard ─────────────────────────────────────────────────────────────────

def scorecard(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    comps: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        comps.setdefault(r["component"], []).append(r)
    table = []
    for name, rs in comps.items():
        scores = [r["judge"]["score"] for r in rs if (r.get("judge") or {}).get("score") is not None]
        secs = [r["secs"] for r in rs if r.get("secs") is not None]
        table.append({
            "component": name, "passed": sum(1 for r in rs if r["ok"]), "total": len(rs),
            "pass_pct": round(100 * sum(1 for r in rs if r["ok"]) / len(rs)),
            "judge_avg": round(statistics.mean(scores), 2) if scores else None,
            "p50_secs": round(statistics.median(secs), 1) if secs else None,
        })
    table.sort(key=lambda c: (c["pass_pct"], c["component"]))
    gaps = [{"component": r["component"], "id": r["id"], "prompt": r.get("prompt"),
             "why": r.get("why"), "answer": (r.get("answer") or "")[:400]}
            for r in rows if not r["ok"]]
    return {"components": table, "gaps": gaps, "passed": sum(1 for r in rows if r["ok"]),
            "total": len(rows)}


async def run(judge_model: Optional[str] = None, only: Optional[str] = None,
              filing_tenants: tuple = ("digg-demo",)) -> Dict[str, Any]:
    t0 = time.monotonic()
    rows: List[Dict[str, Any]] = []
    rows += run_routing()
    rows += run_detectors()
    for tid in filing_tenants:
        try:
            rows += run_filing_accuracy(tid)
        except Exception as exc:  # noqa: BLE001
            rows.append({"id": f"filing:{tid}", "component": f"Document filing · {tid}", "ok": False,
                         "why": [f"couldn't run: {exc}"]})
    judge_cost = 0.0
    for case in load_cases():
        if only and only.lower() not in case["component"].lower():
            continue
        try:
            r = await run_agent_case(case, judge_model)
        except Exception as exc:  # noqa: BLE001
            r = {"id": case["id"], "component": case["component"], "prompt": case["prompt"],
                 "ok": False, "why": [f"case crashed: {type(exc).__name__}: {exc}"[:300]]}
        judge_cost += float((r.get("judge") or {}).get("cost_usd") or 0)
        rows.append(r)
    card = scorecard(rows)
    return {"kind": "benchmark", "judge_model": judge_model, "secs": round(time.monotonic() - t0, 1),
            "judge_cost_usd": round(judge_cost, 4), **card, "rows": rows}
