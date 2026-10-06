"""
core/supervisor.py — the last check on a reply the model wrote, before anyone reads it.

Step 3 of the chat rework (6 Oct 2026). The skill-level backstops (leaked tool output, a claimed
action that never happened) only saw what one skill passed them, and nothing checked a rand
figure in an owner reply against the business's records. The supervisor keeps, for each skill
run, everything the model could legitimately have taken a figure from — the question, the
conversation, every message sent to the model, every tool result — and then checks the answer:

  claims      "I've filed / booked / sent …" needs a state-changing tool that succeeded;
  figures     every rand amount must appear in that evidence (directly, as cents, or as the sum
              or difference of two figures that do) — owner replies only;
  arithmetic  any "A × B = C" shown must be right.

A failure never blocks the reply: only the offending sentence or line is dropped, an honest line
says so, and the turn record (vula/turns.py) notes what was caught (type labels only — POPIA).
Replies built in code from the records (reply_verbatim, direct database answers) are trusted
as-is: the figures there were never the model's.

Evidence lives in a context variable for the length of one skill run, so it works the same on
WhatsApp and in the dashboard chat, and never leaves the process.
"""
from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_RUN: ContextVar[Optional[dict]] = ContextVar("vula_supervisor_run", default=None)
_MAX_EVIDENCE_CHARS = 400_000
_MAX_AMOUNTS = 600           # pair sums are checked over at most this many distinct figures

FIGURE_NOTE = ("I've left out a figure I couldn't match to your records — ask me and I'll pull "
               "it up exactly.")
ARITHMETIC_NOTE = ("I've left out a calculation that didn't add up — ask me and I'll work it "
                   "out properly.")


# ── evidence for one skill run ────────────────────────────────────────────────

def start() -> Any:
    """Open a fresh evidence store for the skill run that's starting; returns the reset token."""
    return _RUN.set({"texts": [], "sources": [], "trusted": set(), "chars": 0})


def stop(token: Any) -> None:
    try:
        _RUN.reset(token)
    except Exception:
        pass


def _run() -> Optional[dict]:
    return _RUN.get()


def add(text: Any) -> None:
    """Something the model saw (a prompt, the question, a tool result, KB text)."""
    run = _run()
    if run is None or text in (None, ""):
        return
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    if run["chars"] + len(s) > _MAX_EVIDENCE_CHARS:
        s = s[: max(0, _MAX_EVIDENCE_CHARS - run["chars"])]
    if s:
        run["texts"].append(s)
        run["chars"] += len(s)


def add_source(source: Dict[str, Any], full_result: Any = None) -> None:
    """A tool result: its full text is evidence, and the source decides whether a state-changing
    tool succeeded this run."""
    run = _run()
    if run is None:
        return
    run["sources"].append(source)
    add(full_result if full_result is not None else source.get("text"))


def saw_messages(messages: Any) -> None:
    """The messages of one model call — everything in them is something the model was given."""
    if _run() is None or not isinstance(messages, list):
        return
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):          # multimodal content parts
            c = " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
        add(c)


def trust(text: str) -> str:
    """Mark a reply as built by code from the records — the supervisor leaves it untouched."""
    run = _run()
    if run is not None and text:
        run["trusted"].add(text.strip())
        add(text)
    return text


def sources() -> List[Dict[str, Any]]:
    run = _run()
    return list(run["sources"]) if run else []


# ── figures ───────────────────────────────────────────────────────────────────

# R1,150.00 · R 1 150.00 · R1150 · R92.5 — thousands may be split by commas or spaces.
_RAND_RE = re.compile(r"R\s?(\d{1,3}(?:[ ,]\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d.,]*\d)"
                      r"(?:\s?(k|m|mil|million)\b)?", re.IGNORECASE)
_SCALE = {"k": 1_000, "m": 1_000_000, "mil": 1_000_000, "million": 1_000_000}
_NUM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(re.sub(r"[ ,]", "", raw))
    except ValueError:
        return None


def _evidence_amounts(texts: Iterable[str]) -> set:
    """Every number in the evidence, read both as rands and (for whole numbers) as cents."""
    out = set()
    for t in texts:
        for m in _NUM_RE.finditer(t):
            v = _to_float(m.group(1))
            if v is None:
                continue
            out.add(round(v, 2))
            if "." not in m.group(1):
                out.add(round(v / 100, 2))
    return out


