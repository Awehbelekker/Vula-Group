"""The WhatsApp turn record (migration 195, vula/turns.py): one row per message — what came in,
the steps Vula took, and every reply it sent in full."""
import pytest

from core.skills.base import BaseSkill, SkillInput, SkillOutput
from vula import turns


@pytest.fixture()
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(turns, "_save", lambda turn: rows.append({k: v for k, v in turn.items()
                                                                  if not k.startswith("_")}))
    return rows


class _Echo(BaseSkill):
    name = "commerce_admin"

    async def run(self, inp):
        from core.reasoning_telemetry import log_tool_call
        log_tool_call(inp.tenant_id, "admin", "find_document", {"query": "jack hammer", "phone": "2782"})
        return SkillOutput(answer="*GARDENS HANDIMAN CENTRE*: 34 documents", skill_name=self.name,
                           confidence=0.9)


@pytest.mark.asyncio
async def test_a_turn_records_the_message_its_steps_and_the_full_reply(saved, monkeypatch):
    from vula.api import whatsapp as wa
    monkeypatch.setattr(wa, "_get_tenant_wa_creds", lambda t: _none())
    monkeypatch.setattr(wa.settings, "whatsapp_token", "")

    async def handle():
        out = await _Echo()(SkillInput(question="Please give me all invoice for jack jammer",
                                       tenant_id="digg-demo"))
        await wa._send_reply("27645755210", out.answer + "\n" + "x" * 400, "digg-demo")

    await turns.run(handle(), tenant_id="digg-demo", phone="27645755210", kind="text",
                    text="Please give me all invoice for jack jammer", wamid="wamid.1")
    [row] = saved
    assert row["text"] == "Please give me all invoice for jack jammer" and row["outcome"] == "done"
    steps = [s["step"] for s in row["steps"]]
    assert steps == ["tool", "skill"]
    tool = row["steps"][0]
    assert tool["tool"] == "find_document" and "phone" not in tool["args"]      # POPIA-safe args
    assert row["steps"][1]["name"] == "commerce_admin" and row["steps"][1]["confidence"] == 0.9
    assert len(row["replies"][0]["text"]) > 400                                  # in full, not a preview
    assert "to" not in row["replies"][0]                                         # went to the sender


async def _none():
    return None


@pytest.mark.asyncio
async def test_a_reply_to_someone_else_is_marked(saved):
    async def handle():
        turns.reply("🔔 Approval needed", to="27827077080")
    await turns.run(handle(), tenant_id="digg-demo", phone="27645755210", kind="text")
    assert saved[0]["replies"][0]["to"] == "27827077080"


@pytest.mark.asyncio
async def test_a_failure_is_recorded_and_still_raised(saved):
    async def boom():
        turns.note("handler", name="pending_document")
        raise RuntimeError("db down")
    with pytest.raises(RuntimeError):
        await turns.run(boom(), tenant_id="digg-demo", phone="27645755210", kind="document",
                        text="Payment Notification (16).pdf")
    row = saved[0]
    assert row["outcome"] == "failed" and row["steps"][-1]["error"] == "RuntimeError: db down"


def test_outside_a_turn_nothing_happens():
    turns.note("tool", tool="x")
    turns.reply("hello")
    assert turns.current() is None


@pytest.mark.asyncio
async def test_a_failed_save_never_breaks_the_reply(monkeypatch):
    monkeypatch.setattr(turns, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no table")))

    async def handle():
        return "ok"
    assert await turns.run(handle(), tenant_id="t", phone="1", kind="text") == "ok"


@pytest.mark.asyncio
async def test_background_message_jobs_become_turns(saved):
    """_run_bg opens a turn from the message it was given (text, voice) — no handler changes."""
    import asyncio
    from vula.api import whatsapp as wa

    async def handler():
        turns.note("handler", name="test")
    wa._run_bg(handler(), label="t", track=None,
               turn={"tenant_id": "digg-demo", "phone": "27645755210", "kind": "document",
                     "text": "Payment Notification (16).pdf", "wamid": "w1"})
    for _ in range(50):
        if saved:
            break
        await asyncio.sleep(0.01)
    assert saved and saved[0]["kind"] == "document" and saved[0]["steps"][0]["name"] == "test"
