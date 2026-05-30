"""
vula/commerce/models.py — Pydantic models for Vula Commerce.

Products, cart, and orders live in Supabase.
All prices stored as integer cents (ZAR). Never floats.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class ProductCategory(str, Enum):
    linefish = "linefish"
    shellfish = "shellfish"
    crayfish = "crayfish"
    box_deal = "box_deal"
    smoked = "smoked"
    frozen = "frozen"


class OrderStatus(str, Enum):
    pending_payment = "pending_payment"
    paid = "paid"
    confirmed = "confirmed"
    packing = "packing"
    dispatched = "dispatched"
    delivered = "delivered"
    cancelled = "cancelled"
    refunded = "refunded"


class DeliverySlot(str, Enum):
    morning = "morning"    # 08:00–12:00
    afternoon = "afternoon"  # 12:00–17:00
    express = "express"    # 2hr window, +R50


# ── Products ─────────────────────────────────────────────────────────────────

class Product(BaseModel):
    id: UUID
    tenant_id: str
    name: str
    slug: str
    description: str
    category: ProductCategory
    price_cents: int           # ZAR cents — e.g. 18500 = R185.00
    weight_grams: Optional[int] = None
    serves: Optional[str] = None   # e.g. "2–3 people"
    in_stock: bool = True
    stock_quantity: Optional[int] = None
    image_url: Optional[str] = None
    catch_source: Optional[str] = None   # "Hout Bay" / "Kalk Bay"
    fisherman_name: Optional[str] = None
    is_daily_catch: bool = False
    created_at: datetime
    updated_at: datetime

    @property
    def price_rands(self) -> str:
        return f"R{self.price_cents / 100:.2f}"


class ProductCreate(BaseModel):
    name: str
    slug: str
    description: str
    category: ProductCategory
    price_cents: int
    weight_grams: Optional[int] = None
    serves: Optional[str] = None
    stock_quantity: Optional[int] = None
    image_url: Optional[str] = None
    catch_source: Optional[str] = None
    fisherman_name: Optional[str] = None
    is_daily_catch: bool = False

    @field_validator("price_cents")
    @classmethod
    def price_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("price_cents must be positive")
        return v

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must be lowercase alphanumeric with hyphens only")
        return v


# ── Cart ─────────────────────────────────────────────────────────────────────

class CartItem(BaseModel):
    product_id: UUID
    product_name: str
    quantity: int
    unit_price_cents: int
    total_cents: int


class Cart(BaseModel):
    id: UUID
    tenant_id: str
    session_id: str           # anonymous cart — linked to WhatsApp phone or browser session
    customer_phone: Optional[str] = None
    customer_name: Optional[str] = None
    items: List[CartItem] = []
    subtotal_cents: int = 0
    delivery_cents: int = 8000   # R80 default delivery
    total_cents: int = 0
    delivery_slot: Optional[DeliverySlot] = None
    delivery_address: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AddToCartRequest(BaseModel):
    product_id: UUID
    quantity: int = 1
    customer_phone: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


# ── Orders ───────────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    product_id: UUID
    product_name: str
    quantity: int
    unit_price_cents: int
    total_cents: int
    catch_source: Optional[str] = None


class Order(BaseModel):
    id: UUID
    display_id: str            # e.g. "OTH-00042"
    tenant_id: str
    customer_phone: str
    customer_name: str
    customer_email: Optional[str] = None
    items: List[OrderItem]
    subtotal_cents: int
    delivery_cents: int
    total_cents: int
    status: OrderStatus = OrderStatus.pending_payment
    delivery_slot: Optional[DeliverySlot] = None
    delivery_address: str
    delivery_notes: Optional[str] = None
    yoco_checkout_id: Optional[str] = None
    yoco_payment_id: Optional[str] = None
    channel: str = "web"       # "web" | "whatsapp"
    created_at: datetime
    updated_at: datetime

    @property
    def total_rands(self) -> str:
        return f"R{self.total_cents / 100:.2f}"

    @property
    def items_summary(self) -> str:
        return ", ".join(f"{i.quantity}x {i.product_name}" for i in self.items)


class CreateOrderRequest(BaseModel):
    cart_id: UUID
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    delivery_address: str
    delivery_slot: DeliverySlot = DeliverySlot.morning
    delivery_notes: Optional[str] = None
    channel: str = "web"


# ── Yoco ─────────────────────────────────────────────────────────────────────

class YocoCheckoutResponse(BaseModel):
    order_id: UUID
    display_id: str
    redirect_url: str
    total_cents: int
    total_rands: str
    expires_in_minutes: int = 30
