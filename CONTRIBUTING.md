# Contributing to Universal Soul AI

Thank you for wanting to contribute. The most impactful way to contribute right now is **building skills**.

---

## Adding a Skill

Skills are the growth engine of Universal Soul. Every new skill makes the system more capable for everyone using it.

### Step 1: Create the skill file

Create `core/skills/your_skill_name.py`. Every skill subclasses `BaseSkill`
(`core/skills/base.py`) and implements one async method, `run(inp: SkillInput) -> SkillOutput`:

```python
"""
core/skills/your_skill_name.py

Brief description of what this skill does.
"""
from __future__ import annotations

from core.skills.base import BaseSkill, SkillInput, SkillOutput, behaviour_preamble


class YourSkillName(BaseSkill):
    name = "your_skill_name"
    description = "One sentence description"
    # "none" | "deterministic" | "adversarial" — see core/verification.py.
    verification_policy = "none"

    async def run(self, inp: SkillInput) -> SkillOutput:
        # inp.question, inp.tenant_id, inp.context, inp.conversation_history, inp.metadata
        answer = f"Processed: {inp.question}"
        return SkillOutput(answer=answer, skill_name=self.name, confidence=0.8)
```

`BaseSkill.__call__` wraps `run()` with timing, exception capture, and the verification hook —
don't reimplement those.

### Step 2: Register it

- Add the instance to `_SKILLS` in `core/skills/loader.py` (this is the authoritative list of
  what's implemented).
- Add routing keywords to `SKILL_KEYWORDS` in `core/hrm/orchestrator.py` — **order matters**,
  it's a first-match ordered table; read the collision comments there before inserting.
- Add a catalogue entry to `core/skills/registry.json` (kept in sync with `_SKILLS` by
  `tests/test_orchestrator.py::test_skill_registry_matches_real_implemented_skills`; it is
  **not** consulted for routing).

### Step 3: Add a test

Create `tests/test_skill_your_skill_name.py` (async tests run without a marker —
`asyncio_mode = "auto"` in `pyproject.toml`):

```python
from core.skills.base import SkillInput
from core.skills.your_skill_name import YourSkillName


async def test_basic_execution():
    out = await YourSkillName()(SkillInput(question="test input", tenant_id="t"))
    assert out.success and out.answer
```

### Step 4: Submit a PR

1. Fork the repository
2. Create a branch: `git checkout -b skill/your-skill-name`
3. Commit your changes
4. Open a pull request with a brief description of what the skill does

---

## Skill Ideas Wanted

High-value skills the community could build:

- `weather` — real-time weather and conditions
- `calendar` — read/write calendar events locally
- `email_draft` — compose emails from natural language
- `translation` — multilingual support (especially Afrikaans)
- `pdf_extract` — deep PDF analysis beyond basic parsing
- `sql_query` — natural language to SQL
- `watersports_conditions` — wind/swell/tide for South African spots (👀 FlowCrew)
- `building_code` — SANS 10400 compliance checking (👀 DIGG)

---

## Code Standards

- Python 3.11+
- Type hints on all functions
- Async-first (`async def run`)
- No external API keys in skill code — per-tenant credentials live in the DB (see `config.py`)
- Lint with `ruff check vula/ core/` (config in `vula_mind/pyproject.toml`; CI gates on real
  errors only, not style)
- Money is always integer cents, computed server-side — never let the LLM do the arithmetic

---

## Questions?

Open a GitHub Issue or Discussion.
