"""11 Nov 2026 (DIGG): HPC's payment certificates 1 and 5-13 were in Judy's mailbox but never reached
Vula. The sync read INBOX and Sent only, and it dropped any emailed attachment whose FILENAME it had
seen before. Each test is the case that went wrong before the fix (migration 257)."""
import inspect
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.email_imap import sync as email_sync

NOW = datetime.now(timezone.utc)


def _raw(subject, when, attachment=("Payment Certificate.pdf", b"%PDF-1 PC5")):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Jodi <hello@jodidevilliers.com>", "judy@digg-ct.co.za", subject
    msg["Date"] = when.strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content("Please find the certificate attached.")
    if attachment:
        msg.add_attachment(attachment[1], maintype="application", subtype="pdf", filename=attachment[0])
    return msg.as_bytes()


class _Imap:
    """A mailbox: {folder: (flags, uidvalidity, {uid: raw bytes})}."""

    def __init__(self, folders):
        self.folders, self.selected, self.readonly = folders, None, []

    def list(self):
        return "OK", [f'({flags}) "/" "{name}"'.encode() for name, (flags, _v, _m) in self.folders.items()]

    def select(self, name, readonly=False):
        name = name.strip('"')
        if name not in self.folders or "\\Noselect" in self.folders[name][0]:
            return "NO", [b""]
        self.selected = name
        self.readonly.append(readonly)
        return "OK", [str(len(self.folders[name][2])).encode()]

    def response(self, code):
        return code, [str(self.folders[self.selected][1]).encode()]

    def uid(self, cmd, *args):
        msgs = self.folders[self.selected][2]
        if cmd == "search":
            lo = int(args[1].split()[1].split(":")[0])
            hits = [u for u in sorted(msgs) if u >= lo] or ([max(msgs)] if msgs else [])
            return "OK", [" ".join(map(str, hits)).encode()]
        uid = int(args[0])
        return "OK", [(f"{uid} (BODY[] {{{len(msgs[uid])}}}".encode(), msgs[uid]), b")"]

    def logout(self):
        pass


def _mailbox():
    old = NOW - timedelta(days=60)
    return {
        "INBOX": ("\\HasChildren", 1, {1: _raw("hi", NOW, None)}),
        "INBOX/HPC": ("\\HasNoChildren", 77, {i: _raw(f"PC {i}", old, (f"Payment Certificate No. {i}.pdf",
                                                                       f"%PDF PC{i}".encode()))
                                             for i in range(5, 14)}),
        "Sent": ("\\HasNoChildren \\Sent", 2, {1: _raw("sent", NOW, None)}),
        "Trash": ("\\HasNoChildren \\Trash", 3, {1: _raw("bin", NOW)}),
        "Junk E-mail": ("\\HasNoChildren", 4, {1: _raw("spam", NOW)}),
        "[Gmail]": ("\\Noselect \\HasChildren", 5, {}),
        "[Gmail]/All Mail": ("\\HasNoChildren \\All", 6, {1: _raw("dupe", NOW)}),
        "Archive": ("\\HasNoChildren", 9, {3: _raw("old supplier invoice", old, ("Invoice.pdf", b"%PDF inv"))}),
    }


# ── which folders ────────────────────────────────────────────────────────────────────────────

def test_list_replies_in_each_servers_shape_are_read():
    p = email_sync._parse_list_item
    assert p(b'(\\HasNoChildren) "/" "INBOX/HPC Payments"') == ({"\\hasnochildren"}, "INBOX/HPC Payments")
    assert p(b'(\\HasNoChildren) "." INBOX.HPC') == ({"\\hasnochildren"}, "INBOX.HPC")
    assert p(b'(\\Noselect \\HasChildren) "/" "[Gmail]"')[0] == {"\\noselect", "\\haschildren"}
    assert p((b'(\\HasNoChildren) "/" {14}', b'Jodi "HPC" PCs'))[1] == 'Jodi "HPC" PCs'
    assert p(b"garbage") is None and p(None) is None


@pytest.mark.parametrize("flags,name,skipped", [
    (set(), "INBOX/HPC", False), (set(), "Archive", False), (set(), "Projects/Belladonna", False),
    (set(), "INBOX", True), (set(), "Trash", True), (set(), "INBOX.Deleted Items", True),
    (set(), "Junk E-mail", True), (set(), "Drafts", True), ({"\\all"}, "[Gmail]/All Mail", True),
    ({"\\noselect"}, "[Gmail]", True), ({"\\sent"}, "Sent Items", True), (set(), "[Gmail]/Starred", True),
])
def test_only_folders_worth_reading_are_read(flags, name, skipped):
    assert email_sync._folder_skipped(flags, name, {"inbox", "sent"}) is skipped


