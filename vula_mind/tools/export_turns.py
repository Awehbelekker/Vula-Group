"""
tools/export_turns.py — candidate replay cases from the turn record (chat rework step 4).

Reads vula_turns (migration 195) for one tenant over a time window — read-only — and prints a
YAML block of text messages with the route and skill Vula actually took, ready to be reviewed and
copied into evals/cases/replay.yaml. What it observed is NOT automatically right: a reviewer
decides the expected route for each case (that's the point — a wrong route that was observed is
exactly the case to add, with the right expectation).

The repository is public, so the output deliberately leaves out phone numbers and replies, and the
reviewer removes anything personal or financial from a message before it goes into the corpus.
Write it to a file outside the repo (default: stdout).

Run from vula_mind/ with the production env (SUPABASE_URL / SUPABASE_SERVICE_KEY):

    python tools/export_turns.py digg-demo --since 2026-10-06 > /tmp/digg_cases.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict, List

import yaml


def _route(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for s in steps or []:
        if s.get("step") == "route":
            out = {"route": s.get("name"), "skill": s.get("skill")}
        elif s.get("step") == "skill" and "skill" not in out:
            out.setdefault("observed_skill", s.get("name"))
        elif s.get("step") == "handler":
            out.setdefault("handler", s.get("name"))
    return out


def _slug(text: str, i: int) -> str:
    words = re.findall(r"[a-z]+", text.lower())[:5]
    return "-".join(words) or f"case-{i}"


def candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, r in enumerate(rows):
        text = (r.get("text") or "").strip()
        if r.get("kind") != "text" or not text:
            continue
        seen = _route(r.get("steps") or [])
        out.append({
            "id": f"{_slug(text, i)}-{str(r.get('started_at') or '')[:10]}",
            "tenant": r.get("tenant_id"),
            "role": "owner",                       # reviewer: confirm the sender's role
            "text": text,
            "observed": seen,                      # what Vula did — not necessarily right
            "expect": {"route": seen.get("route"), "skill": seen.get("skill")},
        })
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tenant")
    ap.add_argument("--since", required=True, help="ISO date/time, e.g. 2026-10-06")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)
    from vula.commerce import service
    rows = (service._client().table("vula_turns")
            .select("tenant_id,kind,text,steps,started_at")
            .eq("tenant_id", args.tenant).gte("started_at", args.since)
            .order("started_at").limit(args.limit).execute().data or [])
    yaml.safe_dump({"cases": candidates(rows)}, sys.stdout, sort_keys=False, allow_unicode=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
