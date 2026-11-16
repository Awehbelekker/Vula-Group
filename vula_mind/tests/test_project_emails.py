"""Email the project's client from the business's own mailbox, on record (vula/commerce/project_emails.py).

16 Nov 2026 (Ian, DIGG): "send him approval so that we can prove he has access" — an email from the
business's mailbox, a copy to that mailbox, and a row saying who sent what to whom and when."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vula.commerce import mail_router
from vula.commerce import project_emails as pe

T = "test-tenant"


class _Q:
    def __init__(self, db, name): self.db, self.name, self.f, self.patch = db, name, [], None
    def select(self, *a): return self
    def limit(self, *a): return self
    def eq(self, k, v): self.f.append(lambda r, k=k, v=v: r.get(k) == v); return self
    def update(self, row): self.patch = row; return self

    def insert(self, row):
        r = dict(row, id=f"e{len(self.db.rows) + 1}", created_at="2026-11-16T14:00:00")
        self.db.rows.append(r)
        self._ins = r
        return self

    def execute(self):
        if getattr(self, "_ins", None):
            return SimpleNamespace(data=[self._ins])
        hit = [r for r in self.db.rows if all(f(r) for f in self.f)]
        if self.patch is not None:
            for r in hit:
                r.update(self.patch)
        return SimpleNamespace(data=hit)


class _DB:
    def __init__(self): self.rows = []
    def table(self, n): return _Q(self, n)


def _run(sent_ok=True, mailbox="judy@digg.example", **kw):
    db, mails = _DB(), []

    async def send(tenant_id, to, subject, body, attachments=None):
        mails.append((to, subject, body))
        return sent_ok
    with (patch.object(pe, "_client", return_value=db), patch.object(mail_router, "send_tenant_email", send),
          patch.object(pe, "_mailbox", return_value=mailbox),
          patch("vula.commerce.service.canonical_project", lambda t, p: "Belladonna")):
        args = dict(to="RudiScheepers@gmail.com, christa@example.com", subject="Download access",
                    body="Dear Rudi and Christa …", by="judy")
        args.update(kw)
        try:
            out = asyncio.run(pe.send(T, "belladonna", **args))
        except (ValueError, RuntimeError) as exc:
            out = exc
    return out, db.rows, mails


def test_it_sends_from_the_mailbox_keeps_a_copy_and_records_it():
    out, rows, mails = _run()
    assert mails[0][0] == "rudischeepers@gmail.com, christa@example.com" and mails[0][1] == "Download access"
    assert mails[1][0] == "judy@digg.example" and mails[1][1] == "Copy: Download access"
    assert "rudischeepers@gmail.com, christa@example.com" in mails[1][2] and "Dear Rudi and Christa" in mails[1][2]
    assert out["status"] == "sent" and out["copy_to"] == "judy@digg.example" and out["sent_by"] == "judy"
    assert rows[0]["project"] == "Belladonna" and rows[0]["recipients"] == ["rudischeepers@gmail.com", "christa@example.com"]


def test_a_send_that_fails_is_recorded_and_said_plainly():
    out, rows, mails = _run(sent_ok=False)
    assert isinstance(out, RuntimeError) and "wasn't sent" in str(out)
    assert rows[0]["status"] == "failed" and rows[0]["error"] and len(mails) == 1      # no copy of an email that didn't go


@pytest.mark.parametrize("kw,msg", [
    ({"to": "rudi at gmail"}, "Not an email address"),
    ({"to": ""}, "Say who to send it to"),
    ({"to": ",".join(f"p{i}@x.co" for i in range(6))}, "At most 5"),
    ({"subject": " "}, "subject"),
    ({"body": ""}, "Write the email"),
])
def test_nothing_is_sent_without_an_address_a_subject_and_a_message(kw, msg):
    out, rows, mails = _run(**kw)
    assert isinstance(out, ValueError) and msg in str(out) and rows == [] and mails == []


def test_the_mailbox_copy_is_skipped_when_it_is_already_a_recipient():
    out, rows, mails = _run(to="judy@digg.example")
    assert len(mails) == 1 and out["copy_to"] is None
