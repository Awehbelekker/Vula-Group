"""
vula/retention.py — one place that deletes personal information Vula no longer needs (POPIA s14).

2026-10-31. Takeoff plans were the first data with a retention period (vula/takeoff/api.py,
purge_expired). Rather than each feature adding its own daily loop (vula/api/server.py already runs
~40), every kind of data that expires is one RULE here, and one daily sweep runs them all
(server.py::_retention_loop).

A rule is (name, the setting that holds its period in days, the function that deletes what is
older). A period of 0 keeps that data. One rule failing never stops the others. Each rule reports
counts only, never content.

Add a rule only for data with no legal minimum. Invoices, bank lines, payroll and other
accounting records must be kept for at least 5 years (Tax Administration Act s29), so they are
never swept here. See docs/security-model.md, "Retention".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    name: str
    setting: str                                  # attribute on config.settings, in days
    purge: Callable[[int], Dict[str, int]]        # (days) → counts deleted


def _takeoff(days: int) -> Dict[str, int]:
    from vula.takeoff.api import purge_expired
    return purge_expired(days=days)


RULES: List[Rule] = [
    Rule("takeoff_plans", "takeoff_retention_days", _takeoff),
]


def run_all(rules: List[Rule] = None) -> Dict[str, Dict[str, int]]:
    """Run every rule whose period is set. {rule name: counts, or {"error": 1}}."""
    from config import settings
    out: Dict[str, Dict[str, int]] = {}
    for rule in (RULES if rules is None else rules):
        days = int(getattr(settings, rule.setting, 0) or 0)
        if days <= 0:
            continue
        try:
            out[rule.name] = rule.purge(days)
        except Exception as exc:  # noqa: BLE001 — one rule never stops the rest
            log.warning("Retention rule %s failed: %s", rule.name, exc)
            out[rule.name] = {"error": 1}
    return out
