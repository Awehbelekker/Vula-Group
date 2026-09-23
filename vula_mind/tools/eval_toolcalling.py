"""
tools/eval_toolcalling.py — compare models on email_admin's real first tool-calling turn.

Built 2026-09-23 so a local model swap is decided on measured results, not reputation. Qwen3 /
Qwen3.5 look stronger on paper than llama3.1:8b, but have open Ollama bugs combining thinking with
tool calls (ollama#14601, #10976, #14745; litellm#18922), so "just swap it" could make things
worse on the 11 GB GTX 1080 Ti.

For each prompt it sends ONE completion (email_admin's system prompt + its full tool list,
exactly as production does) and records what the model did with it: which tool it picked, whether
the arguments parse, whether it added a `category` filter (a real miss on 2026-09-23), whether a
plain reply leaked JSON, and latency. Tools are NOT executed — nothing is read from or written to
any tenant; this only measures the model's decision.

Run where the Ollama tunnel is reachable (a Railway shell has OLLAMA_BASE and the CF-Access
token), from vula_mind/:

    python tools/eval_toolcalling.py --model ollama_chat/llama3.1:8b
    python tools/eval_toolcalling.py --model ollama_chat/qwen3:8b
    python tools/eval_toolcalling.py --model ollama/llama3.1:8b          # the old provider
    python tools/eval_toolcalling.py --model openrouter/meta-llama/llama-3.3-70b-instruct

Swap MODEL_WORKER only if a candidate clearly beats the current model on native chat.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (prompt, the tool a correct first move calls — None means "answer or ask, no tool needed")
PROMPTS = [
    ("Need all jack hammer invoices and a summary of what was spent", "find_document"),
    ("What materials did we buy from Jack Hammer?", "find_document"),
    ("How much have we spent with Gardens Handiman Centre this month?", "find_document"),
    ("Please check expenses from Jack Hammer", "find_document"),
    ("Find the invoice from Solid Cape for R7,571.44", "find_document"),
    ("Summarise all my emails from Coastal Hire", "email_thread_summary"),
    ("Any emails waiting on me to reply?", "list_followups"),
    ("Draft an email to Judy saying the site meeting moved to Friday", "find_contact"),
    ("Check my inbox for anything from the council", "email_search"),
    ("Thanks, that's all for now", None),
]


def _route(model: str):
    from config import settings
    from core.llm_router import OPENROUTER_BASE
    if model.startswith("openrouter/"):
        return model, settings.openrouter_api_key, OPENROUTER_BASE
    return model, None, settings.ollama_base


async def _one(model: str, prompt: str, system: str, tools: list) -> dict:
    import litellm
    from core.llm_router import generation_kwargs
    litellm.drop_params = True
    m, key, base = _route(model)
    t0 = time.monotonic()
    try:
        resp = await litellm.acompletion(
            model=m, api_key=key, api_base=base, tools=tools, tool_choice="auto",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=800, **generation_kwargs(m))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:80]}",
                "secs": round(time.monotonic() - t0, 1)}
    msg = resp.choices[0].message
    calls = getattr(msg, "tool_calls", None) or []
    out: dict = {"secs": round(time.monotonic() - t0, 1), "tool": None, "args_ok": None,
                 "category": False, "json_leak": False}
    if calls:
        fn = calls[0].function
        out["tool"] = fn.name
        try:
            args = json.loads(fn.arguments or "{}")
            out["args_ok"] = isinstance(args, dict)
            out["category"] = bool(args.get("category"))
        except (json.JSONDecodeError, TypeError):
            out["args_ok"] = False
    else:
        text = (msg.content or "").strip()
        out["json_leak"] = text.startswith("{") or '"name"' in text[:200]
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="litellm model, e.g. ollama_chat/qwen3:8b")
    a = ap.parse_args()

    from core.llm_router import install_ollama_auth
    from core.skills.email_admin import TOOL_SPECS, EmailAdminSkill
    install_ollama_auth()
    system = EmailAdminSkill()._system("draft")

    right = 0
    print(f"model: {a.model}\n")
    print(f"{'expected':22} {'got':22} {'args':5} {'cat':4} {'leak':5} {'secs':>5}  prompt")
    for prompt, expected in PROMPTS:
        r = await _one(a.model, prompt, system, TOOL_SPECS)
        if "error" in r:
            print(f"{str(expected):22} {'ERROR':22} {'':5} {'':4} {'':5} {r['secs']:>5}  "
                  f"{prompt[:50]}  <- {r['error']}")
            continue
        ok = r["tool"] == expected and r["args_ok"] is not False and not r["json_leak"]
        right += ok
        print(f"{str(expected):22} {str(r['tool']):22} {str(r['args_ok']):5} "
              f"{'Y' if r['category'] else '':4} {'Y' if r['json_leak'] else '':5} "
              f"{r['secs']:>5}  {prompt[:50]}")
    print(f"\ncorrect first move: {right}/{len(PROMPTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