def test_every_other_folder_is_read_read_only_and_shares_the_budget():
    box = _Imap(_mailbox())
    with patch.object(email_sync, "_imap_login", return_value=box):
        res = email_sync._fetch_other_folders({"email": "judy@digg-ct.co.za"}, {}, {"inbox", "sent"}, budget=6)
    assert res["folders"] == ["Archive", "INBOX/HPC"]
    got = [(e["folder"], e["uid"]) for e in res["emails"]]
    assert got == [("Archive", 3), ("INBOX/HPC", 5), ("INBOX/HPC", 6), ("INBOX/HPC", 7),
                   ("INBOX/HPC", 8), ("INBOX/HPC", 9)]
    assert res["emails"][1]["attachments"][0]["name"] == "Payment Certificate No. 5.pdf"
    assert res["cursors"] == {"Archive": {"uidvalidity": 9, "last_uid": 3}, "INBOX/HPC": {"uidvalidity": 77, "last_uid": 9}}
    assert all(box.readonly)                                    # EXAMINE: Judy's mail is untouched

    with patch.object(email_sync, "_imap_login", return_value=box):  # next sync carries on
        res = email_sync._fetch_other_folders({}, res["cursors"], {"inbox", "sent"}, budget=6)
    assert [e["uid"] for e in res["emails"]] == [10, 11, 12, 13]


def test_a_rebuilt_folder_is_read_again_from_the_start():
    box = _Imap(_mailbox())
    with patch.object(email_sync, "_imap_login", return_value=box):
        res = email_sync._fetch_other_folders({}, {"INBOX/HPC": {"uidvalidity": 1, "last_uid": 13}},
                                              {"inbox", "sent"}, budget=20)
    assert [e["uid"] for e in res["emails"] if e["folder"] == "INBOX/HPC"][0] == 5


# ── the sync run ─────────────────────────────────────────────────────────────────────────────

def _sync_db(account):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute \
        .return_value.data = [account]
    return db


async def _run(account, folder_emails, select_fails=False):
    db = _sync_db(account)
    if select_fails:
        real = db.table.return_value.select

        def select(cols, *a):
            if "folder_cursors" in cols:
                raise RuntimeError("column vula_email_accounts.folder_cursors does not exist")
            return real(cols, *a)
        db.table.return_value.select = select
    filed, tracked, saved = [], [], []
    other = MagicMock(return_value={"emails": folder_emails, "folders": ["INBOX/HPC"],
                                    "cursors": {"INBOX/HPC": {"uidvalidity": 77, "last_uid": 13}}, "oversized": []})
    with (patch.object(email_sync, "get_email_creds", return_value={"email": "judy@digg-ct.co.za"}),
          patch.object(email_sync, "_client", return_value=db),
          patch.object(email_sync, "_fetch_new", return_value={"emails": [], "max_uid": 5, "folder": "Sent",
                                                                "oversized": []}),
          patch.object(email_sync, "_fetch_other_folders", other),
          patch.object(email_sync, "_save_folder_cursors", side_effect=lambda d, a, c: saved.append(dict(c))),
          patch.object(email_sync, "_file_attachment",
                       new=AsyncMock(side_effect=lambda t, em, att, phone, quiet=False: filed.append((att["name"], quiet)))),
          patch.object(email_sync, "_track_followup", new=AsyncMock(side_effect=lambda *a: tracked.append(a[3]["uid"]))),
          patch.object(email_sync, "_needs_reply", return_value="request"),
          patch.object(email_sync, "_resolve_replied_followups", new=AsyncMock(return_value=0)),
          patch.object(email_sync, "_capture_voice_samples", new=AsyncMock())):
        res = await email_sync._do_email_sync("digg-demo", "acc1", 20)
    return res, other, filed, tracked, saved


def _em(uid, days_old, name):
    return {"uid": uid, "folder": "INBOX/HPC", "uidvalidity": 77, "when": (NOW - timedelta(days=days_old)).isoformat(),
            "people": [], "subject": "PC", "from": "hello@jodidevilliers.com", "body": "", "bulk": False,
            "attachments": [{"name": name, "data": name.encode()}]}


