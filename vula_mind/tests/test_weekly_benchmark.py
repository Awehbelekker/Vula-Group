"""The capability benchmark runs itself on Sunday night and Monday's email says what changed
(vula/weekly_benchmark.py, 1 Nov 2026). Made-up components and prompts."""
import asyncio
from datetime import datetime

from config import settings
from vula import weekly_benchmark as wb

SAST = wb.SAST


def test_due_only_sunday_night_sast():
    assert wb.due(datetime(2026, 11, 8, 22, 5, tzinfo=SAST))          # Sunday 22:05
    assert wb.due(datetime(2026, 11, 8, 23, 59, tzinfo=SAST))
    assert not wb.due(datetime(2026, 11, 8, 21, 59, tzinfo=SAST))
    assert not wb.due(datetime(2026, 11, 9, 0, 30, tzinfo=SAST))      # Monday
    assert not wb.due(datetime(2026, 11, 7, 22, 30, tzinfo=SAST))     # Saturday


def test_start_runs_once_and_respects_the_switch(monkeypatch):
    sunday = datetime(2026, 11, 8, 22, 15, tzinfo=SAST)
    started = []

    class _T:
        def __init__(self):
            self.row = None

        def insert(self, row):
            self.row = row
            return self

        def execute(self):
            return type("R", (), {"data": [{"id": "run-1", **(self.row or {})}]})()

    monkeypatch.setattr(wb, "_client", lambda: type("C", (), {"table": lambda s, n: _T()})())
    import vula.api.master as master

    async def fake_run(job_id, judge, only):
        started.append((job_id, judge, only))
    monkeypatch.setattr(master, "_run_benchmark", fake_run)

    monkeypatch.setattr(wb, "already_started", lambda now: False)
    res = asyncio.run(wb.start(sunday))
    assert res == {"started": True, "id": "run-1"} and started[0][0] == "run-1" and started[0][2] is None

    monkeypatch.setattr(wb, "already_started", lambda now: True)
    assert asyncio.run(wb.start(sunday))["started"] is False and len(started) == 1

    monkeypatch.setattr(wb, "already_started", lambda now: False)
    assert asyncio.run(wb.start(datetime(2026, 11, 9, 10, 0, tzinfo=SAST)))["reason"] == "not due"
    monkeypatch.setattr(settings, "weekly_benchmark", False)
    assert asyncio.run(wb.start(sunday))["reason"] == "WEEKLY_BENCHMARK is off"


def test_marker_check_failure_skips_rather_than_floods(monkeypatch):
    monkeypatch.setattr(wb, "_client", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert wb.already_started(datetime(2026, 11, 8, 22, 15, tzinfo=SAST)) is True


def _report(passed, comps, rows):
    return {"passed": passed, "total": sum(c[2] for c in comps),
            "components": [{"component": n, "passed": p, "total": t} for n, p, t in comps],
            "rows": [{"id": i, "ok": ok} for i, ok in rows],
            "gaps": [{"id": i, "prompt": f"question {i}"} for i, ok in rows if not ok]}


def test_compare_names_what_got_worse_and_better():
    prev = _report(8, [("Owner admin", 4, 4), ("Search", 1, 4), ("Filing", 3, 3)],
                   [("a1", True), ("a2", True), ("s1", False), ("s2", True)])
    cur = _report(7, [("Owner admin", 2, 4), ("Search", 3, 4), ("Filing", 3, 3)],
                  [("a1", False), ("a2", True), ("s1", True), ("s2", True)])
    c = wb.compare(cur, prev)
    assert c["worse"] == [{"component": "Owner admin", "now": "2/4", "before": "4/4"}]
    assert c["better"] == [{"component": "Search", "now": "3/4", "before": "1/4"}]
    assert c["newly_failing"] == [{"id": "a1", "prompt": "question a1"}]
    assert c["top_failures"][0] == {"component": "Owner admin", "failing": 2, "total": 4}
    lines = wb.render_lines({**c, "weekly": True})
    assert lines[0].startswith("Capability benchmark: 7/11 passed (previous run 8/11)")
    assert any(line.startswith("Got worse: Owner admin 4/4 → 2/4") for line in lines)


def test_compare_with_no_previous_run():
    c = wb.compare(_report(3, [("Search", 3, 4)], [("s1", True)]), None)
    assert c["before"] is None and c["worse"] == [] and c["newly_failing"] == []
    assert "started by hand" in wb.render_lines({**c, "weekly": False})[0]


def test_digest_carries_the_benchmark_section():
    from vula import master_digest as md
    d = {"tenants": [], "counts": {"red": 0, "amber": 0, "green": 0, "off": 0}, "dormant": [],
         "missing_migrations": [],
         "benchmark": {**wb.compare(_report(5, [("Search", 1, 2)], [("s1", False)]), None), "weekly": True}}
    assert "Capability benchmark: 5/2 passed" in md.render_text(d)
    html_out = md.render_html(d)
    assert "Capability benchmark" in html_out and "Most failures: Search (1 of 2)" in html_out
    d["benchmark"] = None
    assert "Capability benchmark" not in md.render_text(d)