def unmatched_figures(answer: str, texts: List[str]) -> List[str]:
    """Rand amounts in `answer` that aren't in the evidence, nor the sum or difference of two
    figures that are (a model saying "R3,000 owed after the R1,000 payment" is fine)."""
    stated, rounded = [], []
    for m in _RAND_RE.finditer(answer or ""):
        v = _to_float(m.group(1))
        if v is None or v < 1:
            continue
        if m.group(2):           # "about R43k" — a rounded figure, held to within 2%
            rounded.append((m.group(0), v * _SCALE[m.group(2).lower()]))
        else:
            stated.append((m.group(0), round(v, 2)))
    if not stated and not rounded:
        return []
    known = _evidence_amounts(texts)
    bad = [raw for raw, v in stated if v not in known]
    bad += [raw for raw, v in rounded if not any(k and abs(k - v) <= 0.02 * k for k in known)]
    if not bad:
        return []
    stated = stated + rounded
    pool = sorted(a for a in known if a >= 1)[-_MAX_AMOUNTS:]
    derived = set()
    for a, b in combinations(pool, 2):
        derived.add(round(a + b, 2))
        derived.add(round(abs(a - b), 2))
    return [raw for raw, v in stated if raw in bad and v not in derived]


# ── dropping only the faulty parts ────────────────────────────────────────────

def _drop_parts(answer: str, is_bad, note: str) -> str:
    """Remove each line — or, within a prose line, each sentence — that `is_bad` flags; keep the
    rest in place and add `note`."""
    kept_lines = []
    for line in (answer or "").split("\n"):
        if not is_bad(line):
            kept_lines.append(line)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", line)
        if len(sentences) > 1:
            keep = " ".join(s for s in sentences if not is_bad(s)).strip()
            if keep:
                kept_lines.append(keep)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    return f"{body}\n\n{note}" if body else note


# ── the check ─────────────────────────────────────────────────────────────────

def check(answer: str, *, skill: str, tenant_id: Optional[str] = None, customer: bool = False,
          extra_sources: Iterable[Dict[str, Any]] = (), check_figures: bool = True,
          evidence: Iterable[str] = ()) -> Tuple[str, List[str]]:
    """Return (answer, findings). `evidence` adds texts beyond the current run's (for callers
    outside a skill run, e.g. the confirm-tap summary)."""
    if not answer or not answer.strip():
        return answer, []
    run = _run()
    if run is not None and answer.strip() in run["trusted"]:
        return answer, []
    findings: List[str] = []
    from core.skills.base import (substitute_if_unbacked_claim, unbacked_action_claim,
                                  wrong_arithmetic)

    srcs = list(extra_sources) + (run["sources"] if run else [])
    if not customer and unbacked_action_claim(answer, srcs):
        answer = substitute_if_unbacked_claim(answer, srcs, skill=skill, tenant_id=tenant_id)
        findings.append("unbacked_claim")

    wrong = wrong_arithmetic(answer)
    if wrong:
        claims = [w.get("claim") or "" for w in wrong]
        answer = _drop_parts(answer, lambda s: any(c and c in s for c in claims), ARITHMETIC_NOTE)
        findings.append("wrong_arithmetic")

    # Figures are only checked when a tool actually returned something this run — with no tool
    # result there's nothing in the business's records to check a figure against.
    texts = list(evidence) + (run["texts"] if run else [])
    if check_figures and not customer and srcs and texts:
        bad = unmatched_figures(answer, texts)
        if bad:
            answer = _drop_parts(answer, lambda s: any(b in s for b in bad), FIGURE_NOTE)
            findings.append("unverified_figure")

    if findings:
        logger.warning("supervisor caught %s, skill=%s tenant=%s", findings, skill, tenant_id)
        try:
            from vula import turns
            turns.note("supervisor", skill=skill, caught=",".join(findings))
        except Exception:
            pass
        try:
            from core.reasoning_telemetry import emit
            emit(system="vula-supervisor", task=skill, outcome="corrected", escalated=False,
                 tenant_id=tenant_id, extra={"findings": findings})
        except Exception:
            pass
    return answer, findings


# ── seeing every model call ───────────────────────────────────────────────────

def install() -> None:
    """Record the messages of every litellm.acompletion call made during a skill run as evidence.
    Idempotent; a no-op if litellm isn't importable."""
    try:
        import litellm
    except Exception:
        return
    current = getattr(litellm, "acompletion", None)
    if current is None or getattr(current, "_vula_supervised", False):
        return

    async def acompletion(*args, **kwargs):
        try:
            saw_messages(kwargs.get("messages"))
        except Exception:
            pass
        return await current(*args, **kwargs)

    acompletion._vula_supervised = True
    litellm.acompletion = acompletion