@pytest.mark.asyncio
async def test_payment_certificates_in_a_folder_are_filed_quietly():
    acct = {"auto_sync": True, "sync_all_folders": True, "folder_cursors": {}}
    res, other, filed, tracked, saved = await _run(acct, [_em(5, 60, "PC5.pdf"), _em(13, 0, "PC13.pdf")])
    assert filed == [("PC5.pdf", True), ("PC13.pdf", False)]    # old mail asks nobody; new mail as usual
    assert tracked == [13]                                     # no follow-up for a 2-month-old email
    assert saved[0] == {"INBOX/HPC": {"uidvalidity": 77, "last_uid": 5}}   # saved before PC5 is filed
    assert saved[-1]["INBOX/HPC"]["last_uid"] == 13
    assert other.call_args.args[2] >= {"inbox", "sent"}         # Inbox and Sent are read as before
    assert res["other_folder_emails"] == 2


@pytest.mark.asyncio
async def test_a_mailbox_switched_off_or_a_database_without_the_migration_reads_inbox_and_sent_only():
    _, other, filed, _, _ = await _run({"auto_sync": True, "sync_all_folders": False}, [_em(5, 60, "PC5.pdf")])
    assert other.call_count == 0 and filed == []
    _, other, filed, _, _ = await _run({"auto_sync": True}, [_em(5, 60, "PC5.pdf")], select_fails=True)
    assert other.call_count == 0 and filed == []


# ── duplicates are the same file, not the same name ──────────────────────────────────────────

class _FiledQ:
    def __init__(self, rows):
        self.rows, self.f = rows, []

    def select(self, *_):
        return self

    def eq(self, c, v):
        self.f.append(lambda r: r.get(c) == v)
        return self

    def is_(self, c, _null):
        self.f.append(lambda r: r.get(c) is None)
        return self

    def limit(self, *_):
        return self

    def execute(self):
        return SimpleNamespace(data=[r for r in self.rows if all(f(r) for f in self.f)])


def test_a_second_payment_certificate_pdf_is_not_a_duplicate_of_the_first():
    import hashlib
    pc5 = {"name": "Payment Certificate.pdf", "data": b"%PDF PC5"}
    rows = [{"tenant_id": "digg-demo", "source": "email", "filename": "Payment Certificate.pdf",
             "content_hash": hashlib.sha256(b"%PDF PC5").hexdigest()},
            {"tenant_id": "digg-demo", "source": "email", "filename": "Statement 06 Jul.pdf", "content_hash": None}]
    db = SimpleNamespace(table=lambda n: _FiledQ(rows))
    assert email_sync._already_filed(db, "digg-demo", pc5) is True
    assert email_sync._already_filed(db, "digg-demo", dict(pc5, data=b"%PDF PC6")) is False
    # filed before content hashes existed: still matched by name, so it isn't read into the bank twice
    assert email_sync._already_filed(db, "digg-demo", {"name": "Statement 06 Jul.pdf", "data": b"x"}) is True
    assert email_sync._already_filed(db, "other-shop", pc5) is False


def test_old_mail_never_asks_on_whatsapp_when_a_bill_is_booked():
    from vula.commerce import service
    src = inspect.getsource(service.commit_inbound_document)
    assert "notify: bool = True" in src
    assert "if ask_direction_of and notify:" in src
    assert "if needs_review and is_invoice and supplier_match and notify:" in src
    assert "notify=not quiet" in inspect.getsource(email_sync._file_attachment)


def test_every_tenants_mailboxes_get_all_folders():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "257_email_all_folders.sql").read_text()
    assert "sync_all_folders boolean not null default true" in sql


# ── the one-time catch-up when a mailbox is connected ────────────────────────────────────────

@pytest.mark.asyncio
async def test_connecting_a_mailbox_reads_its_whole_history_once(monkeypatch):
    rounds = iter([{"synced": 50}, {"synced": 0, "skipped": "sync already running"}, {"synced": 50},
                   {"synced": 12}, {"synced": 0}])
    calls = []

    async def sync(tenant_id, account_id, max_emails=20, from_uid=None):
        calls.append(max_emails)
        return next(rounds)
    db = MagicMock()
    monkeypatch.setattr(email_sync, "process_email_sync", sync)
    monkeypatch.setattr(email_sync, "_client", lambda: db)
    res = await email_sync.catch_up("digg-demo", "acc1", pause=0)
    assert res == {"synced": 112, "finished": True}
    assert calls == [email_sync.CATCH_UP_BATCH] * 5            # bigger batches than the 15-minute sync's 20
    db.table.return_value.update.assert_called_with({"history_read_at": "now()"})


@pytest.mark.asyncio
async def test_a_catch_up_that_hits_an_error_carries_on_next_time(monkeypatch):
    async def sync(*a, **k):
        return {"synced": 0, "error": "connection timed out"}
    db = MagicMock()
    monkeypatch.setattr(email_sync, "process_email_sync", sync)
    monkeypatch.setattr(email_sync, "_client", lambda: db)
    assert (await email_sync.catch_up("digg-demo", "acc1", pause=0))["finished"] is False
    db.table.return_value.update.assert_not_called()          # history_read_at stays empty


