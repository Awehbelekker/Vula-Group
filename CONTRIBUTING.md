# Contributing to Universal Soul AI

Thank you for wanting to contribute. The most impactful way to contribute right now is **building skills**.

---

## Adding a Skill

Skills are the growth engine of Universal Soul. Every new skill makes the system more capable for everyone using it.

### Step 1: Create the skill file

Create `core/skills/your_skill_name.py`:

```python
"""
core/skills/your_skill_name.py

Brief description of what this skill does.
"""

from __future__ import annotations
from typing import Any, Dict


class YourSkillName:
    """
    What this skill does.
    When HRM routes tasks here, what happens.
    """

    async def execute(self, subtask: str, context: Dict[str, Any]) -> str:
        """
        Execute the skill.
        
        Args:
            subtask: The specific task string from the TaskBranch
            context: Dict with goal, memory, session_id etc.
            
        Returns:
            String result to be merged by ThinKMesh
        """
        # Your implementation here
        result = f"Processed: {subtask}"
        return result
```

### Step 2: Add to registry

Add an entry to `core/skills/registry.json`:

```json
{
  "name": "your_skill_name",
  "description": "One sentence description",
  "trigger_keywords": [
    "keyword1", "keyword2", "phrase that triggers this skill"
  ],
  "model_tier": "7b",
  "device_pref": "any",
  "timeout_ms": 30000,
  "module": "core.skills.your_skill_name"
}
```

**model_tier options:** `"1.5b"` `"7b"` `"14b"` `"32b"`
**device_pref options:** `"any"` `"desktop"` `"laptop"` `"mobile"`

### Step 3: Add a test

Create `tests/test_skill_your_skill_name.py`:

```python
import pytest
from core.skills.your_skill_name import YourSkillName

@pytest.mark.asyncio
async def test_basic_execution():
    skill = YourSkillName()
    result = await skill.execute("test input", {})
    assert isinstance(result, str)
    assert len(result) > 0
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
- Async-first (`async def execute`)
- No external API keys in skill code — document any requirements in the skill docstring
- Format with `black`, lint with `ruff`

---

## Questions?

Open a GitHub Issue or Discussion.
