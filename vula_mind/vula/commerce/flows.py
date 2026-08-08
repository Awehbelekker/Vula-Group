"""
vula/commerce/flows.py — owner-configurable WhatsApp conversation flows (MVP, migration 118).

A linear, keyword-branching flow builder: an owner defines trigger phrases, a sequence of
steps (prompt + expected reply type + branch rules), and a terminal action drawn from the
existing commerce_admin tool vocabulary. Deliberately narrow, same scoped-down philosophy as
automations.py (P3): no visual node-graph builder, no arbitrary tool execution (a curated
action whitelist only), no new send-logic (notify_team reuses automations.py's exact helper-
pick + _send_reply path).

Two runtime entry points, checked at different points in the WhatsApp router:
  maybe_continue_flow — is this phone already mid-flow? Advance state, or complete it.
  maybe_start_flow    — does this NEW message match a trigger phrase? Start a session.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

log = logging.getLogger(__name__)

REPLY_TYPES = {"free_text", "yes_no", "multiple_choice"}
END_ACTION_TOOLS = {"create_booking", "create_contact", "create_reminder", "notify_team"}

MAX_FLOWS_PER_TENANT = 10
MAX_STEPS_PER_FLOW = 8
MAX_TRIGGER_PHRASES = 10
_IDLE_HOURS = 24


def _client():
    from vula.commerce import service as cs
    return cs._client()


def _digits(phone: str) -> str:
    n = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "27" + n[1:] if n.startswith("0") else n


def _fill(template: str, ctx: dict) -> str:
    """Same {{placeholder}} substitution as automations.py — kept as its own copy since
    flows.py has no reason to import from automations.py (different, unrelated tables)."""
    def sub(m):
        return str(ctx.get(m.group(1), m.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", sub, template or "")


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_steps(steps: List[dict]) -> Optional[str]:
    if not steps:
        return "A flow needs at least one step."
    if len(steps) > MAX_STEPS_PER_FLOW:
        return f"A flow can have at most {MAX_STEPS_PER_FLOW} steps."
    for i, step in enumerate(steps):
        reply_type = step.get("reply_type")
        if reply_type not in REPLY_TYPES:
            return f"Step {i + 1}: reply_type must be one of {sorted(REPLY_TYPES)}."
        if reply_type == "multiple_choice" and not step.get("options"):
            return f"Step {i + 1}: multiple_choice needs at least one option."
        if not (step.get("prompt") or "").strip():
            return f"Step {i + 1}: needs a prompt."
        # Mandatory fallback (this session's decision) — a step with branch rules must also
        # say where to go if nothing matches, so the runtime never has to guess or retry-loop.
        if step.get("branches") and step.get("default_next") is None:
            return f"Step {i + 1}: has branch rules but no default_next — every branching step needs a fallback."
    return None


def _validate_end_action(end_action: dict) -> Optional[str]:
    tool = (end_action or {}).get("tool")
    if tool and tool not in END_ACTION_TOOLS:
        return f"end_action.tool must be one of {sorted(END_ACTION_TOOLS)}."
    return None


# ── CRUD (mirrors automations.py's shape) ──────────────────────────────────────

def list_flows(tenant_id: str) -> list[dict]:
    try:
        return (_client().table("commerce_flows").select("*")
                .eq("tenant_id", tenant_id).order("created_at", desc=True).execute().data or [])
    except Exception as exc:
        log.debug("flows list skipped (run migration 118?): %s", exc)
        return []


def create_flow(tenant_id: str, body: dict) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("A flow needs a name.")
    trigger_phrases = [p.strip().lower() for p in (body.get("trigger_phrases") or []) if (p or "").strip()]
    if not trigger_phrases:
        raise ValueError("A flow needs at least one trigger phrase.")
    if len(trigger_phrases) > MAX_TRIGGER_PHRASES:
        raise ValueError(f"At most {MAX_TRIGGER_PHRASES} trigger phrases per flow.")

    steps = body.get("steps") or []
    err = _validate_steps(steps)
    if err:
        raise ValueError(err)

    end_action = body.get("end_action") or {}
    err = _validate_end_action(end_action)
    if err:
        raise ValueError(err)

    existing = list_flows(tenant_id)
    if len(existing) >= MAX_FLOWS_PER_TENANT:
        raise ValueError(f"At most {MAX_FLOWS_PER_TENANT} flows per tenant — remove one first.")

    row = {
        "tenant_id": tenant_id, "name": name, "trigger_phrases": trigger_phrases,
        "steps": steps, "end_action": end_action, "enabled": bool(body.get("enabled", True)),
    }
    try:
        res = _client().table("commerce_flows").insert(row).execute()
    except Exception as exc:
        log.warning("flow create failed (run migration 118?): %s", exc)
        return {"error": "Flows aren't set up yet on this server (run migration 118)."}
    return res.data[0] if res.data else {"error": "insert failed"}


def update_flow(tenant_id: str, flow_id: str, patch: dict) -> dict:
    allowed = {"name", "trigger_phrases", "steps", "end_action", "enabled"}
    upd = {k: v for k, v in patch.items() if k in allowed}
    if "steps" in upd:
        err = _validate_steps(upd["steps"])
        if err:
            raise ValueError(err)
    if "end_action" in upd:
        err = _validate_end_action(upd["end_action"])
        if err:
            raise ValueError(err)
    if "trigger_phrases" in upd:
        upd["trigger_phrases"] = [p.strip().lower() for p in (upd["trigger_phrases"] or []) if (p or "").strip()]
        if not upd["trigger_phrases"]:
            raise ValueError("A flow needs at least one trigger phrase.")
    if not upd:
        return {}
    upd["updated_at"] = "now()"
    res = (_client().table("commerce_flows").update(upd)
           .eq("tenant_id", tenant_id).eq("id", flow_id).execute())
    return res.data[0] if res.data else {}


def delete_flow(tenant_id: str, flow_id: str) -> None:
    _client().table("commerce_flows").delete() \
        .eq("tenant_id", tenant_id).eq("id", flow_id).execute()


# ── Runtime ─────────────────────────────────────────────────────────────────

def _get_active_session(tenant_id: str, phone: str) -> Optional[dict]:
    try:
        rows = (_client().table("commerce_flow_sessions").select("*")
                .eq("tenant_id", tenant_id).eq("phone", _digits(phone)).eq("status", "active")
                .limit(1).execute().data or [])
        return rows[0] if rows else None
    except Exception as exc:
        log.debug("flow session lookup skipped (run migration 118?): %s", exc)
        return None


def _get_flow(tenant_id: str, flow_id: str) -> Optional[dict]:
    rows = (_client().table("commerce_flows").select("*")
            .eq("tenant_id", tenant_id).eq("id", flow_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _render_prompt(step: dict) -> str:
    prompt = step.get("prompt") or ""
    if step.get("reply_type") == "multiple_choice" and step.get("options"):
        numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(step["options"]))
        return f"{prompt}\n\n{numbered}"
    return prompt


def _is_idle(session: dict) -> bool:
    from datetime import datetime, timezone, timedelta
    try:
        updated = datetime.fromisoformat(str(session["updated_at"]).replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.now(timezone.utc) - updated > timedelta(hours=_IDLE_HOURS)


async def maybe_continue_flow(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """Is this phone already mid-flow? Advance its state, or complete it. Returns the message
    to send back, or None if this phone has no active flow (caller should fall through to
    normal routing)."""
    session = _get_active_session(tenant_id, phone)
    if not session:
        return None

    if _is_idle(session):
        try:
            _client().table("commerce_flow_sessions").update({"status": "abandoned"}) \
                .eq("id", session["id"]).execute()
        except Exception as exc:
            log.debug("flow session abandon failed: %s", exc)
        return None  # this message falls through to normal routing, not treated as a flow reply

    flow = _get_flow(tenant_id, session["flow_id"])
    if not flow or not flow.get("enabled"):
        try:
            _client().table("commerce_flow_sessions").update({"status": "abandoned"}) \
                .eq("id", session["id"]).execute()
        except Exception as exc:
            log.debug("flow session abandon (missing/disabled flow) failed: %s", exc)
        return None

    steps = flow.get("steps") or []
    idx = session.get("current_step_index") or 0
    if idx >= len(steps):
        return None  # shouldn't happen, but never crash on bad state
    step = steps[idx]

    branches = step.get("branches") or {}
    text_l = (text or "").lower()
    next_idx = None
    for keyword, target in branches.items():
        if keyword.lower() in text_l:
            next_idx = target
            break
    if next_idx is None:
        next_idx = step.get("default_next")

    answers = dict(session.get("collected_answers") or {})
    if step.get("save_as"):
        answers[step["save_as"]] = text

    if next_idx is None or next_idx >= len(steps):
        # Flow complete.
        closing = await _run_end_action(tenant_id, phone, flow, answers)
        try:
            from vula.commerce import service as cs
            _client().table("commerce_flow_sessions").update({
                "status": "completed", "collected_answers": answers, "completed_at": cs._now(),
            }).eq("id", session["id"]).execute()
            _client().table("commerce_flows").update(
                {"fire_count": (flow.get("fire_count") or 0) + 1}
            ).eq("id", flow["id"]).execute()
        except Exception as exc:
            log.debug("flow completion update failed: %s", exc)
        return closing

    try:
        _client().table("commerce_flow_sessions").update({
            "current_step_index": next_idx, "collected_answers": answers,
        }).eq("id", session["id"]).execute()
    except Exception as exc:
        log.debug("flow session advance failed: %s", exc)
    return _render_prompt(steps[next_idx])


async def maybe_start_flow(tenant_id: str, phone: str, text: str) -> Optional[str]:
    """Does this new inbound message match a trigger phrase? Returns the first step's prompt
    if a flow starts, or None (caller should fall through to normal routing)."""
    if _get_active_session(tenant_id, phone):
        return None  # never re-trigger mid-flow, even on an overlapping phrase

    try:
        flows = (_client().table("commerce_flows").select("*")
                 .eq("tenant_id", tenant_id).eq("enabled", True)
                 .order("created_at", desc=False).execute().data or [])
    except Exception as exc:
        log.debug("flow trigger check skipped (run migration 118?): %s", exc)
        return None

    text_l = (text or "").lower()
    matched = None
    for flow in flows:
        if any(phrase in text_l for phrase in (flow.get("trigger_phrases") or [])):
            matched = flow
            break
    if not matched:
        return None

    steps = matched.get("steps") or []
    if not steps:
        return None

    row = {"tenant_id": tenant_id, "phone": _digits(phone), "flow_id": matched["id"],
           "current_step_index": 0, "collected_answers": {}, "status": "active"}
    try:
        _client().table("commerce_flow_sessions").insert(row).execute()
    except Exception as exc:
        log.warning("flow session start failed: %s", exc)
        return None
    return _render_prompt(steps[0])


async def _run_end_action(tenant_id: str, phone: str, flow: dict, answers: dict) -> str:
    action = flow.get("end_action") or {}
    tool_name = action.get("tool")
    ctx_vars = {**answers, "__phone": phone, "__customer_name": answers.get("name") or "there"}

    if tool_name == "notify_team":
        # Same code path as automations.py's whatsapp_team action — no new send logic.
        from vula.escalation import _pick_helper
        from vula.api.whatsapp import _send_reply
        helper = _pick_helper(tenant_id)
        msg = _fill(action.get("message") or "New flow lead: {{__phone}}", ctx_vars)
        if helper and helper.get("whatsapp"):
            try:
                await _send_reply(helper["whatsapp"], msg, tenant_id=tenant_id)
            except Exception as exc:
                log.warning("flow notify_team send failed: %s", exc)
    elif tool_name:
        try:
            from core.skills.loader import get_skill
            skill = get_skill("commerce_admin")
            args = {k: _fill(str(v), ctx_vars) for k, v in (action.get("arg_map") or {}).items()}
            await skill._dispatch_tool(tool_name, args, {"tenant_id": tenant_id, "phone": phone})
        except Exception as exc:
            log.warning("flow end_action %s failed: %s", tool_name, exc)

    return _fill(action.get("closing_message") or "Thanks — we've got your details!", ctx_vars)
