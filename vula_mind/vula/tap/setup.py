"""Self-serve setup for tap-to-pay: checklist status, per-person split, tags, R5 test payment,
go-live / pause. Pure orchestration over `ports.Repo` so it is tested with the in-memory fake.

Modes (kb_settings.mode): off -> testing -> live. Going live requires a confirmed test payment
(`tested_at`, set by TapService.confirm_payment when an is_test bill is paid) so a mis-keyed PayFast
account or wrong passphrase is caught before a customer ever sees it."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable, Optional

from vula.tap.core import money as mo
from vula.tap.service import TapService

TEST_AMOUNT_CENTS = 500


class SetupError(ValueError):
    """User-facing problem (shown verbatim in the dashboard)."""


@dataclass
class Setup:
    svc: TapService
    base_url: str
    payfast_lookup: Callable[[str], Optional[dict]]     # tenant -> {"connected": bool, "mode": str} | None
    on_mode_change: Callable[[str], None] = lambda tenant: None

    @property
    def repo(self):
        return self.svc.repo

    # ── read ─────────────────────────────────────────────────────────────────────────────────
    def tag_links(self, code: str) -> dict:
        return {"code": code, "url": f"{self.base_url}/t/{code}", "qr": f"{self.base_url}/t/{code}/qr.svg"}

    def status(self, tenant: str) -> dict:
        cfg = self.repo.get_settings(tenant) or {}
        pf = self.payfast_lookup(tenant) or {"connected": False, "mode": None}
        wa = self.repo.tenant_wa_number(tenant)
        rules = {r.get("scope"): r for r in self.repo.list_split_rules(tenant)}
        tags = {t["bound_id"]: t for t in self.repo.list_tags(tenant)
                if t.get("status") == "active" and t.get("bound_id")}
        staff = []
        for m in self.repo.team_members(tenant):
            rule = rules.get(f"staff:{m['id']}") or rules.get("default") or {}
            tag = tags.get(m["id"])
            staff.append({"id": m["id"], "name": m.get("name") or "Unnamed", "whatsapp": m.get("whatsapp"),
                          "share_bp": int(rule.get("staff_share_bp", 0)),
                          "tag": self.tag_links(tag["code"]) if tag else None})
        has_tag = any(s["tag"] for s in staff)
        tested = bool(cfg.get("tested_at"))
        blockers = []
        if not pf["connected"]:
            blockers.append("Connect your PayFast account.")
        if not wa:
            blockers.append("Connect your WhatsApp number (Settings).")
        if not has_tag:
            blockers.append("Create a tag for at least one team member.")
        return {
            "mode": cfg.get("mode", "off"), "tested": tested, "payfast": pf,
            "whatsapp": {"connected": bool(wa), "number": wa},
            "default_share_bp": int((rules.get("default") or {}).get("staff_share_bp", 0)),
            "staff": staff, "blockers": blockers,
            "can_test": not blockers,
            "can_go_live": not blockers and tested,
        }

    # ── write ────────────────────────────────────────────────────────────────────────────────
    def save_shares(self, tenant: str, default_bp: int, staff_shares: dict[str, int]) -> None:
        members = {m["id"] for m in self.repo.team_members(tenant)}
        for bp in [default_bp, *staff_shares.values()]:
            if not isinstance(bp, int) or not 0 <= bp <= mo.BP:
                raise SetupError("A share must be between 0% and 100%.")
        unknown = set(staff_shares) - members
        if unknown:
            raise SetupError("Unknown team member.")
        self.repo.upsert_split_rule(tenant, "default", {"staff_share_bp": default_bp, "tip_rule": "direct"})
        for mid, bp in staff_shares.items():
            self.repo.upsert_split_rule(tenant, f"staff:{mid}", {"staff_share_bp": bp, "tip_rule": "direct"})

    def ensure_tag(self, tenant: str, member_id: str, rotate: bool = False) -> dict:
        if member_id not in {m["id"] for m in self.repo.team_members(tenant)}:
            raise SetupError("Unknown team member.")
        mine = [t for t in self.repo.list_tags(tenant)
                if t.get("bound_id") == member_id and t.get("status") == "active"]
        if mine and not rotate:
            return self.tag_links(mine[0]["code"])
        for t in mine:                                   # rotation: old tag stops working at once
            self.repo.update_tag(tenant, t["id"], {"status": "disabled"})
        tag = self.repo.create_tag({"tenant_id": tenant, "code": secrets.token_urlsafe(9),
                                    "mode": "appointment", "bound_type": "person", "bound_id": member_id})
        return self.tag_links(tag["code"])

    def start_test(self, tenant: str, member_id: str) -> dict:
        st = self.status(tenant)
        # the test creates the person's tag itself, so a missing tag is not a blocker here
        blockers = [b for b in st["blockers"] if "tag" not in b]
        if blockers:
            raise SetupError(blockers[0])
        link = self.ensure_tag(tenant, member_id)
        tag = next(t for t in self.repo.list_tags(tenant) if t["code"] == link["code"])
        for b in self.repo.live_bills_for_tag(tenant, tag["id"]):      # one open bill per tag
            if b.get("is_test"):
                self.svc.cancel_bill(tenant, b["id"])
            else:
                raise SetupError("This person already has an open bill. Cancel or finish it first.")
        bill = self.svc.create_bill(tenant_id=tenant, tag_id=tag["id"], amount_cents=TEST_AMOUNT_CENTS,
                                    description="Test payment", staff_id=member_id,
                                    created_by="setup", is_test=True)
        if st["mode"] == "off":
            self.set_mode(tenant, "testing")
        return {**link, "bill_id": bill["id"], "amount_cents": TEST_AMOUNT_CENTS}

    def set_mode(self, tenant: str, mode: str) -> None:
        self.repo.upsert_settings(tenant, {"mode": mode})
        self.on_mode_change(tenant)

    def go_live(self, tenant: str) -> None:
        st = self.status(tenant)
        if st["blockers"]:
            raise SetupError(st["blockers"][0])
        if not st["tested"]:
            raise SetupError("Run the R5 test payment first — it confirms PayFast is set up correctly.")
        self.set_mode(tenant, "live")

    def pause(self, tenant: str) -> None:
        self.set_mode(tenant, "off")
