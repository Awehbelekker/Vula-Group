"""
vula/takeoff/order_manager.py

Vula Order Manager

Converts a priced BOQ into structured purchase orders
ready to send to suppliers via:
    - Email (Resend API)
    - WhatsApp (WhatsApp Business API)
    - PDF attachment (generated via WeasyPrint)

Supplier routing:
    Each BOQ trade is mapped to preferred and alternative suppliers.
    Judy maintains her supplier list in the Vula dashboard.
    Discounts and preferred rates stored per supplier.

Order workflow:
    1. BOQ generated from plans
    2. OrderManager groups items by supplier/trade
    3. Judy reviews order list in Vula Takeoff UI
    4. Clicks "Send RFQ" — Vula sends request for quotation
    5. Supplier replies → Vula captures quote
    6. Vula compares quotes → shows cheapest vs Judy's estimate
    7. Judy approves → Vula sends purchase order

Usage:
    boq = await BOQGenerator(project).generate()
    manager = OrderManager(boq, tenant_id="digg_001")
    orders = manager.build_orders()
    await manager.send_rfqs(orders)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx

from vula.takeoff.boq_generator import BOQ

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".vula" / "suppliers.db"

# ─── Supplier routing map ────────────────────────────────────────────────────
# Maps BOQ trade → preferred supplier category
# Judy builds her actual supplier list in the UI; these are defaults

TRADE_SUPPLIER_MAP = {
    "demolition":  ["general_contractor"],
    "concrete":    ["general_contractor", "structural_engineer"],
    "wet_works":   ["waterproofing_specialist", "general_contractor"],
    "drywall":     ["drywall_contractor", "general_contractor"],
    "flooring":    ["flooring_supplier", "general_contractor"],
    "tiling":      ["tiling_contractor", "tile_supplier"],
    "joinery":     ["joinery_supplier", "general_contractor"],
    "glazing":     ["glazing_contractor"],
    "plumbing":    ["plumbing_contractor"],
    "electrical":  ["electrical_contractor"],
    "hvac":        ["hvac_contractor"],          # ExtraAir in Judy's case
    "fire":        ["fire_protection_contractor"],
    "painting":    ["painting_contractor", "general_contractor"],
    "external":    ["general_contractor"],
}


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Supplier:
    id: str
    name: str
    category: str
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    contact_name: Optional[str] = None
    discount_pct: float = 0.0
    preferred: bool = False
    notes: str = ""


@dataclass
class OrderLine:
    boq_item_id: str
    trade: str
    description: str
    qty: float
    unit: str
    estimated_rate: float
    estimated_total: float
    notes: str = ""


@dataclass
class PurchaseOrder:
    po_number: str
    project_name: str
    supplier: Supplier
    lines: List[OrderLine]
    status: str = "draft"        # draft | sent | quoted | approved | ordered
    quote_received: float = 0.0
    sent_at: Optional[str] = None
    notes: str = ""

    @property
    def estimated_total(self) -> float:
        return sum(ln.estimated_total for ln in self.lines)

    @property
    def net_after_discount(self) -> float:
        return self.estimated_total * (1 - self.supplier.discount_pct / 100)


@dataclass
class OrderPackage:
    """Complete set of POs for a project."""
    project_name: str
    tenant_id: str
    orders: List[PurchaseOrder] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def total_estimated(self) -> float:
        return sum(o.estimated_total for o in self.orders)

    @property
    def total_net_discounted(self) -> float:
        return sum(o.net_after_discount for o in self.orders)

    @property
    def total_savings(self) -> float:
        return self.total_estimated - self.total_net_discounted


# ─── Supplier database ────────────────────────────────────────────────────────

class SupplierDatabase:
    """SQLite store for Judy's supplier contacts and discount terms."""

    DEFAULT_SUPPLIERS = [
        Supplier("sup_gc",    "General Contractor (TBC)",  "general_contractor",    discount_pct=0),
        Supplier("sup_dw",    "Drywall Specialist",         "drywall_contractor",    discount_pct=5),
        Supplier("sup_fl",    "Flooring Supplier",          "flooring_supplier",     discount_pct=10),
        Supplier("sup_til",   "Tiling Contractor",          "tiling_contractor",     discount_pct=0),
        Supplier("sup_joi",   "Joinery Supplier",           "joinery_supplier",      discount_pct=7.5),
        Supplier("sup_glz",   "Glazing Contractor",         "glazing_contractor",    discount_pct=0),
        Supplier("sup_plb",   "Plumbing Contractor",        "plumbing_contractor",   discount_pct=0),
        Supplier("sup_ele",   "Electrical Contractor",      "electrical_contractor", discount_pct=0),
        Supplier("sup_hva",   "ExtraAir (HVAC)",            "hvac_contractor",       discount_pct=5, preferred=True, notes="Appointed HVAC contractor — confirmed Phase 1"),
        Supplier("sup_fire",  "Fire Protection Contractor", "fire_protection_contractor", discount_pct=0),
        Supplier("sup_pnt",   "Painting Contractor",        "painting_contractor",   discount_pct=5),
        Supplier("sup_wtp",   "Waterproofing Specialist",   "waterproofing_specialist", discount_pct=0),
        Supplier("sup_str",   "Structural Engineer",        "structural_engineer",   discount_pct=0),
        Supplier("sup_tile_s","Italtile (tiles)",           "tile_supplier",         discount_pct=12, preferred=True, notes="Trade account — 12% discount confirmed"),
        Supplier("sup_fl_s",  "Polyflor SA (flooring)",     "flooring_supplier",     discount_pct=15, preferred=True, notes="Appointed supplier"),
    ]

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                email TEXT,
                whatsapp TEXT,
                contact_name TEXT,
                discount_pct REAL DEFAULT 0,
                preferred INTEGER DEFAULT 0,
                notes TEXT,
                tenant_id TEXT DEFAULT 'default'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchase_orders (
                po_number TEXT PRIMARY KEY,
                project_name TEXT,
                supplier_id TEXT,
                status TEXT DEFAULT 'draft',
                estimated_total REAL,
                quote_received REAL DEFAULT 0,
                sent_at TEXT,
                lines TEXT,
                created_at TEXT
            )
        """)
        conn.commit()

        # Seed defaults if empty
        count = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        if count == 0:
            for s in self.DEFAULT_SUPPLIERS:
                conn.execute(
                    "INSERT OR IGNORE INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,'default')",
                    (s.id, s.name, s.category, s.email, s.whatsapp, s.contact_name,
                     s.discount_pct, int(s.preferred), s.notes)
                )
            conn.commit()
        conn.close()

    def get_by_category(self, category: str, tenant_id: str = "default") -> List[Supplier]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT * FROM suppliers WHERE category=? AND (tenant_id=? OR tenant_id='default') ORDER BY preferred DESC",
            (category, tenant_id)
        ).fetchall()
        conn.close()
        return [self._row_to_supplier(r) for r in rows]

    def get_all(self, tenant_id: str = "default") -> List[Supplier]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT * FROM suppliers WHERE tenant_id=? OR tenant_id='default'", (tenant_id,)).fetchall()
        conn.close()
        return [self._row_to_supplier(r) for r in rows]

    def upsert_supplier(self, supplier: Supplier, tenant_id: str = "default"):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (supplier.id, supplier.name, supplier.category, supplier.email,
              supplier.whatsapp, supplier.contact_name, supplier.discount_pct,
              int(supplier.preferred), supplier.notes, tenant_id))
        conn.commit()
        conn.close()

    def save_po(self, po: PurchaseOrder, project_name: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?)
        """, (po.po_number, project_name, po.supplier.id, po.status,
              po.estimated_total, po.quote_received, po.sent_at,
              json.dumps([{"desc": ln.description, "qty": ln.qty, "unit": ln.unit, "total": ln.estimated_total} for ln in po.lines]),
              datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def _row_to_supplier(self, row) -> Supplier:
        return Supplier(
            id=row[0], name=row[1], category=row[2],
            email=row[3], whatsapp=row[4], contact_name=row[5],
            discount_pct=row[6] or 0.0, preferred=bool(row[7]),
            notes=row[8] or "",
        )


# ─── Order Manager ────────────────────────────────────────────────────────────

class OrderManager:
    """
    Builds and sends purchase orders / RFQs from a BOQ.
    """

    def __init__(self, boq: BOQ, tenant_id: str = "default"):
        self.boq = boq
        self.tenant_id = tenant_id
        self.db = SupplierDatabase()
        self._po_counter = 1

    def build_orders(self) -> OrderPackage:
        """Group BOQ items by supplier and build PO package."""
        package = OrderPackage(
            project_name=self.boq.project_name,
            tenant_id=self.tenant_id,
        )

        # Group items by trade
        by_trade = self.boq.by_trade

        for trade, items in by_trade.items():
            supplier_categories = TRADE_SUPPLIER_MAP.get(trade, ["general_contractor"])

            # Find preferred supplier for this trade
            supplier = None
            for cat in supplier_categories:
                suppliers = self.db.get_by_category(cat, self.tenant_id)
                if suppliers:
                    supplier = suppliers[0]  # Preferred first
                    break

            if not supplier:
                # Create placeholder
                supplier = Supplier(
                    id=f"tbc_{trade}",
                    name=f"{trade.replace('_',' ').title()} Contractor (TBC)",
                    category=trade,
                )

            # Build order lines
            lines = [
                OrderLine(
                    boq_item_id=item.id,
                    trade=item.trade,
                    description=item.description,
                    qty=item.qty,
                    unit=item.unit,
                    estimated_rate=item.rate_used,
                    estimated_total=item.line_total,
                    notes=item.notes,
                )
                for item in items
            ]

            po = PurchaseOrder(
                po_number=f"PO-{datetime.now().strftime('%Y%m')}-{self._po_counter:03d}",
                project_name=self.boq.project_name,
                supplier=supplier,
                lines=lines,
            )
            self._po_counter += 1
            package.orders.append(po)
            self.db.save_po(po, self.boq.project_name)

        logger.info(f"Order package built: {len(package.orders)} POs, "
                    f"estimated total R{package.total_estimated:,.0f}, "
                    f"net after discounts R{package.total_net_discounted:,.0f}")
        return package

    async def send_rfqs(self, package: OrderPackage, dry_run: bool = True) -> List[dict]:
        """Send RFQ emails/WhatsApp to all suppliers in the package."""
        results = []
        for po in package.orders:
            if dry_run:
                logger.info(f"[DRY RUN] Would send RFQ to {po.supplier.name} for {len(po.lines)} items")
                results.append({"po": po.po_number, "supplier": po.supplier.name, "status": "dry_run"})
                continue

            sent = False
            if po.supplier.email:
                sent = await self._send_rfq_email(po)
            elif po.supplier.whatsapp:
                sent = await self._send_rfq_whatsapp(po)

            po.status = "sent" if sent else "draft"
            po.sent_at = datetime.now().isoformat()
            self.db.save_po(po, package.project_name)
            results.append({"po": po.po_number, "supplier": po.supplier.name, "status": po.status})

        return results

    async def _send_rfq_email(self, po: PurchaseOrder) -> bool:
        """Send RFQ via Resend email API."""
        import os
        RESEND_KEY = os.getenv("RESEND_API_KEY", "")
        if not RESEND_KEY:
            logger.warning("RESEND_API_KEY not set")
            return False

        lines_text = "\n".join(
            f"  {i+1}. {ln.description} — {ln.qty} {ln.unit} (est. R{ln.estimated_total:,.0f})"
            for i, ln in enumerate(po.lines)
        )

        body = f"""Dear {po.supplier.contact_name or po.supplier.name},

We are requesting your quotation for the following works for:

Project: {po.project_name}
PO Reference: {po.po_number}
Date: {datetime.now().strftime('%d %B %Y')}

SCOPE OF WORKS:
{lines_text}

Total estimated value: R{po.estimated_total:,.0f} (excl. VAT)

Please provide your best quotation by return email.

Kind regards,
DIGG | Judy Downing
Professional Architectural Technologist — PAT44740093
"""

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_KEY}"},
                    json={
                        "from": "Judy Downing <judy@digg.co.za>",
                        "to": [po.supplier.email],
                        "subject": f"RFQ — {po.project_name} — {po.supplier.category.replace('_',' ').title()}",
                        "text": body,
                    },
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    async def _send_rfq_whatsapp(self, po: PurchaseOrder) -> bool:
        """Send RFQ summary via WhatsApp Business API."""
        import os
        WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
        WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
        if not WA_TOKEN:
            return False

        lines_text = "\n".join(
            f"  • {ln.description} — {ln.qty} {ln.unit}"
            for ln in po.lines[:8]  # Cap at 8 for WhatsApp readability
        )
        if len(po.lines) > 8:
            lines_text += f"\n  ... and {len(po.lines)-8} more items"

        message = (
            f"*RFQ — {po.project_name}*\n"
            f"Ref: {po.po_number}\n\n"
            f"Hi {po.supplier.contact_name or po.supplier.name},\n\n"
            f"Please quote on the following {po.supplier.category.replace('_',' ')} works:\n\n"
            f"{lines_text}\n\n"
            f"Est. value: R{po.estimated_total:,.0f} excl. VAT\n\n"
            f"Reply with your best price. Full RFQ doc to follow by email.\n\n"
            f"Judy Downing | DIGG | PAT44740093"
        )

        number = (po.supplier.whatsapp or "").replace(" ", "").replace("+", "")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages",
                    headers={"Authorization": f"Bearer {WA_TOKEN}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": number,
                        "type": "text",
                        "text": {"body": message},
                    },
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")
            return False

    def order_summary_text(self, package: OrderPackage) -> str:
        """Plain text order summary for Judy to review."""
        lines = [
            f"VULA ORDER PACKAGE — {package.project_name}",
            f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')}",
            "=" * 60,
        ]

        for po in package.orders:
            lines.append(f"\n{po.po_number} — {po.supplier.name}")
            lines.append(f"Trade: {po.supplier.category.replace('_',' ').title()}")
            if po.supplier.discount_pct:
                lines.append(f"Discount: {po.supplier.discount_pct}%")
            lines.append(f"Items: {len(po.lines)}")
            for ln in po.lines:
                lines.append(f"  • {ln.description} — {ln.qty} {ln.unit} — R{ln.estimated_total:,.0f}")
            lines.append(f"  Subtotal: R{po.estimated_total:,.0f}")
            if po.supplier.discount_pct:
                lines.append(f"  Net after {po.supplier.discount_pct}% discount: R{po.net_after_discount:,.0f}")

        lines += [
            "\n" + "=" * 60,
            f"TOTAL ESTIMATED:          R{package.total_estimated:,.0f}",
            f"TOTAL AFTER DISCOUNTS:    R{package.total_net_discounted:,.0f}",
            f"TOTAL SAVINGS:            R{package.total_savings:,.0f}",
        ]
        return "\n".join(lines)
