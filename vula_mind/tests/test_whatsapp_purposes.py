"""Several WhatsApp numbers per business, each with a purpose (25 Oct, Ian: "giving them a choice
to use the number for clients or internal sales, admin, stores, booking … and what does it look
like if the client wants to add numbers").

- A reply goes from the number the person wrote to; Vula's own messages from the main number.
- Connecting a second number adds it (it used to replace the first).
- The owner picks each number's purpose; a team-only number points customers to the main one.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from config import settings
from vula import whatsapp_numbers as nums
from vula.api import whatsapp, whatsapp_connect

_real_creds = whatsapp._get_tenant_wa_creds   # conftest stubs the lookup for every other test
T = "harbour"


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload, self.conflict = db, table, [], "select", None, None

    def select(self, *_a, **_k): return self
    def limit(self, *_a): return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.conflict = "upsert", payload, on_conflict
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            key = self.conflict
            if key == "tenant_id" and self.db.multi:
                raise RuntimeError("there is no unique or exclusion constraint matching the ON CONFLICT")
            if key == "phone_number_id" and not self.db.multi and any(
                    r["tenant_id"] == self.payload["tenant_id"] and r["phone_number_id"] != self.payload["phone_number_id"]
                    for r in rows):
                raise RuntimeError('duplicate key value violates unique constraint "vula_whatsapp_accounts_tenant_id_key"')
            hit = next((r for r in rows if r.get(key) == self.payload.get(key)), None)
            if hit:
                hit.update(self.payload)
            else:
                rows.append(dict(self.payload, created_at=f"2026-10-25T10:0{len(rows)}:00"))
            return SimpleNamespace(data=[self.payload])
        hits = [r for r in rows if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return SimpleNamespace(data=[dict(r) for r in hits])


class _DB:
    def __init__(self, rows, multi=True):
        self.tables, self.multi = {"vula_whatsapp_accounts": rows}, multi

    def table(self, name):
        return _Q(self, name)


def _row(pnid, purpose="shop", main=False, status="connected", **kw):
    return {"tenant_id": T, "phone_number_id": pnid, "phone_number": f"+27 82 000 {pnid[-4:]}",
            "access_token": None, "status": status, "purpose": purpose, "route_mode": nums.PURPOSES[purpose],
            "is_primary": main, "created_at": f"2026-10-0{pnid[-1]}", **kw}


@pytest.fixture()
def db(monkeypatch):
    d = _DB([_row("P0001", "shop", main=True), _row("P0002", "team")])
    monkeypatch.setattr(settings, "whatsapp_token", "server-system-token")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "DEFAULT-NUMBER")
    monkeypatch.setattr("supabase.create_client", lambda *a, **k: d)
    monkeypatch.setattr(whatsapp_connect, "_supabase", lambda: d)
    monkeypatch.setattr("vula.commerce.service._client", lambda: d)
    monkeypatch.setattr(whatsapp_connect, "_default_purpose", lambda t: "shop")
    for c in (whatsapp._wa_creds_cache, whatsapp._WA_CREDS_AT, whatsapp._ROUTE_CACHE,
              whatsapp._ROUTE_PURPOSE, whatsapp_connect._HEALTH_CACHE, whatsapp._POINTED_AT):
        c.clear()
    return d


def _by(db, pnid):
    return next(r for r in db.tables["vula_whatsapp_accounts"] if r["phone_number_id"] == pnid)


# ── which number sends ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reply_goes_from_the_number_written_to_and_vulas_own_from_the_main_one(db):
    assert (await _real_creds(T))["phone_id"] == "P0001"                     # nothing inbound: main
    tok = nums.INBOUND.set({"pnid": "P0002", "purpose": "team", "from": "27820000111"})
    try:
        assert (await _real_creds(T, to="27820000111"))["phone_id"] == "P0002"   # replying on the team line
        assert (await _real_creds(T, "P0001"))["phone_id"] == "P0001"        # asked for explicitly
    finally:
        nums.INBOUND.reset(tok)


@pytest.mark.asyncio
async def test_a_number_of_another_business_or_a_disconnected_one_never_sends(db):
    _by(db, "P0002")["status"] = "disconnected"
    tok = nums.INBOUND.set({"pnid": "P0002"})
    try:
        assert (await _real_creds(T))["phone_id"] == "P0001"
    finally:
        nums.INBOUND.reset(tok)
    tok = nums.INBOUND.set({"pnid": "SOMEONE-ELSES"})
    try:
        assert (await _real_creds(T))["phone_id"] == "P0001"
    finally:
        nums.INBOUND.reset(tok)
    _by(db, "P0001")["status"] = "disconnected"
    whatsapp._forget_wa_number(T)
    assert await _real_creds(T) is None                                       # never Vula's own line


def test_main_number_without_a_mark_is_a_customer_number_not_the_team_line():
    rows = [_row("P0001", "team"), _row("P0002", "shop")]
    assert nums.main_row(rows)["phone_number_id"] == "P0002"
    assert nums.purpose_of({"route_mode": "knowledge"}) == "assistant"      # rows from before 225
    assert nums.purpose_of({}) == "shop"


# ── adding and changing numbers ──────────────────────────────────────────────

def test_a_second_number_is_added_not_swapped_in(db):
    whatsapp_connect.save_number(db, T, "P0003", {"tenant_id": T, "phone_number_id": "P0003",
                                                  "status": "connected"}, purpose="bookings")
    assert len(db.tables["vula_whatsapp_accounts"]) == 3
    new = _by(db, "P0003")
    assert new["purpose"] == "bookings" and new["route_mode"] == "commerce" and not new.get("is_primary")
    assert _by(db, "P0001")["is_primary"] is True


def test_the_first_number_is_the_main_one_and_never_team_only(db):
    db.tables["vula_whatsapp_accounts"] = []
    whatsapp_connect.save_number(db, T, "P0009", {"tenant_id": T, "phone_number_id": "P0009",
                                                  "status": "connected"}, purpose="team")
    row = _by(db, "P0009")
    assert row["is_primary"] is True and row["purpose"] == "shop"


def test_a_reconnected_number_keeps_its_purpose(db):
    whatsapp_connect.save_number(db, T, "P0002", {"tenant_id": T, "phone_number_id": "P0002",
                                                  "status": "connected"})
    assert _by(db, "P0002")["purpose"] == "team" and len(db.tables["vula_whatsapp_accounts"]) == 2


def test_before_migration_225_a_connect_is_stored_the_old_way(db):
    db.multi = False
    db.tables["vula_whatsapp_accounts"] = [_row("P0001", "shop")]
    whatsapp_connect.save_number(db, T, "P0005", {"tenant_id": T, "phone_number_id": "P0005",
                                                  "status": "connected"}, purpose="shop")
    assert [r["phone_number_id"] for r in db.tables["vula_whatsapp_accounts"]] == ["P0005"]


@pytest.mark.asyncio
async def test_changing_a_numbers_purpose_changes_how_it_is_answered(db):
    out = await whatsapp_connect.update_number(T, "P0002", whatsapp_connect.NumberUpdate(purpose="assistant"))
    assert out["purpose"] == "assistant" and _by(db, "P0002")["route_mode"] == "knowledge"
    assert whatsapp._resolve_number_route("P0002") == (T, "knowledge")


@pytest.mark.asyncio
async def test_the_main_number_cant_be_team_only_and_bad_input_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await whatsapp_connect.update_number(T, "P0001", whatsapp_connect.NumberUpdate(purpose="team"))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        await whatsapp_connect.update_number(T, "P0002", whatsapp_connect.NumberUpdate(is_primary=True))
    assert e.value.status_code == 409                                       # a team line can't be main
    with pytest.raises(HTTPException) as e:
        await whatsapp_connect.update_number(T, "P0002", whatsapp_connect.NumberUpdate(purpose="sales"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await whatsapp_connect.update_number(T, "NOPE", whatsapp_connect.NumberUpdate(purpose="shop"))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_making_another_number_main_moves_the_star(db):
    await whatsapp_connect.update_number(T, "P0002", whatsapp_connect.NumberUpdate(purpose="shop", is_primary=True))
    assert _by(db, "P0002")["is_primary"] is True and _by(db, "P0001")["is_primary"] is False
    assert (await _real_creds(T))["phone_id"] == "P0002"


@pytest.mark.asyncio
async def test_disconnecting_the_main_number_hands_main_to_another_customer_number(db):
    db.tables["vula_whatsapp_accounts"].append(_row("P0003", "bookings"))
    out = await whatsapp_connect.disconnect_whatsapp(T, "P0001")
    assert out["status"] == "connected"
    assert _by(db, "P0001")["status"] == "disconnected" and _by(db, "P0002")["status"] == "connected"
    assert _by(db, "P0003")["is_primary"] is True                             # not the team line
    with pytest.raises(HTTPException):
        await whatsapp_connect.disconnect_whatsapp(T, "NOT-OURS")


@pytest.mark.asyncio
async def test_disconnect_without_a_number_disconnects_them_all(db):
    out = await whatsapp_connect.disconnect_whatsapp(T)
    assert out["status"] == "disconnected"
    assert {r["status"] for r in db.tables["vula_whatsapp_accounts"]} == {"disconnected"}


@pytest.mark.asyncio
async def test_status_lists_every_number_main_first_and_keeps_the_old_top_level(db):
    out = await whatsapp_connect.whatsapp_status(T)
    assert out["phone_number_id"] == "P0001" and out["status"] == "connected"
    assert [(n["phone_number_id"], n["is_primary"], n["purpose"]) for n in out["numbers"]] == [
        ("P0001", True, "shop"), ("P0002", False, "team")]
    assert all("access_token" not in n for n in out["numbers"])
    assert out["numbers"][1]["purpose_label"] == "Team only"


# ── routing ──────────────────────────────────────────────────────────────────

def test_the_businesss_choice_beats_the_static_map(db, monkeypatch):
    monkeypatch.setitem(whatsapp._NUMBER_ROUTING, "P0002", ("someone", "commerce"))
    assert whatsapp._resolve_number_route("P0002") == (T, "team")
    assert whatsapp._ROUTE_PURPOSE["P0002"] == "team"


def test_the_static_map_still_routes_when_the_db_cant_be_read(monkeypatch):
    whatsapp._ROUTE_CACHE.clear()
    monkeypatch.setattr("vula.commerce.service._client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setitem(whatsapp._NUMBER_ROUTING, "STATIC-1", ("digg-demo", "knowledge"))
    assert whatsapp._resolve_number_route("STATIC-1") == ("digg-demo", "knowledge")


def test_a_redriven_message_remembers_its_number():
    tok = nums.INBOUND.set({"pnid": "P0002", "purpose": "team"})
    try:
        assert whatsapp._text_track("m1", T, "2782", "hi", "knowledge")["payload"]["pnid"] == "P0002"
    finally:
        nums.INBOUND.reset(tok)


# ── a team-only number ───────────────────────────────────────────────────────

def test_a_customer_on_the_team_line_is_pointed_to_the_main_number_once_in_a_while(db, monkeypatch):
    monkeypatch.setattr("vula.api.tenants.display_name", lambda t: "Harbour & Vine")
    msg = whatsapp._team_line_pointer(T)
    assert "Harbour & Vine's team" in msg and "https://wa.me/27820000001" in msg
    assert whatsapp._should_point_to_main(T, "2783", now=100.0) is True
    assert whatsapp._should_point_to_main(T, "2783", now=200.0) is False
    assert whatsapp._should_point_to_main(T, "2783", now=100.0 + whatsapp._POINT_EVERY_SECS) is True


def test_team_members_are_answered_as_on_the_main_number(db, monkeypatch):
    monkeypatch.setattr(whatsapp, "_is_tenant_owner", lambda t, p: p == "owner")
    assert whatsapp._team_line_mode(T, "owner") == "commerce"                # admin assistant
    assert whatsapp._team_line_mode(T, "driver") == "knowledge"
    _by(db, "P0001").update(route_mode="knowledge", purpose="assistant")
    assert whatsapp._team_line_mode(T, "owner") == "knowledge"               # a knowledge business


def test_the_webhook_routes_a_team_line_before_anything_answers():
    import inspect
    src = inspect.getsource(whatsapp.receive_message)
    assert src.index('route_mode == "team"') < src.index("_sender_over_limit")
    assert "_nums.INBOUND.set" in src


# ── a bookings number ────────────────────────────────────────────────────────

def test_a_bookings_number_is_bookings_first(monkeypatch):
    from core.skills import commerce_assistant as ca
    monkeypatch.setattr(ca, "_tenant_has_bookings", lambda t: True)
    monkeypatch.setattr(ca, "_tenant_business_type", lambda t: "retail")
    assert ca._is_booking_focused(T) is False
    tok = nums.INBOUND.set({"pnid": "P0003", "purpose": "bookings"})
    try:
        assert ca._is_booking_focused(T) is True
        monkeypatch.setattr(ca, "_tenant_has_bookings", lambda t: False)
        assert ca._is_booking_focused(T) is False                           # needs the bookings module
    finally:
        nums.INBOUND.reset(tok)


@pytest.mark.asyncio
async def test_on_the_team_line_only_the_person_who_wrote_is_answered_from_it(db):
    tok = nums.INBOUND.set({"pnid": "P0002", "purpose": "team", "from": "27820000111"})
    try:
        assert (await _real_creds(T, to="+27 82 000 0111"))["phone_id"] == "P0002"   # the rep
        assert (await _real_creds(T, to="27830000999"))["phone_id"] == "P0001"       # a customer
        assert (await _real_creds(T))["phone_id"] == "P0001"            # recipient unknown: main
    finally:
        nums.INBOUND.reset(tok)
    tok = nums.INBOUND.set({"pnid": "P0002", "purpose": "shop", "from": "27820000111"})
    try:   # any other line answers everyone from itself
        assert (await _real_creds(T, to="27830000999"))["phone_id"] == "P0002"
    finally:
        nums.INBOUND.reset(tok)
