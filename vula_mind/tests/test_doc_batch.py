"""One reply for a batch of documents (vula/api/doc_batch.py, 10 Nov 2026).

Judy sent ~20 documents in a row; each got its own reply as it finished, and the replies kept coming
for ten minutes after she stopped. Now a batch gets one summary; a single document is unchanged."""
import asyncio

import pytest

from vula.api import doc_batch as db

PHONE = "27645550000"


@pytest.fixture(autouse=True)
def _clean():
    db._bursts.clear()
    yield
    db._bursts.clear()


def _sender():
    sent = []

    async def send(to, message, tenant_id):
        sent.append((to, message, tenant_id))
        return True
    return sent, send


async def _document(i, sent_now, send, hold=0.0, reply=True):
    """One document's work: replies through capture() like _send_reply does."""
    token = db.enter(PHONE)
    try:
        await asyncio.sleep(hold)
        if reply and not db.capture(PHONE, f"Filed invoice {i}", "t1"):
            sent_now.append(f"Filed invoice {i}")
    finally:
        await db.leave(PHONE, token, send, grace=0.01)


@pytest.mark.asyncio
async def test_one_document_is_answered_at_once_as_before():
    sent, send = _sender()
    now = []
    await _document(1, now, send)
    await asyncio.sleep(0.05)
    assert now == ["Filed invoice 1"] and sent == [] and db._bursts == {}


@pytest.mark.asyncio
async def test_a_batch_gets_one_numbered_summary_after_the_last_document():
    sent, send = _sender()
    now = []
    # the first is still being read when the others arrive — as on WhatsApp
    await asyncio.gather(*[_document(i, now, send, hold=0.02 * i) for i in range(1, 6)])
    await asyncio.sleep(0.05)
    assert now == []                                       # nothing went out one by one
    assert len(sent) == 1
    to, text, tenant = sent[0]
    assert to == PHONE and tenant == "t1"
    assert text.startswith("📄 Done — 5 documents worked through:")
    assert "*1.* Filed invoice 1" in text and "*5.* Filed invoice 5" in text


@pytest.mark.asyncio
async def test_a_document_that_arrives_before_the_summary_joins_it():
    sent, send = _sender()
    now = []
    await asyncio.gather(_document(1, now, send, hold=0.01), _document(2, now, send, hold=0.01))
    await _document(3, now, send)                         # within the grace period
    await asyncio.sleep(0.05)
    assert len(sent) == 1 and "3 documents" in sent[0][1]


@pytest.mark.asyncio
async def test_a_message_typed_meanwhile_is_never_held():
    sent, send = _sender()
    seen = {}
    started = asyncio.Event()
    release = asyncio.Event()

    async def document():                                 # two documents being worked on
        token = db.enter(PHONE)
        started.set()
        await release.wait()
        seen["other_sender"] = db.capture("27820000000", "hello", "t1")   # someone else: not held
        await db.leave(PHONE, token, send, grace=0.01)

    tasks = [asyncio.create_task(document()), asyncio.create_task(document())]
    await started.wait()
    # the sender types a question: answered on its own request, outside the documents' work
    assert db.capture(PHONE, "what do I owe?", "t1") is False
    release.set()
    await asyncio.gather(*tasks)
    assert seen["other_sender"] is False


@pytest.mark.asyncio
async def test_send_reply_holds_document_replies_but_not_the_got_it(monkeypatch):
    from vula.api import whatsapp as wa
    out = []

    async def creds(tenant_id, to=None):
        return None                                        # never reaches Meta in a test
    monkeypatch.setattr(wa, "_send_creds", creds)
    monkeypatch.setattr(wa, "_claim_outbound", lambda t, k: True)

    async def fake_send(to, message, tenant_id="", idem_key=None):
        out.append(message)
        return True
    t1 = db.enter(PHONE)
    t2 = db.enter(PHONE)
    assert await wa._send_reply(PHONE, "Filed invoice 1044", "digg") is True      # held
    assert db._bursts[db._key(PHONE)].items == [("digg", "Filed invoice 1044")]
    await wa._send_reply(PHONE, "📄 Got it", "digg", idem_key="doc-ack:x")      # not held
    assert db._bursts[db._key(PHONE)].items == [("digg", "Filed invoice 1044")]
    await db.leave(PHONE, t2, fake_send, grace=0.01)
    await db.leave(PHONE, t1, fake_send, grace=0.01)
    await asyncio.sleep(0.05)
    assert len(out) == 1 and "Filed invoice 1044" in out[0]
