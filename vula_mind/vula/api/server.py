"""
vula/api/server.py — Vula AI API Server

Run:
    cd vula_mind
    uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload

Endpoints:
    POST /ingest                        — upload + ingest a document (async)
    POST /ingest/sync                   — upload + ingest (sync, for testing)
    GET  /ingest/status/{tenant_id}     — list ingested docs + processing state
    POST /query                         — RAG question against tenant KB
    GET  /status                        — health check (Ollama + Qdrant)
    GET  /documents/{tenant_id}         — list tenant documents

    POST /v1/whatsapp/webhook           — inbound WhatsApp messages (Meta)
    GET  /v1/whatsapp/webhook           — Meta webhook verification

    POST /v1/onboard                    — register new tenant
    POST /v1/payfast/notify             — PayFast ITN payment callback

    POST /v1/chat/{tenant_id}/message   — chat with tenant KB
    GET  /v1/chat/{tenant_id}/history   — conversation history
    DELETE /v1/chat/{tenant_id}/history — clear history

    GET  /v1/training/status            — shared construction KB status
    POST /v1/training/seed              — (re)seed shared KB

    POST /v1/field/contractors                      — register/update contractor
    GET  /v1/field/contractors/{tenant_id}          — list contractors
    POST /v1/field/project/assign                   — assign contractor to project
    GET  /v1/field/project/{project_id}/team        — project team
    GET  /v1/field/project/{project_id}/status      — project status + task board
    POST /v1/field/task                             — create task
    POST /v1/field/task/assign                      — assign task + WhatsApp briefing
    POST /v1/field/task/{task_id}/complete-request  — chase contractor via WhatsApp
    GET  /v1/field/task/{task_id}                   — task detail + evidence
    POST /v1/field/walkthrough/start                — send photo checklist via WhatsApp
    POST /v1/field/walkthrough/{id}/approve         — architect sign-off
    GET  /v1/field/daily-tasks/{tenant_id}          — tasks due today (n8n cron)
    POST /v1/field/daily-tasks/{tenant_id}/dispatch — send morning briefings

    GET  /takeoff/rates                 — construction market rates
    POST /takeoff/upload                — upload drawing for BOQ takeoff
    GET  /takeoff/{job_id}/status       — job status
    GET  /takeoff/{job_id}/boq          — BOQ JSON
    GET  /takeoff/{job_id}/boq/excel    — BOQ Excel download

    POST /scrape/company    — company research
    POST /scrape/prices     — competitor price monitoring
    POST /scrape/digest     — industry news digest
    POST /scrape/tenders    — SA government tender monitoring
    POST /scrape/custom     — custom bulk scraper

    POST /v1/oth/briefing/morning — manually fire OTH morning delivery list
    POST /v1/oth/briefing/evening — manually fire OTH 18:00 sales summary
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security, UploadFile, File, Form, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from vula.ingestion.pipeline import VulaIngestionPipeline
from vula.skills.web_scraper import VulaWebScraper
from vula.takeoff.api import router as takeoff_router
from vula.api.onboarding import router as onboarding_router
from vula.api.whatsapp import router as whatsapp_router
from vula.api.whatsapp import _send_wa_template
from vula.api.training import router as training_router
from vula.api.chat import router as chat_router
from vula.api.field_ops import router as field_ops_router
from vula.api.clickup import router as clickup_router
from vula.api.documents import router as documents_router
from vula.api.projects import router as projects_router
from vula.api.team import router as team_router
from vula.api.payments import router as payments_router
from vula.api.tenants import router as tenants_router
from vula.api.signup import router as signup_router
from vula.api.users import router as users_router
from vula.api.qs import router as qs_router
from vula.api.google import router as google_router
from vula.api.microsoft import router as microsoft_router
from vula.api.dynamics365 import router as dynamics365_router
from vula.api.email_connect import router as email_connect_router
from vula.api.commerce import router as commerce_router
from vula.api.bookings import router as bookings_router
from vula.api.subscriptions import router as subscriptions_router
from vula.api.recurring_bills import router as recurring_bills_router
from vula.api.yoco import router as yoco_router
from vula.api.whatsapp_connect import router as whatsapp_connect_router
from vula.api.yoco_connect import router as yoco_connect_router
from vula.api.draft import router as draft_router
from vula.api.agent import router as agent_router
from vula.api.twilio_whatsapp import router as twilio_router
from vula.api.links import router as links_router
from vula.api.master import router as master_router
from vula.api.menu_page import router as menu_page_router
from vula.api.email_public import router as email_public_router

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vula.api")

# ─── Rate limiter ─────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ─── Lifespan ─────────────────────────────────────────────────────────────────

async def _seed_training_on_boot() -> None:
    """Seed the shared construction KB once at startup if not already populated.

    Gated behind SEED_TRAINING_ON_BOOT (default off) — re-embedding on every
    deploy burns embedding-API credits. Run once manually via POST /v1/training/seed.
    """
    import os
    if os.environ.get("SEED_TRAINING_ON_BOOT", "false").lower() != "true":
        log.info("Training KB boot-seed disabled (set SEED_TRAINING_ON_BOOT=true to enable)")
        return
    import asyncio as _asyncio
    await _asyncio.sleep(30)  # Wait for Qdrant to be ready
    try:
        from vula.training.seeder import training_kb_status, seed_training_kb
        status = await training_kb_status()
        if not status.get("seeded"):
            log.info("Training KB empty — seeding on boot...")
            result = await seed_training_kb()
            log.info("Training KB ready: %d chunks from %d docs", result.total_chunks, result.total_documents)
        else:
            log.info("Training KB already seeded: %d chunks", status.get("chunks", 0))
    except Exception as exc:
        log.warning("Training KB seed on boot failed: %s", exc)


async def _weekly_rates_loop() -> None:
    """Run construction rates scrape weekly. Starts 10 min after boot."""
    import asyncio as _asyncio
    await _asyncio.sleep(600)  # Let the server settle first
    while True:
        try:
            from vula.takeoff.construction_rates_scraper import ConstructionRatesScraper
            scraper = ConstructionRatesScraper()
            result = await scraper.run_full_update()
            log.info("Weekly rates update: %d new, %d updated in %.1fs",
                     result.new, result.updated, result.duration_s)
        except Exception as exc:
            log.warning("Weekly rates update failed: %s", exc)
        await _asyncio.sleep(7 * 24 * 3600)  # sleep one week


async def _call_sheet_loop() -> None:
    """Weekly per-rep call-sheet digest. Own loop (not job_config-driven — see
    vula/commerce/call_sheet.py's module docstring): a rep's schedule/recipient config lives on
    vula_team_members, one row per rep, not on the tenant-scoped commerce_scheduled_job_config
    table. Runs hourly — each rep's own configured day/hour (is_due()) is the real gate; this is
    just a reasonably fresh polling cadence."""
    import asyncio as _asyncio
    await _asyncio.sleep(900)  # settle on boot, after the other loops
    while True:
        try:
            from vula.commerce.call_sheet import run_weekly_call_sheets
            sent = await run_weekly_call_sheets()
            if sent:
                log.info("Weekly call sheet sent to %d rep(s)", sent)
        except Exception as exc:
            log.warning("Call sheet loop error: %s", exc)
        await _asyncio.sleep(3600)  # check hourly; per-rep due-check makes this safe


async def _expense_sheet_loop() -> None:
    """Monthly per-rep expense claim sheet. Same own-loop shape as _call_sheet_loop — schedule/
    recipient config lives per-rep on vula_team_members, not tenant-scoped job_config. A monthly
    cadence doesn't need hourly granularity, so this checks daily instead."""
    import asyncio as _asyncio
    await _asyncio.sleep(1200)  # settle on boot, after the other loops
    while True:
        try:
            from vula.commerce.expense_sheet import run_monthly_expense_sheets
            sent = await run_monthly_expense_sheets()
            if sent:
                log.info("Monthly expense sheet sent to %d rep(s)", sent)
        except Exception as exc:
            log.warning("Expense sheet loop error: %s", exc)
        await _asyncio.sleep(24 * 3600)  # check daily; per-rep due-check makes this safe


async def _daily_trial_expiry_loop() -> None:
    """Warn tenants whose free trial is expiring soon. Runs daily, starts 2h after boot."""
    import asyncio as _asyncio
    from datetime import datetime, timezone
    await _asyncio.sleep(7200)
    while True:
        try:
            from vula.api.onboarding import _supabase, _payfast_url
            from vula.api.email import send_trial_expiry_email
            today = datetime.now(timezone.utc).date()
            rows = await _supabase.select("vula_tenants", {"paid": "false", "status": "active"}) or []
            warned = 0
            for t in rows:
                trial_end_str = (t.get("trial_ends") or "")[:10]
                if not trial_end_str:
                    continue
                try:
                    days_left = (datetime.fromisoformat(trial_end_str).date() - today).days
                    if days_left in (7, 3, 1, 0):
                        payment_url = _payfast_url(
                            t["tenant_id"], t.get("plan", "starter"),
                            t.get("email", ""), t.get("contact_name", ""),
                        )
                        await send_trial_expiry_email(
                            to=t.get("email", ""),
                            first_name=(t.get("contact_name") or "there").split()[0],
                            company_name=t.get("company_name", ""),
                            days_left=days_left,
                            payment_url=payment_url,
                        )
                        warned += 1
                except Exception:
                    pass
            if warned:
                log.info("Trial expiry warnings sent: %d tenants", warned)
        except Exception as exc:
            log.warning("Trial expiry loop failed: %s", exc)
        await _asyncio.sleep(24 * 3600)


# ── Commerce scheduled-job generalization ──────────────────────────────────────
# The 5 jobs below (delivery briefing, unpaid-order chase, low stock, Friday catch reminder,
# sales summary) were originally hardcoded to tenant_id="off-the-hook" with Stacy's phone number
# baked in. Onboarding a second retail/food tenant would have needed a code change for each job —
# this makes them DB-driven instead, so a new commerce tenant just needs team members configured
# (vula_team_members, already DB-driven — see vula.api.yoco._tenant_team) and its own approved
# WhatsApp templates; no code change. WhatsApp template content needs Meta approval PER TENANT'S
# WABA, so template NAMES stay tenant-specific — _COMMERCE_TEMPLATE_PREFIX maps a tenant to its
# approved prefix (e.g. "oth"); add one line here once a new tenant's templates are approved.
_COMMERCE_TEMPLATE_PREFIX: dict[str, str] = {"off-the-hook": "oth"}


def _commerce_template_name(tenant_id: str, suffix: str) -> str:
    prefix = _COMMERCE_TEMPLATE_PREFIX.get(tenant_id) or tenant_id.replace("-", "_")
    return f"{prefix}_{suffix}"


async def _commerce_tenant_ids() -> list[str]:
    """Every tenant with the 'orders' module enabled (i.e. runs a WhatsApp shop) — DB-driven so
    a new retail/food tenant is picked up automatically. Falls back to just off-the-hook if the
    config table isn't reachable, so today's only real commerce tenant keeps working either way."""
    try:
        from vula.commerce import service as _cs
        rows = (_cs._client().table("vula_tenant_config").select("tenant_id,modules")
                .execute().data or [])
        ids = [r["tenant_id"] for r in rows if "orders" in (r.get("modules") or [])]
        if ids:
            return ids
    except Exception as exc:
        log.debug("commerce tenant list fetch failed, falling back: %s", exc)
    return ["off-the-hook"]


def _commerce_notify_phones(tenant_id: str) -> list[str]:
    """WhatsApp numbers for this tenant's owner/operations staff — DB-driven (vula_team_members)
    with the static _TENANT_TEAM map as fallback, same source of truth as order-paid alerts."""
    try:
        from vula.api.yoco import _tenant_team
        return [phone for _, phone, role in _tenant_team(tenant_id) if role in ("owner", "operations")]
    except Exception as exc:
        log.debug("commerce notify phones lookup failed for %s: %s", tenant_id, exc)
        return []


async def _run_commerce_job(tenant_id: str, job_type: str, tpl: str) -> None:
    """Dispatch one claimed job run for one tenant (called only by the poller below)."""
    if job_type == "delivery_briefing":
        await _send_oth_delivery_briefing(only_tenant=tenant_id, template_override=tpl)
    elif job_type == "unpaid_order_chase":
        await _chase_unpaid_orders(only_tenant=tenant_id, template_override=tpl)
    elif job_type == "low_stock_alert":
        await _send_low_stock_alert(only_tenant=tenant_id, template_override=tpl)
    elif job_type == "friday_catch_reminder":
        await _send_friday_catch_reminder(only_tenant=tenant_id, template_override=tpl)
    elif job_type == "sales_summary":
        await _send_oth_sales_summary(only_tenant=tenant_id, template_override=tpl)
    elif job_type == "pending_project_nudge":
        await _send_pending_project_nudge(only_tenant=tenant_id, template_override=tpl)


async def _commerce_jobs_scheduler_loop() -> None:
    """Config-driven scheduler for the system WhatsApp jobs (replaces the original five fixed
    wall-clock loops, migration 069). Polls every 5 minutes; per tenant + job it checks the
    ⏰ Scheduling tab's config (defaults where unset), and claim_run() atomically decides
    "due now AND not already fired this window" — so times/templates are admin-editable and
    a leader handover can't double-send. Only the scheduler-lock leader runs this at all."""
    import asyncio as _asyncio
    from vula.commerce import job_config

    await _asyncio.sleep(90)  # let the app settle on boot
    while True:
        try:
            for tenant_id in await _commerce_tenant_ids():
                cfgs = job_config.get_configs(tenant_id)
                for job_type, cfg in cfgs.items():
                    if not cfg.get("enabled", True):
                        continue
                    if not job_config.claim_run(tenant_id, job_type, cfg):
                        continue
                    tpl = cfg.get("template_name") or _commerce_template_name(
                        tenant_id, cfg["default_template_suffix"])
                    try:
                        await _run_commerce_job(tenant_id, job_type, tpl)
                    except Exception as exc:
                        log.warning("commerce job %s/%s failed: %s", tenant_id, job_type, exc)
        except Exception as exc:
            log.warning("commerce jobs scheduler tick failed: %s", exc)
        await _asyncio.sleep(300)


async def _send_oth_delivery_briefing(only_tenant: str | None = None,
                                      template_override: str | None = None) -> None:
    """Build and WhatsApp the day's delivery list to each commerce tenant's team.
    only_tenant/template_override are set by the config-driven scheduler (migration 069);
    the bare call (manual trigger endpoints) does every tenant with defaults."""
    if not settings.whatsapp_token:
        return

    from datetime import date
    from vula.commerce import service as _commerce

    today = date.today().isoformat()

    for tenant_id in ([only_tenant] if only_tenant else await _commerce_tenant_ids()):
        phones = _commerce_notify_phones(tenant_id)
        if not phones:
            continue
        try:
            orders = await _commerce.get_delivery_list(tenant_id, date_str=today)
        except Exception as exc:
            log.warning("%s delivery list fetch failed: %s", tenant_id, exc)
            continue

        _PAID = {"paid", "confirmed", "packing", "dispatched", "delivered"}
        paid_orders   = [o for o in orders if o["status"] in _PAID]
        unpaid_orders = [o for o in orders if o["status"] == "pending_payment"]

        # Full itemized breakdown is filed in the log for reference; the WhatsApp send itself
        # must be a template (proactive/scheduled — free text to someone who hasn't messaged
        # recently fails with 'Re-engagement message', confirmed 2026-07-15), so it can only
        # carry a short summary — staff open Vula for the itemized list.
        log.info("%s delivery briefing %s: %d orders (%d paid, %d unpaid)",
                 tenant_id, today, len(orders), len(paid_orders), len(unpaid_orders))

        tpl = template_override or _commerce_template_name(tenant_id, "delivery_briefing")
        for phone in phones:
            ok = await _send_wa_template(tenant_id, phone, tpl,
                                         str(len(orders)), today, str(len(paid_orders)), str(len(unpaid_orders)))
            log.info("%s delivery briefing to %s: %s", tenant_id, phone, "sent" if ok else "FAILED")


async def _chase_unpaid_orders(only_tenant: str | None = None,
                               template_override: str | None = None) -> None:
    """Find orders pending_payment for 2-4h, across every commerce tenant, and send a WhatsApp
    nudge (once per order) to the CUSTOMER — this one was already tenant-scoped per-order via
    customer_phone, it just needed the tenant filter itself to stop being hardcoded."""
    if not settings.whatsapp_token:
        return

    from datetime import datetime, timezone, timedelta

    tenant_ids = [only_tenant] if only_tenant else await _commerce_tenant_ids()

    try:
        from supabase import create_client as _sb_client
        client = _sb_client(
            settings.supabase_url,
            settings.supabase_service_role_key or settings.supabase_service_key,
        )
        now = datetime.now(timezone.utc)
        two_hours_ago = (now - timedelta(hours=2)).isoformat()
        four_hours_ago = (now - timedelta(hours=4)).isoformat()

        result = (
            client.table("commerce_orders")
            .select("id,tenant_id,display_id,customer_name,customer_phone,total_cents")
            .in_("tenant_id", tenant_ids)
            .eq("status", "pending_payment")
            .gte("created_at", four_hours_ago)
            .lte("created_at", two_hours_ago)
            .is_("followup_sent_at", "null")
            .execute()
        )
        orders = result.data or []
    except Exception as exc:
        log.warning("Unpaid order fetch failed: %s", exc)
        return

    if not orders:
        return

    for o in orders:
        tenant_id = o["tenant_id"]
        phone = (o.get("customer_phone") or "").strip().lstrip("+").replace(" ", "")
        if phone.startswith("0"):
            phone = "27" + phone[1:]
        if not phone or len(phone) < 9:
            continue
        # Claim the row atomically BEFORE sending — uvicorn runs this loop once per worker
        # (WEB_CONCURRENCY=2, see start.py), so two workers can both fetch the same
        # "un-followed-up" order in the same 30-min tick. Whichever worker's conditional
        # UPDATE actually matches a row (followup_sent_at still NULL) wins the claim; the
        # other gets zero rows back and skips the send — confirmed duplicate-send bug fixed.
        try:
            claim = (
                client.table("commerce_orders")
                .update({"followup_sent_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", o["id"])
                .is_("followup_sent_at", "null")
                .execute()
            )
        except Exception as exc:
            log.warning("Unpaid follow-up claim failed for %s: %s", o["display_id"], exc)
            continue
        if not claim.data:
            continue  # another worker already claimed + is sending this one
        name = (o.get("customer_name") or "there").split()[0]
        amount = f"R{o['total_cents'] / 100:.2f}"
        tpl = template_override or _commerce_template_name(tenant_id, "unpaid_order_chase")
        try:
            await _send_wa_template(tenant_id, phone, tpl, name, o["display_id"], amount)
            log.info("Unpaid follow-up sent: %s → %s", o["display_id"], phone)
        except Exception as exc:
            log.warning("Unpaid follow-up failed for %s: %s", o["display_id"], exc)


async def _send_low_stock_alert(only_tenant: str | None = None,
                                template_override: str | None = None) -> None:
    """WhatsApp each commerce tenant's team if any product has stock_quantity below 5."""
    if not settings.whatsapp_token:
        return
    from vula.commerce import service as _commerce

    for tenant_id in ([only_tenant] if only_tenant else await _commerce_tenant_ids()):
        phones = _commerce_notify_phones(tenant_id)
        if not phones:
            continue
        try:
            low = await _commerce.get_low_stock_products(tenant_id, threshold=5)
        except Exception as exc:
            log.warning("%s low stock query failed: %s", tenant_id, exc)
            continue
        if not low:
            continue

        # Itemized list filed in the log (unbounded length, can't fit a template); WhatsApp
        # gets a count-only summary via the approved template — staff open Vula for which items.
        log.info("%s low stock alert: %s", tenant_id,
                 ", ".join(f"{p['name']} ({p.get('stock_quantity')})" for p in low))
        tpl = template_override or _commerce_template_name(tenant_id, "low_stock_alert")
        for phone in phones:
            ok = await _send_wa_template(tenant_id, phone, tpl, str(len(low)))
            log.info("%s low stock alert to %s: %s (%d items)",
                     tenant_id, phone, "sent" if ok else "FAILED", len(low))

        # Auto-draft (never auto-send) supplier POs for anything the owner has explicitly
        # configured for auto-reorder (reorder_threshold/reorder_qty/default_supplier_id all
        # set) — everything else is still just the alert above, unchanged.
        try:
            from vula.commerce import purchase_orders as _po
            drafted = await _po.draft_reorder_pos(tenant_id)
            if drafted:
                log.info("%s auto-drafted %d supplier PO(s) for review", tenant_id, len(drafted))
        except Exception as exc:
            log.warning("%s auto-draft POs failed: %s", tenant_id, exc)


async def _send_pending_project_nudge(only_tenant: str | None = None,
                                      template_override: str | None = None) -> None:
    """WhatsApp each commerce tenant's team a count-only reminder when filed documents are
    sitting with status='pending_project' (never assigned to a project) — confirmed real,
    unaddressed backlog (2026-08-28 platform review: 472 documents across tenants, no existing
    reminder/nudge mechanism at all)."""
    if not settings.whatsapp_token:
        return
    from vula.commerce import service as _cs

    for tenant_id in ([only_tenant] if only_tenant else await _commerce_tenant_ids()):
        phones = _commerce_notify_phones(tenant_id)
        if not phones:
            continue
        try:
            count = (_cs._client().table("vula_filed_documents").select("id", count="exact")
                     .eq("tenant_id", tenant_id).eq("status", "pending_project")
                     .execute().count or 0)
        except Exception as exc:
            log.warning("%s pending-project count failed: %s", tenant_id, exc)
            continue
        if not count:
            continue

        log.info("%s pending-project nudge: %d unassigned document(s)", tenant_id, count)
        tpl = template_override or _commerce_template_name(tenant_id, "pending_project_nudge")
        for phone in phones:
            ok = await _send_wa_template(tenant_id, phone, tpl, str(count))
            log.info("%s pending-project nudge to %s: %s (%d docs)",
                     tenant_id, phone, "sent" if ok else "FAILED", count)


async def _send_friday_catch_reminder(only_tenant: str | None = None,
                                      template_override: str | None = None) -> None:
    """Remind each commerce tenant's team to update their weekly specials."""
    if not settings.whatsapp_token:
        return

    for tenant_id in ([only_tenant] if only_tenant else await _commerce_tenant_ids()):
        phones = _commerce_notify_phones(tenant_id)
        if not phones:
            continue
        tpl = template_override or _commerce_template_name(tenant_id, "friday_catch_reminder")
        for phone in phones:
            ok = await _send_wa_template(tenant_id, phone, tpl)
            log.info("%s Friday catch reminder to %s: %s", tenant_id, phone, "sent" if ok else "FAILED")


async def _send_oth_sales_summary(only_tenant: str | None = None,
                                  template_override: str | None = None) -> None:
    """Build and WhatsApp each commerce tenant's team their day's sales summary at 18:00."""
    if not settings.whatsapp_token:
        return

    from datetime import date
    from vula.commerce import service as _commerce

    today = date.today().isoformat()

    for tenant_id in ([only_tenant] if only_tenant else await _commerce_tenant_ids()):
        phones = _commerce_notify_phones(tenant_id)
        if not phones:
            continue
        try:
            orders = await _commerce.get_delivery_list(tenant_id, date_str=today)
        except Exception as exc:
            log.warning("%s sales summary fetch failed: %s", tenant_id, exc)
            continue

        _PAID = {"paid", "confirmed", "packing", "dispatched", "delivered"}
        paid_orders = [o for o in orders if o["status"] in _PAID]
        total_rev    = sum(o["total_cents"] for o in paid_orders)

        # Top sellers + unpaid detail filed in the log (unbounded, can't fit a template); the
        # approved template carries the summary numbers only — staff open Vula for the breakdown.
        product_counts: dict[str, int] = {}
        for o in orders:
            for it in (o.get("commerce_order_items") or []):
                name = it.get("product_name", "Unknown")
                product_counts[name] = product_counts.get(name, 0) + it.get("quantity", 1)
        top = sorted(product_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        log.info("%s sales summary %s: %d orders, %d paid, R%.2f — top: %s",
                 tenant_id, today, len(orders), len(paid_orders), total_rev / 100, top)

        tpl = template_override or _commerce_template_name(tenant_id, "sales_summary")
        for phone in phones:
            ok = await _send_wa_template(tenant_id, phone, tpl,
                                         today, str(len(orders)), str(len(paid_orders)), f"{total_rev / 100:.2f}")
            log.info("%s sales summary to %s: %s", tenant_id, phone, "sent" if ok else "FAILED")


async def _scheduled_campaigns_loop() -> None:
    """Fire due scheduled / recurring broadcast campaigns every 60s."""
    import asyncio as _asyncio
    await _asyncio.sleep(30)  # let the app settle on boot
    while True:
        try:
            from vula.api.commerce import process_due_campaigns
            fired = await process_due_campaigns()
            if fired:
                log.info("Campaign scheduler fired %d campaign(s)", fired)
        except Exception as exc:
            log.warning("Campaign scheduler loop error: %s", exc)
        await _asyncio.sleep(60)


async def _automations_loop() -> None:
    """Evaluate every enabled automation rule every 5 minutes (P3 automations builder)."""
    import asyncio as _asyncio
    await _asyncio.sleep(45)  # let the app settle on boot
    while True:
        try:
            from vula.commerce.automations import process_due_automations
            fired = await process_due_automations()
            if fired:
                log.info("Automations loop fired %d action(s)", fired)
        except Exception as exc:
            log.warning("Automations loop error: %s", exc)
        try:
            from vula.api.whatsapp import check_and_nudge_quiet_team_members
            nudged = await check_and_nudge_quiet_team_members()
            if nudged:
                log.info("Keep-window-open nudge sent to %d team member(s)", nudged)
        except Exception as exc:
            log.warning("Window-nudge check error: %s", exc)
        try:
            from vula.api.whatsapp import check_and_nudge_due_reminders
            reminded = await check_and_nudge_due_reminders()
            if reminded:
                log.info("Due-reminder nudge sent for %d reminder(s)", reminded)
        except Exception as exc:
            log.warning("Reminder nudge check error: %s", exc)
        await _asyncio.sleep(300)


async def _daily_commerce_jobs_loop() -> None:
    """Once a day: mark overdue invoices (+remind customers) and send low-stock alerts, per tenant."""
    import asyncio as _asyncio
    await _asyncio.sleep(150)  # settle on boot
    while True:
        try:
            from vula.api.commerce import _process_overdue_invoices, _process_stock_alerts
            from vula.api import tenants as _t
            rows = _t._client().table("vula_tenant_config").select("tenant_id").execute().data or []
            for r in rows:
                tid = r.get("tenant_id")
                if not tid:
                    continue
                try:
                    await _process_overdue_invoices(tid)
                    await _process_stock_alerts(tid)   # throttled to once/day internally
                except Exception as exc:
                    log.debug("daily commerce job failed for %s: %s", tid, exc)
        except Exception as exc:
            log.warning("Daily commerce jobs loop error: %s", exc)
        await _asyncio.sleep(86400)  # daily


async def _voice_retry_scheduler_loop() -> None:
    """Retry voice notes whose transcription failed, locally (2026-09-01).

    Real telemetry: 26% of every voice note ever received (6 of 23) was lost to the local
    Whisper tunnel being unreachable — never to an actual transcription failure. Rather than
    ship customer audio to a third-party cloud transcriber, the note is parked and retried
    here once the box is back, so an outage costs a delay instead of an order.
    """
    import asyncio as _asyncio
    from core.transcribe import transcribe_audio
    from vula import voice_retry
    from vula.api.whatsapp import _handle_commerce_message, _handle_message, _send_reply
    from core.lang import is_voice_supported

    await _asyncio.sleep(120)  # settle on boot
    while True:
        try:
            for row in voice_retry.pending():
                rid, tenant_id = row["id"], row.get("tenant_id") or ""
                phone = row.get("customer_phone") or ""
                attempts = int(row.get("attempts") or 0)

                if voice_retry.too_old(row) or attempts >= voice_retry.MAX_ATTEMPTS:
                    voice_retry.give_up(rid)
                    await _send_reply(phone, (
                        "Sorry — I still couldn't listen to that voice note. 🎙️ Could you type "
                        "it out for me? I'll help right away."
                    ), tenant_id)
                    log.warning("gave up on queued voice note %s after %d attempts", rid, attempts)
                    continue

                audio = voice_retry.audio_of(row)
                if not audio:
                    voice_retry.give_up(rid)
                    continue

                text, lang = await transcribe_audio(
                    audio, mime_type=row.get("mime_type") or "audio/ogg",
                    filename=f"voice-retry-{rid}.ogg", tenant_id=tenant_id,
                )
                if not text:
                    voice_retry.mark_failed_attempt(rid, attempts, "transcription still failing")
                    continue

                # Transcribed at last — drop the audio first so a routing error can never cause
                # the same note to be replayed to the customer twice.
                voice_retry.mark_done(rid)
                if lang and not is_voice_supported(lang):
                    await _send_reply(phone, (
                        "👋 I can only understand *voice notes* in English and Afrikaans right "
                        "now. Please type your message — I'll reply in your language."
                    ), tenant_id)
                    continue
                log.info("recovered queued voice note %s (lang=%s): %r", rid, lang, text[:80])
                if (row.get("route_mode") or "commerce") == "commerce":
                    await _handle_commerce_message(phone, text, None, tenant_id, detected_lang=lang)
                else:
                    await _handle_message(phone, text, None, route_tenant_id=tenant_id)
        except Exception as exc:
            log.warning("voice retry scheduler tick failed: %s", exc)
        await _asyncio.sleep(120)  # the box usually comes back quickly; retry often


async def _stale_escalation_scheduler_loop() -> None:
    """Tenant-agnostic 'conversation gone stale' nudge (2026-07-28) — generalizes proactive
    re-engagement past commerce-only tenants. _commerce_jobs_scheduler_loop only ever iterates
    tenants with the "orders" module (_commerce_tenant_ids), so a DIGG-shaped services/
    construction tenant had no re-engagement mechanism at all. An open customer escalation
    that sat unanswered too long used to just silently expire at 48h
    (escalation.open_escalation_for_helper) with nobody told a question was dropped — this
    reminds the assigned helper before that happens, for every tenant."""
    import asyncio as _asyncio
    from datetime import datetime, timezone
    from vula.commerce import job_config
    from vula import escalation as esc
    from vula.api.whatsapp import _send_reply
    from vula.api import tenants as _t

    await _asyncio.sleep(180)  # settle on boot
    while True:
        try:
            rows = _t._client().table("vula_tenant_config").select("tenant_id").execute().data or []
            for r in rows:
                tenant_id = r.get("tenant_id")
                if not tenant_id:
                    continue
                cfg = job_config.get_configs(tenant_id).get("stale_escalation_nudge")
                if not cfg or not cfg.get("enabled", True):
                    continue
                if not job_config.claim_run(tenant_id, "stale_escalation_nudge", cfg):
                    continue
                try:
                    for row in esc.find_stale_open_escalations(tenant_id):
                        age_h = int((datetime.now(timezone.utc)
                                    - datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")))
                                   .total_seconds() // 3600)
                        msg = (f"⏰ Reminder — this customer question is still waiting ({age_h}h):\n\n"
                              f"\"{(row.get('question') or '').strip()}\"\n\nReply to this message "
                              f"with the answer — I'll send it to them and remember it.")
                        if await _send_reply(row["helper_phone"], msg, tenant_id=tenant_id):
                            esc.mark_stale_notified(row["id"])

                    # Close the loop with the CUSTOMER on anything the helper never answered.
                    # Silence is the worst reply to a real buying question — see
                    # find_abandoned_escalations for the incident this comes from.
                    for row in esc.find_abandoned_escalations(tenant_id):
                        q = (row.get("question") or "").strip()
                        apology = (
                            "Hi 👋 You asked me earlier:\n\n"
                            f"\"{q}\"\n\n"
                            "I'm sorry — I checked with the team and haven't been able to get "
                            "you a firm answer. I didn't want to leave you waiting with no "
                            "reply. Please send me a message and I'll get someone onto it "
                            "right away."
                        )
                        if await _send_reply(row["customer_phone"], apology, tenant_id=tenant_id):
                            esc.mark_customer_notified(row["id"])
                            log.info("apologised to customer for unanswered escalation %s", row["id"])
                except Exception as exc:
                    log.warning("stale escalation nudge failed for %s: %s", tenant_id, exc)
        except Exception as exc:
            log.warning("stale escalation scheduler tick failed: %s", exc)
        await _asyncio.sleep(600)  # poll every 10 minutes; per-tenant interval is job_config-driven


async def _subscriptions_loop() -> None:
    """Create due recurring orders every hour (acts only when a subscription's next_run arrives)."""
    import asyncio as _asyncio
    await _asyncio.sleep(90)  # settle on boot
    while True:
        try:
            from vula.commerce.subscriptions import process_due
            n = await process_due()
            if n:
                log.info("Subscriptions scheduler created %d recurring order(s)", n)
        except Exception as exc:
            log.warning("Subscriptions scheduler loop error: %s", exc)
        await _asyncio.sleep(3600)


async def _recurring_bills_loop() -> None:
    """Spawn pending expenses for due recurring bills (rent, utilities, ...) once a day."""
    import asyncio as _asyncio
    await _asyncio.sleep(100)  # settle on boot
    while True:
        try:
            from vula.commerce.recurring_bills import process_due
            n = await process_due()
            if n:
                log.info("Recurring bills scheduler spawned %d pending expense(s)", n)
        except Exception as exc:
            log.warning("Recurring bills scheduler loop error: %s", exc)
        await _asyncio.sleep(24 * 3600)


async def _email_sync_loop() -> None:
    """Auto-sync connected mailboxes every 15 min: build contacts + file attachments."""
    import asyncio as _asyncio
    await _asyncio.sleep(45)  # settle on boot
    while True:
        try:
            from vula.email_imap.sync import process_all_email_sync
            n = await process_all_email_sync()
            if n:
                log.info("Email sync processed %d new message(s)", n)
        except Exception as exc:
            log.warning("Email sync loop error: %s", exc)
        await _asyncio.sleep(60 * 60)   # hourly — email isn't time-critical; saves AI cost


async def _recurring_invoices_loop() -> None:
    """Generate due recurring invoices once a day."""
    import asyncio as _asyncio
    await _asyncio.sleep(180)
    while True:
        try:
            from vula.commerce import service as commerce_service
            n = await commerce_service.process_due_recurring()
            if n:
                log.info("Recurring invoices generated: %d", n)
        except Exception as exc:
            log.debug("recurring invoices loop error: %s", exc)
        await _asyncio.sleep(24 * 3600)


async def _infra_snapshot_loop() -> None:
    """Daily per-tenant snapshot of vectors + storage for COGS visibility."""
    import asyncio as _asyncio
    await _asyncio.sleep(120)
    while True:
        try:
            from vula.integrations.metering import snapshot_infra
            await snapshot_infra()
        except Exception as exc:
            log.debug("infra snapshot loop error: %s", exc)
        await _asyncio.sleep(24 * 3600)


_SCHEDULER_LOCK_NAME = "main"
_SCHEDULER_LEASE_SECONDS = 180
_SCHEDULER_RENEW_SECONDS = 60


def _scheduler_holder_id() -> str:
    import os, socket
    return f"{socket.gethostname()}-{os.getpid()}"


async def _try_acquire_or_renew_scheduler_lock() -> bool:
    """Compare-and-swap claim on the single scheduler lock row. Returns True if THIS process
    holds the lock after the call (either newly acquired, renewed, or taken over from a dead
    holder whose lease expired) — see migration 067 for why this exists."""
    from datetime import datetime, timezone, timedelta
    from supabase import create_client as _sb_client

    holder = _scheduler_holder_id()
    client = _sb_client(settings.supabase_url,
                        settings.supabase_service_role_key or settings.supabase_service_key)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(seconds=_SCHEDULER_LEASE_SECONDS)).isoformat()

    try:
        existing = (client.table("vula_scheduler_lock").select("holder,expires_at")
                    .eq("lock_name", _SCHEDULER_LOCK_NAME).limit(1).execute().data or [])
    except Exception as exc:
        log.warning("scheduler lock check failed: %s", exc)
        return False

    if not existing:
        try:
            client.table("vula_scheduler_lock").insert({
                "lock_name": _SCHEDULER_LOCK_NAME, "holder": holder, "expires_at": expires,
            }).execute()
            return True
        except Exception:
            return False  # another worker's row won the race

    row = existing[0]
    is_mine = row.get("holder") == holder
    expired = (row.get("expires_at") or "") < now.isoformat()
    if not (is_mine or expired):
        return False

    # Conditional update: only succeeds if `holder` is still what we last read — if another
    # worker already took over an expired lease, this update matches zero rows and we lose.
    try:
        res = (client.table("vula_scheduler_lock")
               .update({"holder": holder, "expires_at": expires})
               .eq("lock_name", _SCHEDULER_LOCK_NAME).eq("holder", row["holder"]).execute())
        return bool(res.data)
    except Exception as exc:
        log.warning("scheduler lock renew failed: %s", exc)
        return False


def _start_scheduled_job_tasks() -> None:
    """All periodic background jobs — started exactly once, only by the worker holding the
    scheduler lock (see _scheduler_leadership_loop). Never call this directly."""
    import asyncio as _asyncio
    _asyncio.create_task(_seed_training_on_boot())
    _asyncio.create_task(_infra_snapshot_loop())
    _asyncio.create_task(_recurring_invoices_loop())
    _asyncio.create_task(_scheduled_campaigns_loop())
    _asyncio.create_task(_automations_loop())
    _asyncio.create_task(_subscriptions_loop())
    _asyncio.create_task(_recurring_bills_loop())
    _asyncio.create_task(_daily_commerce_jobs_loop())
    _asyncio.create_task(_email_sync_loop())
    _asyncio.create_task(_weekly_rates_loop())
    _asyncio.create_task(_call_sheet_loop())
    _asyncio.create_task(_expense_sheet_loop())
    _asyncio.create_task(_daily_trial_expiry_loop())
    # One config-driven poller replaces the five fixed wall-clock loops (delivery briefing,
    # sales summary, unpaid chase, low stock, Friday reminder) — see migration 069 + the
    # ⏰ Scheduling tab. Needs the commerce_scheduled_job_config table to fire anything.
    _asyncio.create_task(_commerce_jobs_scheduler_loop())
    # Runs for EVERY tenant (not just ones with the "orders" module) — see its own docstring.
    _asyncio.create_task(_stale_escalation_scheduler_loop())
    # Retries voice notes parked when transcription was unreachable (migration 148).
    _asyncio.create_task(_voice_retry_scheduler_loop())


async def _scheduler_leadership_loop() -> None:
    """Runs in EVERY worker (start.py runs uvicorn with WEB_CONCURRENCY=2). Without this, every
    periodic job above started once per worker with no coordination — confirmed 2026-07-16 as the
    cause of duplicate delivery briefings, sales summaries, and unpaid-order chase messages, all
    sent twice. Only the worker holding the DB lease starts the job tasks, and only once; it
    renews the lease well before it expires, so a dead leader is replaced within ~3 minutes
    without needing a manual restart."""
    import asyncio as _asyncio
    is_leader = False
    while True:
        try:
            got_lock = await _try_acquire_or_renew_scheduler_lock()
        except Exception as exc:
            log.warning("scheduler leadership check failed: %s", exc)
            got_lock = False
        if got_lock and not is_leader:
            is_leader = True
            log.info("Scheduler leadership acquired (%s) — starting periodic jobs", _scheduler_holder_id())
            _start_scheduled_job_tasks()
        elif not got_lock and is_leader:
            log.warning("Scheduler leadership lost unexpectedly (%s)", _scheduler_holder_id())
            is_leader = False
        await _asyncio.sleep(_SCHEDULER_RENEW_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio as _asyncio
    settings.warn_missing()
    try:
        from vula.integrations.metering import install_metering
        install_metering()
    except Exception as exc:
        log.warning("metering install skipped: %s", exc)
    _asyncio.create_task(_scheduler_leadership_loop())
    yield


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vula AI API",
    description="Unlock your business intelligence — by Vula Group",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    # Allow every Vula tenant origin (current + future) without env changes:
    #   *.vula-ai.com, *.offthehook.co.za, *.digg-ct.co.za, *.vercel.app, localhost
    allow_origin_regex=(
        r"https://([a-z0-9-]+\.)*(vula-ai\.com|offthehook\.co\.za|digg-ct\.co\.za|vercel\.app)"
        r"|http://localhost:[0-9]+"
    ),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)


import time as _time
_request_stats: dict = {"total": 0, "errors": 0, "latencies_ms": []}


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    """Structured request logging with correlation ID, timing, and error tracking."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    started = _time.monotonic()

    try:
        response = await call_next(request)
    except Exception as exc:
        _request_stats["errors"] += 1
        log.error("[%s] UNHANDLED %s %s — %s", request_id, request.method, request.url.path, exc)
        raise

    latency_ms = int((_time.monotonic() - started) * 1000)
    _request_stats["total"] += 1
    _request_stats["latencies_ms"].append(latency_ms)
    # Keep only last 1000 for rolling average
    if len(_request_stats["latencies_ms"]) > 1000:
        _request_stats["latencies_ms"] = _request_stats["latencies_ms"][-1000:]
    if response.status_code >= 500:
        _request_stats["errors"] += 1

    # Structured log line — parseable by log aggregators (Railway, Datadog, etc.)
    log.info(
        "req=%s method=%s path=%s status=%d latency_ms=%d",
        request_id, request.method, request.url.path,
        response.status_code, latency_ms,
    )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Latency-Ms"] = str(latency_ms)
    return response


# Tenant-scoped authorization guard (2026-07-17) — closes the "any caller who knows a tenant
# slug can hit its admin endpoints" gap. Enforced only when ENFORCE_TENANT_AUTH=true so the
# dashboard's token-attach wrapper can ship first (dark rollout, env-flip to arm — rollback is
# flipping it back, no redeploy).
_TENANT_GUARD_RES = [
    re.compile(r"^/v1/commerce/([^/]+)/admin(?:/|$)"),
    re.compile(r"^/v1/team/([^/]+)(?:/|$)"),
    re.compile(r"^/v1/users/([^/]+)(?:/|$)"),
]

# 2026-08-14: per-tenant LLM cost metering (vula/integrations/metering.py) attributes every
# litellm call's token usage via a request-scoped contextvar — but until now that contextvar was
# only ever set on WhatsApp's entry points (vula/api/whatsapp.py). Every dashboard-triggered AI
# call (page-copy drafting, invoice-style cloning, marketing generation, voice-profile analysis,
# etc — everything under /v1/commerce/{tenant}/...) was landing in vula_ai_usage's
# "_unattributed" bucket instead of the responsible tenant. Deliberately a SEPARATE middleware
# from tenant_admin_guard above, not folded into it — metering must never depend on
# ENFORCE_TENANT_AUTH, which only gates auth, not cost attribution.
_TENANT_ID_RE = re.compile(r"^/v1/commerce/([^/]+)/")


@app.middleware("http")
async def tenant_metering_context(request, call_next):
    m = _TENANT_ID_RE.match(request.url.path)
    if m:
        try:
            from vula.integrations.metering import set_request_tenant
            set_request_tenant(m.group(1))
        except Exception:
            pass
    return await call_next(request)


@app.middleware("http")
async def tenant_admin_guard(request, call_next):
    if settings.enforce_tenant_auth and request.method != "OPTIONS":
        path = request.url.path
        for rx in _TENANT_GUARD_RES:
            m = rx.match(path)
            if not m:
                continue
            from fastapi.responses import JSONResponse
            auth_header = request.headers.get("authorization", "")
            if not auth_header:
                return JSONResponse({"detail": "Sign in required."}, status_code=401)
            from vula.api.tenant_auth import is_tenant_member
            if not await is_tenant_member(auth_header, m.group(1)):
                return JSONResponse(
                    {"detail": "You don't have access to this workspace."}, status_code=403)
            break
    return await call_next(request)

app.include_router(links_router)  # no prefix — public /l/{code} redirect for broadcast click tracking
app.include_router(email_public_router)  # no prefix — public /email/unsubscribe for campaigns
app.include_router(menu_page_router)  # no prefix — public /menu/{tenant_id} photo menu
app.include_router(master_router, prefix="/v1/master")  # ALL endpoints require verified master JWT
app.include_router(takeoff_router, prefix="/takeoff")
app.include_router(onboarding_router, prefix="/v1")
app.include_router(signup_router, prefix="/v1")
app.include_router(whatsapp_router, prefix="/v1/whatsapp")
app.include_router(training_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(field_ops_router, prefix="/v1/field")
app.include_router(clickup_router, prefix="/v1/clickup")
app.include_router(documents_router, prefix="/v1/documents")
app.include_router(projects_router, prefix="/v1/projects")
app.include_router(team_router, prefix="/v1/team")
app.include_router(payments_router, prefix="/v1/payments")
app.include_router(tenants_router, prefix="/v1/tenants")
app.include_router(users_router, prefix="/v1/users")
app.include_router(qs_router, prefix="/v1/qs")
app.include_router(google_router, prefix="/v1/google")
app.include_router(microsoft_router, prefix="/v1/microsoft")
app.include_router(dynamics365_router, prefix="/v1/dynamics365")
app.include_router(email_connect_router, prefix="/v1/email")
app.include_router(commerce_router, prefix="/v1/commerce")
app.include_router(bookings_router, prefix="/v1/bookings")
app.include_router(subscriptions_router, prefix="/v1/subscriptions")
app.include_router(recurring_bills_router, prefix="/v1/recurring-bills")
app.include_router(yoco_router, prefix="/v1/yoco")
app.include_router(yoco_connect_router, prefix="/v1/yoco")
app.include_router(whatsapp_connect_router, prefix="/v1/whatsapp")
app.include_router(draft_router, prefix="/v1")
app.include_router(agent_router, prefix="/v1")
app.include_router(twilio_router, prefix="/v1/twilio")

UPLOAD_DIR = settings.upload_dir

# ─── Auth ────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_auth(api_key: str | None = Security(_api_key_header),
                       request: Request = None) -> None:
    """Require X-API-Key when API_KEY is set — OR a verified master login (2026-07-17: the
    dashboard authenticates with Supabase, so the master's JWT works without exposing the
    shared API key to the browser)."""
    if not settings.api_key:
        return  # no key configured — open (dev mode only)
    if api_key and secrets.compare_digest(api_key, settings.api_key):
        return
    auth_header = request.headers.get("authorization", "") if request is not None else ""
    if auth_header:
        try:
            from vula.api.master_auth import require_master
            await require_master(auth_header)
            return
        except HTTPException:
            pass
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key. Set X-API-Key header or sign in as master.",
    )


# ─── Tenant validation ────────────────────────────────────────────────────────

_TENANT_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def validate_tenant(tenant_id: str) -> str:
    if not _TENANT_RE.match(tenant_id):
        raise HTTPException(
            status_code=422,
            detail="tenant_id must be 1–64 alphanumeric characters, underscores, or hyphens.",
        )
    return tenant_id


# ─── OTH manual briefing triggers (for testing / on-demand) ──────────────────

@app.post("/v1/oth/briefing/morning", tags=["oth"])
async def trigger_morning_briefing():
    """Manually fire the morning delivery list — for testing or on-demand sends."""
    await _send_oth_delivery_briefing()
    return {"sent": True, "briefing": "morning"}


@app.post("/v1/oth/briefing/evening", tags=["oth"])
async def trigger_evening_summary():
    """Manually fire the 18:00 sales summary — for testing or on-demand sends."""
    await _send_oth_sales_summary()
    return {"sent": True, "briefing": "evening"}


@app.post("/v1/oth/briefing/low-stock", tags=["oth"])
async def trigger_low_stock_alert():
    """Manually fire the low stock alert to Stacy."""
    await _send_low_stock_alert()
    return {"sent": True, "briefing": "low_stock"}


@app.post("/v1/oth/briefing/friday-catch", tags=["oth"])
async def trigger_friday_catch_reminder():
    """Manually fire the Friday catch reminder to Stacy."""
    await _send_friday_catch_reminder()
    return {"sent": True, "briefing": "friday_catch"}


@app.post("/v1/oth/briefing/chase-unpaid", tags=["oth"])
async def trigger_chase_unpaid():
    """Manually fire the unpaid order follow-up chase."""
    await _chase_unpaid_orders()
    return {"sent": True, "briefing": "chase_unpaid"}


# ─── Request / Response Models ────────────────────────────────────────────────

class QueryRequest(BaseModel):
    tenant_id: str
    question: str
    top_k: int = 5

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant(cls, v: str) -> str:
        return validate_tenant(v)

    @field_validator("question")
    @classmethod
    def _check_question(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question cannot be empty")
        return v[:2000]  # cap length


class QueryResponse(BaseModel):
    answer: str
    sources: List[dict]
    tenant_id: str


class CompanyResearchRequest(BaseModel):
    tenant_id: str
    url: str


class PriceMonitorRequest(BaseModel):
    tenant_id: str
    urls: List[str]


class DigestRequest(BaseModel):
    tenant_id: str
    topic: str
    sources: Optional[List[str]] = None


class TenderRequest(BaseModel):
    tenant_id: str
    keywords: List[str]


class ScrapeRequest(BaseModel):
    tenant_id: str
    urls: List[str]
    extract_prompt: str


# ─── Document Ingestion ───────────────────────────────────────────────────────

async def _analyze_and_file_upload(tenant_id: str, result, file_path, mime_type: Optional[str]) -> None:
    """Bring the dashboard upload dropzone up to the same standard as the email/WhatsApp
    intake channels: classify, extract structured fields, match a project, and — for a
    financial category — commit it into the books via the shared commit_inbound_document
    path. Previously this channel only embedded text into the KB. Best-effort: a failure
    here never affects the (already-succeeded) KB ingestion or the response already sent."""
    try:
        from vula.api.whatsapp import _analyze_document, _file_uploaded_document
        analysis = await _analyze_document(tenant_id, result.filename, file_path)
        if not analysis:
            return
        await _file_uploaded_document(
            tenant_id, "dashboard", result, file_path, mime_type,
            analysis["category"], analysis.get("summary", ""), analysis.get("fields", {}),
            source="dashboard",
        )
    except Exception as exc:
        log.warning("Dashboard-upload analysis/filing failed for %s: %s", result.filename, exc)


@app.post("/ingest", dependencies=[Depends(require_auth)])
@limiter.limit("20/minute")
async def ingest_document(
    request: Request,
    background_tasks: BackgroundTasks,
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a document and ingest it into the tenant knowledge base (async)."""
    validate_tenant(tenant_id)

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_file_mb:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_file_mb}MB limit")

    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / file.filename
    file_path.write_bytes(content)
    mime_type = file.content_type

    async def _ingest() -> None:
        pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
        result = await pipeline.ingest_file(file_path)
        log.info("Ingestion complete: %s → %d chunks (%s)", result.filename, result.chunks_stored, result.status)
        if result.status in ("success", "done"):
            await _analyze_and_file_upload(tenant_id, result, file_path, mime_type)

    background_tasks.add_task(_ingest)

    return {
        "status": "queued",
        "filename": file.filename,
        "tenant_id": tenant_id,
        "size_mb": round(size_mb, 2),
        "message": f"'{file.filename}' is processing. Ready in 2–5 minutes.",
    }


@app.post("/ingest/batch", dependencies=[Depends(require_auth)])
async def ingest_documents_batch(
    background_tasks: BackgroundTasks,
    tenant_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """Upload multiple documents at once — all queued for KB ingestion.

    Portal multi-upload: send up to 20 files in a single multipart request.
    Each file is saved and queued independently so partial failures don't block.
    Returns a per-file status list.
    """
    validate_tenant(tenant_id)
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 files per batch.")

    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for file in files:
        try:
            content = await file.read()
            size_mb = len(content) / (1024 * 1024)
            if size_mb > settings.max_file_mb:
                results.append({"filename": file.filename, "status": "skipped",
                                 "reason": f"exceeds {settings.max_file_mb}MB limit"})
                continue

            file_path = tenant_dir / file.filename
            file_path.write_bytes(content)
            mime_type = file.content_type

            async def _ingest_one(path=file_path, name=file.filename, mime=mime_type) -> None:
                pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
                result = await pipeline.ingest_file(path)
                log.info("Batch ingest: %s → %d chunks (%s)", name, result.chunks_stored, result.status)
                if result.status in ("success", "done"):
                    await _analyze_and_file_upload(tenant_id, result, path, mime)

            background_tasks.add_task(_ingest_one)
            results.append({"filename": file.filename, "status": "queued", "size_mb": round(size_mb, 2)})

        except Exception as exc:
            log.error("Batch ingest error for %s: %s", file.filename, exc)
            results.append({"filename": file.filename, "status": "error", "reason": str(exc)})

    queued = sum(1 for r in results if r["status"] == "queued")
    return {
        "tenant_id": tenant_id,
        "total": len(files),
        "queued": queued,
        "files": results,
        "message": f"{queued}/{len(files)} files queued for KB ingestion.",
    }


@app.post("/ingest/sync", dependencies=[Depends(require_auth)])
async def ingest_document_sync(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Synchronous ingestion — waits for completion. Use for testing."""
    validate_tenant(tenant_id)
    content = await file.read()
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / file.filename
    file_path.write_bytes(content)

    pipeline = VulaIngestionPipeline(tenant_id=tenant_id)
    result = await pipeline.ingest_file(file_path)

    return {
        "status": result.status,
        "filename": result.filename,
        "pages_processed": result.pages_processed,
        "chunks_stored": result.chunks_stored,
        "processing_time_s": result.processing_time_s,
        "error": result.error,
    }


# ─── Query / RAG ─────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_auth)])
@limiter.limit("30/minute")
async def query_knowledge_base(request: Request, body: QueryRequest):
    """Ask a question against the tenant's ingested documents."""
    try:
        pipeline = VulaIngestionPipeline(tenant_id=body.tenant_id)
        sources = await pipeline.query(body.question, top_k=body.top_k)

        if not sources:
            return QueryResponse(
                answer="I don't have enough information in your documents yet. Try uploading more.",
                sources=[],
                tenant_id=body.tenant_id,
            )

        answer = await pipeline.answer(body.question)
        return QueryResponse(
            answer=answer,
            sources=[
                {"filename": s.get("filename"), "page": s.get("page_num"), "excerpt": s.get("text", "")[:200]}
                for s in sources
            ],
            tenant_id=body.tenant_id,
        )
    except Exception as exc:
        log.exception("Query failed for tenant %s: %s", body.tenant_id, exc)
        raise HTTPException(status_code=500, detail=f"Query failed: {type(exc).__name__}: {exc}")


# ─── Web Scraper Endpoints ────────────────────────────────────────────────────

@app.post("/scrape/company", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def research_company(request: Request, body: CompanyResearchRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    profile = await scraper.research_company(body.url)
    return {
        "url": profile.url, "name": profile.name, "description": profile.description,
        "services": profile.services, "contact_info": profile.contact_info,
        "social_links": profile.social_links, "key_facts": profile.key_facts,
        "pitch_angle": profile.suggested_pitch_angle,
    }


@app.post("/scrape/prices", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def monitor_prices(request: Request, body: PriceMonitorRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    results = await scraper.monitor_prices(body.urls)
    return {
        "monitored": len(results),
        "results": [{"url": r.url, "products": r.products, "scraped_at": r.scraped_at, "changes": r.changes_detected} for r in results],
    }


@app.post("/scrape/digest", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def news_digest(request: Request, body: DigestRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    digest = await scraper.industry_digest(body.topic, body.sources)
    return {"topic": digest.topic, "generated_at": digest.generated_at, "key_trends": digest.key_trends, "articles": digest.articles}


@app.post("/scrape/tenders", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def monitor_tenders(request: Request, body: TenderRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    tenders = await scraper.monitor_tenders(body.keywords)
    return {"keywords": body.keywords, "tenders_found": len(tenders), "tenders": tenders}


@app.post("/scrape/custom", dependencies=[Depends(require_auth)])
@limiter.limit("10/minute")
async def custom_scrape(request: Request, body: ScrapeRequest):
    scraper = VulaWebScraper(tenant_id=body.tenant_id)
    results = await scraper.scrape_urls(body.urls, body.extract_prompt)
    return {
        "scraped": len(results),
        "results": [{"url": r.url, "title": r.title, "status": r.status, "data": r.extracted_data, "time_s": r.scrape_time_s} for r in results],
    }


# ─── Health & Status ──────────────────────────────────────────────────────────

@app.get("/status")
async def health_check():
    """Public health check — no auth required."""
    checks: dict = {}

    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            resp = await client.get(f"{settings.ollama_base}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models": models}
        except Exception as exc:
            checks["ollama"] = {"status": "error", "detail": str(exc)}

        try:
            qdrant_headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
            resp = await client.get(f"{settings.qdrant_base}/collections", headers=qdrant_headers)
            n = len(resp.json().get("result", {}).get("collections", []))
            checks["qdrant"] = {"status": "ok", "collections": n}
        except Exception as exc:
            checks["qdrant"] = {"status": "error", "detail": str(exc)}

        # Ollama on Railway routes to OpenRouter — check accordingly
        if checks.get("ollama", {}).get("status") == "error" and settings.openrouter_api_key:
            checks["ollama"] = {"status": "ok", "models": ["openrouter/cloud"], "note": "via OpenRouter"}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "service": "vula-api", "version": "1.0.0", "checks": checks}


@app.get("/metrics", dependencies=[Depends(require_auth)])
async def metrics():
    """Request performance metrics — total, errors, rolling average latency."""
    lats = _request_stats["latencies_ms"]
    avg_lat = int(sum(lats) / len(lats)) if lats else 0
    p95_lat = sorted(lats)[int(len(lats) * 0.95)] if lats else 0
    try:
        from core.memory.reflection import ReflectionAgent
        reflection_stats = ReflectionAgent().get_stats()
    except Exception:
        reflection_stats = {}
    return {
        "requests": {
            "total": _request_stats["total"],
            "errors": _request_stats["errors"],
            "error_rate": round(_request_stats["errors"] / max(_request_stats["total"], 1), 3),
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95_lat,
        },
        "agent": reflection_stats,
        "service": "vula-api",
    }


@app.get("/ingest/status/{tenant_id}", dependencies=[Depends(require_auth)])
async def ingestion_status(tenant_id: str):
    """Return document ingestion status for a tenant."""
    validate_tenant(tenant_id)
    from vula.ingestion.pipeline import get_tracker
    docs = get_tracker().get_all(tenant_id)
    return {"tenant_id": tenant_id, "documents": docs, "count": len(docs)}


@app.get("/documents/{tenant_id}", dependencies=[Depends(require_auth)])
async def list_documents(tenant_id: str):
    validate_tenant(tenant_id)

    # Files physically uploaded to this Railway instance
    docs = []
    tenant_dir = UPLOAD_DIR / tenant_id
    if tenant_dir.exists():
        docs = [
            {"filename": f.name, "size_kb": f.stat().st_size // 1024, "type": f.suffix}
            for f in sorted(tenant_dir.iterdir())
            if f.is_file()
        ]

    # True KB size = vector count in the tenant's Qdrant collection.
    # This reflects all ingested knowledge even if files were ingested elsewhere.
    kb_chunks = 0
    try:
        collection = f"vula_{tenant_id.replace('-', '_')}"
        qdrant_headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.qdrant_base}/collections/{collection}",
                headers=qdrant_headers,
            )
            if resp.status_code == 200:
                kb_chunks = resp.json().get("result", {}).get("points_count", 0) or 0
    except Exception as exc:
        log.debug("Qdrant chunk count failed for %s: %s", tenant_id, exc)

    return {
        "tenant_id": tenant_id,
        "documents": docs,
        "count": len(docs),
        "kb_chunks": kb_chunks,
    }


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("\n  Vula AI Server — by Vula Group")
    print(f"  http://{settings.api_host}:{settings.api_port}\n")
    uvicorn.run(
        "vula.api.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
