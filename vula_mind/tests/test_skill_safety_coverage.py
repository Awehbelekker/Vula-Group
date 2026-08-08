"""CI guardrail: every skill must be explicitly triaged for prompt-injection fencing.

The 2026-08 accuracy audit found 6 skills (later corrected: 6 skills, not the originally
audited 1 — email_admin.py plus clickup_admin/google_admin/microsoft_admin/draft_admin/
finance_admin, found while classifying skills for this exact guardrail) that injected
externally-authored content (KB chunks, or tool results that can carry email/task/file text
nobody at the tenant wrote) into an LLM prompt with nothing checking for it. Before this test,
nothing would have caught that — or would catch a FUTURE skill shipping the same way. Coverage
was purely "whoever wrote/patched that skill remembered to."

Every skill in core/skills/loader.py's registry must now appear in either FENCED (and its
source file must actually import core.prompt_safety) or NO_EXTERNAL_CONTENT (reviewed and
justified with a one-line reason, not a bare exemption). An unclassified skill fails this test
loudly, forcing the decision to be made deliberately rather than silently skipped.
"""
import inspect

import pytest

from core.skills.loader import available_skills, get_skill

# Skills that inject externally-authored content into an LLM prompt (KB/RAG chunks, or tool
# results that can carry text nobody at the tenant wrote) — must import core.prompt_safety.
FENCED = {
    "reasoning", "web_search", "standards_lookup", "calculations", "architecture_planning",
    "commerce_assistant", "commerce_admin", "email_admin", "clickup_admin", "google_admin",
    "microsoft_admin", "draft_admin", "finance_admin",
}

# Skills confirmed by direct code read (not assumption) to never surface externally-authored
# text into an LLM prompt. Each needs a real reason.
NO_EXTERNAL_CONTENT = {
    "memory_recall": "no LLM generation at all — returns raw retrieved context or a fixed string",
    "file_parse": "raw-context path (run()) never hits an LLM; the one LLM-synthesis path "
                  "(_search_kb) delegates to vula.ingestion.pipeline.answer(), which is "
                  "itself already fenced",
}


def test_every_skill_is_classified():
    all_skills = set(available_skills())
    classified = FENCED | set(NO_EXTERNAL_CONTENT.keys())
    unclassified = all_skills - classified
    assert not unclassified, (
        f"Skill(s) {unclassified} aren't classified as FENCED or NO_EXTERNAL_CONTENT in "
        f"tests/test_skill_safety_coverage.py — a new skill must have this decision made "
        f"deliberately before it ships, not silently skipped.")

    stale = classified - all_skills
    assert not stale, (
        f"Classification for {stale} refers to a skill that no longer exists — remove it "
        f"from FENCED/NO_EXTERNAL_CONTENT.")


def test_no_classification_overlap():
    overlap = FENCED & set(NO_EXTERNAL_CONTENT.keys())
    assert not overlap, f"Skill(s) {overlap} are in both buckets — pick one."


@pytest.mark.parametrize("skill_name", sorted(FENCED))
def test_fenced_skill_actually_imports_prompt_safety(skill_name):
    module_name = type(get_skill(skill_name)).__module__
    import sys
    source = inspect.getsource(sys.modules[module_name])
    assert "prompt_safety" in source, (
        f"{skill_name} ({module_name}) is classified FENCED but its source doesn't import "
        f"core.prompt_safety — either it lost its fencing (a real regression) or it should "
        f"move to NO_EXTERNAL_CONTENT with a documented reason.")


# Lines that match the "json.dumps(result" heuristic but are provably NOT a prompt-injection
# site — reviewed individually, not a blanket escape hatch. Each entry needs the skill name,
# a distinctive substring of the line, and why it's safe.
KNOWN_SAFE_LINES = {
    ("finance_admin", "raw = self._numbers(json.dumps(result"):
        "feeds the accuracy anchor-check's number extraction (a local Python computation), "
        "never sent to an LLM prompt at all — the actual tool-result-to-prompt sites in this "
        "same file are fenced separately",
}


@pytest.mark.parametrize("skill_name", sorted(FENCED))
def test_fenced_skill_wraps_every_tool_result_injection(skill_name):
    """File-level "imports prompt_safety somewhere" isn't precise enough on its own — found
    live during this audit: commerce_admin.py and commerce_assistant.py each import fence()
    in ONE method (a web-search/KB helper) while a SEPARATE method (_agent_loop, the one
    behind real financial/stock mutations and customer-facing tool calls) injected
    json.dumps(result) completely unfenced, a few hundred lines away in the same file. A
    file-level substring check would never have caught that. This checks every individual
    line that serialises a tool result also fences it on that same line — the exact shape
    every fix in this codebase actually takes."""
    module_name = type(get_skill(skill_name)).__module__
    import sys
    source = inspect.getsource(sys.modules[module_name])
    safe_substrings = [sub for (name, sub), _reason in KNOWN_SAFE_LINES.items() if name == skill_name]
    unwrapped = [
        (i, line.strip()) for i, line in enumerate(source.splitlines(), start=1)
        if "json.dumps(result" in line and "fence(" not in line
        and not any(sub in line for sub in safe_substrings)
    ]
    assert not unwrapped, (
        f"{skill_name} ({module_name}) serialises a tool result without fencing it on "
        f"{len(unwrapped)} line(s): {unwrapped[:3]}{'...' if len(unwrapped) > 3 else ''} — "
        f"either wrap it in fence('LABEL', ...) or, if that specific result is genuinely "
        f"internal/curated data (not externally-authored text), leave a comment explaining "
        f"why and add a targeted # noqa-style acknowledgement here.")
