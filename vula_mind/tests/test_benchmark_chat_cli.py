"""scripts/benchmark_chat.py --yes gate (2026-09-11, pre-go-live brief item #3): the benchmark
suite runs real skills against Vula's real, live tenant IDs (digg-demo, off-the-hook) — a
confirm_flow scenario can create a real invoice and potentially send a real WhatsApp message.
Nothing must actually execute without an explicit --yes."""
import sys
from unittest.mock import AsyncMock, patch

import pytest

from scripts import benchmark_chat


@pytest.mark.asyncio
async def test_without_yes_nothing_runs_and_it_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["benchmark_chat.py"])
    with patch.object(benchmark_chat, "run_all", new=AsyncMock()) as run_all:
        with pytest.raises(SystemExit) as exc:
            await benchmark_chat.main()

    run_all.assert_not_called()
    assert exc.value.code != 0
    out = capsys.readouterr().out
    assert "--yes" in out
    assert "REAL" in out


@pytest.mark.asyncio
async def test_with_yes_it_actually_runs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["benchmark_chat.py", "--yes", "--id",
                                      benchmark_chat.ALL_SCENARIOS[0].id])
    with patch.object(benchmark_chat, "run_all", new=AsyncMock(return_value=[])) as run_all:
        await benchmark_chat.main()

    run_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_id_without_yes_still_reports_not_found_not_the_warning(monkeypatch, capsys):
    # An unknown --id/--category should fail its own way, before the --yes gate is even reached
    # — no change in behavior here, just confirming the new gate didn't swallow this path.
    monkeypatch.setattr(sys, "argv", ["benchmark_chat.py", "--id", "does_not_exist"])
    with patch.object(benchmark_chat, "run_all", new=AsyncMock()) as run_all:
        await benchmark_chat.main()

    run_all.assert_not_called()
    assert "No scenario with id" in capsys.readouterr().out
