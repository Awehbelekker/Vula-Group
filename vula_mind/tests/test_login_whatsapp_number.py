"""A login made for a business (Settings › Team, or Master › Users › New login) can carry the
person's WhatsApp number: it goes on their team row, which WhatsApp sign-in and Vula's messages
to them go by (5 Nov 2026 — Master's form had no place for it, so a new business's owner had
no number on record)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vula.api.users import CreateUserIn, create_user

TID = "shop-eazy"


class _Team:
    """vula_team_members, just enough of the query chain create_user uses."""
    def __init__(self, rows):
        self.rows, self._where, self._op, self._vals = rows, [], None, None

    def table(self, name):
        assert name == "vula_team_members"
        t = _Team(self.rows)
        return t

    def select(self, *_):
        self._op = "select"; return self

    def insert(self, vals):
        self._op, self._vals = "insert", vals; return self

    def update(self, vals):
        self._op, self._vals = "update", vals; return self

    def eq(self, col, val):
        self._where.append((col, val)); return self

    def limit(self, _):
        return self

    def execute(self):
        match = [r for r in self.rows if all(r.get(c) == v for c, v in self._where)]
        if self._op == "insert":
            self.rows.append(dict(self._vals, id=f"m{len(self.rows) + 1}")); return SimpleNamespace(data=[self._vals])
        if self._op == "update":
            for r in match: r.update(self._vals)
            return SimpleNamespace(data=match)
        return SimpleNamespace(data=[{"id": r["id"]} for r in match])


async def _create(rows, **body):
    created = MagicMock(status_code=201)
    created.json.return_value = {"id": "user-1"}
    with (
        patch("vula.api.users._auth_admin", new=AsyncMock(return_value=created)),
        patch("vula.api.users._map_tenant_user"),
        patch("vula.api.users._client", return_value=_Team(rows)),
        patch("vula.api.users.merchant_audit.audit"),
        patch("vula.team_index.invalidate"),
    ):
        return await create_user(TID, CreateUserIn(**body), identity={"user_id": "master-1"})


@pytest.mark.asyncio
async def test_the_number_goes_on_the_team_row_as_digits():
    rows = []
    r = await _create(rows, email="owner@shopeazy.test", name="Thandi", role="owner", whatsapp="082 123 4567")
    assert r["temp_password"]
    assert rows == [dict(rows[0], tenant_id=TID, name="Thandi", email="owner@shopeazy.test", role="owner",
                         whatsapp="0821234567", active=True)]


@pytest.mark.asyncio
async def test_a_number_for_someone_already_on_the_team_by_email_joins_their_row():
    rows = [{"id": "m1", "tenant_id": TID, "name": "Thandi", "email": "owner@shopeazy.test", "role": "staff"}]
    await _create(rows, email="owner@shopeazy.test", role="owner", whatsapp="+27 82 123 4567")
    assert len(rows) == 1
    assert rows[0]["whatsapp"] == "27821234567" and rows[0]["role"] == "owner"


@pytest.mark.asyncio
async def test_no_number_given_keeps_the_one_on_record():
    rows = [{"id": "m1", "tenant_id": TID, "name": "Thandi", "email": "owner@shopeazy.test", "whatsapp": "0821234567"}]
    await _create(rows, email="owner@shopeazy.test", role="owner")
    assert len(rows) == 1 and rows[0]["whatsapp"] == "0821234567"