@pytest.mark.asyncio
async def test_one_catch_up_per_mailbox_at_a_time(monkeypatch):
    import asyncio
    gate = asyncio.Event()

    async def slow(tenant_id, account_id, **k):
        await gate.wait()
        return {"synced": 0, "finished": True}
    monkeypatch.setattr(email_sync, "catch_up", slow)
    email_sync._catch_ups.clear()
    assert email_sync.start_catch_up("digg-demo", "acc1") is True
    assert email_sync.start_catch_up("digg-demo", "acc1") is False
    assert email_sync.start_catch_up("digg-demo", "acc2") is True
    gate.set()
    await asyncio.gather(*email_sync._catch_ups.values())
    assert email_sync.start_catch_up("digg-demo", "acc1") is True  # finished: Backfill can start another
    gate.set()
    await asyncio.gather(*email_sync._catch_ups.values())
    email_sync._catch_ups.clear()


@pytest.mark.asyncio
async def test_a_new_mailboxs_old_inbox_mail_asks_nobody():
    """A mailbox connected today is read from its first email: months of Inbox mail must not each
    send a "which project?" WhatsApp or open a follow-up."""
    old = {"uid": 1, "when": (NOW - timedelta(days=200)).isoformat(), "people": [], "subject": "Invoice",
           "from": "a@supplier.co.za", "body": "", "bulk": False, "attachments": [{"name": "INV1.pdf", "data": b"1"}]}
    new = dict(old, uid=2, when=NOW.isoformat(), attachments=[{"name": "INV2.pdf", "data": b"2"}])
    db = _sync_db({"auto_sync": True, "sync_all_folders": False})
    filed, tracked = [], []
    with (patch.object(email_sync, "get_email_creds", return_value={"email": "owner@shop.co.za"}),
          patch.object(email_sync, "_client", return_value=db),
          patch.object(email_sync, "_fetch_new", side_effect=[
              {"emails": [old, new], "max_uid": 2, "folder": "INBOX", "oversized": []},
              {"emails": [], "max_uid": 0, "folder": None, "oversized": []}]),
          patch.object(email_sync, "_advance_cursor"),
          patch.object(email_sync, "_file_attachment",
                       new=AsyncMock(side_effect=lambda t, em, att, phone, quiet=False: filed.append((att["name"], quiet)))),
          patch.object(email_sync, "_track_followup", new=AsyncMock(side_effect=lambda *a: tracked.append(a[3]["uid"]))),
          patch.object(email_sync, "_needs_reply", return_value="request"),
          patch.object(email_sync, "_resolve_replied_followups", new=AsyncMock(return_value=0)),
          patch.object(email_sync, "_capture_voice_samples", new=AsyncMock())):
        await email_sync._do_email_sync("shop", "acc1", 50)
    assert filed == [("INV1.pdf", True), ("INV2.pdf", False)] and tracked == [2]


@pytest.mark.asyncio
async def test_backfill_reads_the_mailbox_again_from_the_start(monkeypatch):
    from vula.api import email_connect
    resets, started = [], []
    monkeypatch.setattr("vula.email_imap.credentials.list_email_accounts",
                        lambda t: [{"id": "acc1", "email": "judy@digg-ct.co.za"}])
    monkeypatch.setattr(email_sync, "reset_history", resets.append)
    monkeypatch.setattr(email_sync, "start_catch_up", lambda t, a: started.append(a) or True)
    res = await email_connect.backfill("digg-demo")
    assert resets == ["acc1"] and started == ["acc1"] and res["accounts"] == ["judy@digg-ct.co.za"]


def test_reset_puts_every_cursor_back_to_the_start():
    db = MagicMock()
    with patch.object(email_sync, "_client", return_value=db):
        email_sync.reset_history("acc1")
    updates = [c.args[0] for c in db.table.return_value.update.call_args_list]
    assert {"last_sync_uid": 0, "last_sync_uid_sent": 0} in updates
    assert {"folder_cursors": {}, "history_read_at": None} in updates


def test_every_mailbox_whose_history_is_unread_gets_a_catch_up():
    src = inspect.getsource(email_sync.process_all_email_sync)
    assert "_history_unread(account_id) and start_catch_up(tenant_id, account_id)" in src
    from vula.api import email_connect
    assert "start_catch_up(body.tenant_id, account_id)" in inspect.getsource(email_connect.connect)
