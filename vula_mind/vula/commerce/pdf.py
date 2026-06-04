"""
vula/commerce/pdf.py — Invoice & Quote PDF renderer.

Uses Jinja2 (already available via FastAPI/Starlette) to render an HTML
template, then WeasyPrint to convert it to PDF bytes.

Usage:
    from vula.commerce.pdf import render_invoice_pdf
    pdf_bytes = render_invoice_pdf(invoice_dict, tenant_profile_dict)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Jinja2 template ───────────────────────────────────────────────────────────
_TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @page { size: A4; margin: 18mm 16mm 20mm 16mm; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 11pt; color: #1a1a1a; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; }
  .brand h1 { font-size: 22pt; font-weight: 800; color: {{ accent }}; margin-bottom: 2px; }
  .brand p  { font-size: 9pt; color: #555; line-height: 1.5; }
  .doc-title { text-align: right; }
  .doc-title h2 { font-size: 18pt; font-weight: 700; color: {{ accent }}; text-transform: uppercase; letter-spacing: 1px; }
  .doc-title .num { font-size: 13pt; color: #333; margin-top: 4px; }
  .doc-title .dates { font-size: 9pt; color: #666; margin-top: 6px; line-height: 1.6; }
  .parties { display: flex; gap: 32px; margin-bottom: 28px; }
  .party { flex: 1; background: #f8f8f8; border-radius: 6px; padding: 14px 16px; }
  .party h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.8px; color: #888; margin-bottom: 8px; }
  .party p  { font-size: 10pt; line-height: 1.6; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  thead th { background: {{ accent }}; color: #fff; padding: 9px 10px; font-size: 9.5pt; text-align: left; }
  thead th:last-child, thead th:nth-last-child(2), thead th:nth-last-child(3) { text-align: right; }
  tbody tr:nth-child(even) { background: #f9f9f9; }
  tbody td { padding: 8px 10px; font-size: 10pt; border-bottom: 1px solid #eee; vertical-align: top; }
  tbody td:last-child, tbody td:nth-last-child(2), tbody td:nth-last-child(3) { text-align: right; }
  .totals { width: 280px; margin-left: auto; margin-bottom: 28px; }
  .totals tr td { padding: 5px 8px; font-size: 10.5pt; }
  .totals tr td:last-child { text-align: right; font-weight: 500; }
  .totals .total-row td { font-weight: 800; font-size: 12pt; border-top: 2px solid {{ accent }}; padding-top: 8px; color: {{ accent }}; }
  .notes { background: #f0f7f4; border-left: 4px solid {{ accent }}; padding: 12px 16px; border-radius: 0 6px 6px 0; margin-bottom: 28px; font-size: 9.5pt; line-height: 1.6; }
  .payment { background: #fafafa; border: 1px solid #e8e8e8; border-radius: 6px; padding: 14px 16px; margin-bottom: 28px; font-size: 9.5pt; line-height: 1.7; }
  .payment h3 { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.8px; color: #888; margin-bottom: 8px; }
  .footer { border-top: 1px solid #ddd; padding-top: 10px; font-size: 8.5pt; color: #888; text-align: center; line-height: 1.6; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
  .badge-draft    { background: #f0f0f0; color: #666; }
  .badge-sent     { background: #e8f4fd; color: #1a73e8; }
  .badge-paid     { background: #e6f4ea; color: #1e7e34; }
  .badge-overdue  { background: #fde8e8; color: #c62828; }
  .badge-accepted { background: #e6f4ea; color: #1e7e34; }
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    <h1>{{ tenant_name }}</h1>
    <p>{{ tenant_address | replace("\\n", "<br>") | safe }}</p>
    {% if tenant_email %}<p>{{ tenant_email }}</p>{% endif %}
    {% if tenant_phone %}<p>{{ tenant_phone }}</p>{% endif %}
    {% if tenant_reg %}<p>Reg: {{ tenant_reg }}</p>{% endif %}
    {% if tenant_vat %}<p>VAT No: {{ tenant_vat }}</p>{% endif %}
  </div>
  <div class="doc-title">
    <span class="badge badge-{{ status }}">{{ status }}</span>
    <h2>{{ doc_label }}</h2>
    <div class="num">{{ invoice_number }}</div>
    <div class="dates">
      Issue date: <strong>{{ issue_date }}</strong><br>
      {% if due_date %}Due date: <strong>{{ due_date }}</strong>{% endif %}
      {% if valid_until %}Valid until: <strong>{{ valid_until }}</strong>{% endif %}
    </div>
  </div>
</div>

<div class="parties">
  <div class="party">
    <h3>Bill To</h3>
    <p><strong>{{ customer_name }}</strong><br>
    {% if customer_address %}{{ customer_address | replace("\\n", "<br>") | safe }}<br>{% endif %}
    {% if customer_email %}{{ customer_email }}<br>{% endif %}
    {% if customer_phone %}{{ customer_phone }}{% endif %}
    </p>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th style="width:50%">Description</th>
      <th style="width:10%">Qty</th>
      <th style="width:20%">Unit Price</th>
      <th style="width:20%">Total</th>
    </tr>
  </thead>
  <tbody>
    {% for item in line_items %}
    <tr>
      <td>{{ item.description }}</td>
      <td>{{ item.quantity }}</td>
      <td>R{{ "%.2f" | format(item.unit_price_cents / 100) }}</td>
      <td>R{{ "%.2f" | format(item.total_cents / 100) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<table class="totals">
  <tr><td>Subtotal</td><td>R{{ "%.2f" | format(subtotal_cents / 100) }}</td></tr>
  <tr><td>VAT ({{ vat_rate | int }}%)</td><td>R{{ "%.2f" | format(vat_cents / 100) }}</td></tr>
  <tr class="total-row"><td>TOTAL DUE</td><td>R{{ "%.2f" | format(total_cents / 100) }}</td></tr>
</table>

{% if notes %}
<div class="notes"><strong>Notes:</strong> {{ notes }}</div>
{% endif %}

{% if payment_info %}
<div class="payment">
  <h3>Payment Details</h3>
  {{ payment_info | replace("\\n", "<br>") | safe }}
</div>
{% endif %}

<div class="footer">
  {{ tenant_name }} &mdash; {{ tenant_address | replace("\\n", " | ") | safe }}<br>
  Thank you for your business.
</div>
</body>
</html>"""



# ── Default tenant branding ───────────────────────────────────────────────────

_TENANT_DEFAULTS: dict[str, dict] = {
    "off-the-hook": {
        "name": "Off the Hook",
        "address": "Cape Town, South Africa",
        "email": "info@offthehook.capetown",
        "phone": "+27 73 781 5979",
        "reg": None,
        "vat": None,
        "accent": "#0077b6",
        "payment_info": (
            "EFT Payment:\n"
            "Bank: FNB\n"
            "Account Name: Off the Hook\n"
            "Branch Code: 250655\n"
            "Please use your invoice number as reference."
        ),
    },
}

_DOC_LABELS = {
    "invoice": "Tax Invoice",
    "quote": "Quotation",
    "proforma": "Pro Forma Invoice",
}


# ── Public API ────────────────────────────────────────────────────────────────

def render_invoice_pdf(invoice: dict, tenant_profile: Optional[dict] = None) -> bytes:
    """Render an invoice / quote dict as PDF bytes.

    Args:
        invoice: Row from commerce_invoices (as returned by service.get_invoice).
        tenant_profile: Optional branding override dict. Falls back to
                        _TENANT_DEFAULTS keyed by invoice["tenant_id"].

    Returns:
        Raw PDF bytes ready to stream as application/pdf.
    """
    try:
        from jinja2 import Environment
        from weasyprint import HTML as WP_HTML
    except ImportError as exc:
        raise RuntimeError(
            "PDF rendering requires weasyprint and jinja2. "
            "Run: pip install weasyprint"
        ) from exc

    tenant_id = invoice.get("tenant_id", "")
    branding = tenant_profile or _TENANT_DEFAULTS.get(tenant_id, {})

    # Parse line_items — stored as JSON string or already a list
    line_items = invoice.get("line_items") or []
    if isinstance(line_items, str):
        try:
            line_items = json.loads(line_items)
        except Exception:
            line_items = []

    # Ensure every item has total_cents computed
    for item in line_items:
        if item.get("total_cents") is None:
            item["total_cents"] = int(item.get("quantity", 1)) * int(item.get("unit_price_cents", 0))

    doc_type = invoice.get("doc_type", "invoice")

    ctx: dict[str, Any] = {
        # Tenant branding
        "tenant_name": branding.get("name", tenant_id.replace("-", " ").title()),
        "tenant_address": branding.get("address", ""),
        "tenant_email": branding.get("email", ""),
        "tenant_phone": branding.get("phone", ""),
        "tenant_reg": branding.get("reg") or "",
        "tenant_vat": branding.get("vat") or "",
        "accent": branding.get("accent", "#1a7a4a"),
        "payment_info": branding.get("payment_info", ""),
        # Document meta
        "doc_label": _DOC_LABELS.get(doc_type, "Document"),
        "invoice_number": invoice.get("invoice_number", ""),
        "status": invoice.get("status", "draft"),
        "issue_date": (invoice.get("issue_date") or "")[:10],
        "due_date": (invoice.get("due_date") or "")[:10] or None,
        "valid_until": (invoice.get("valid_until") or "")[:10] or None,
        # Customer
        "customer_name": invoice.get("customer_name", ""),
        "customer_email": invoice.get("customer_email", ""),
        "customer_phone": invoice.get("customer_phone", ""),
        "customer_address": invoice.get("customer_address", ""),
        # Financials — always integer cents, never floats in storage
        "line_items": line_items,
        "subtotal_cents": int(invoice.get("subtotal_cents") or 0),
        "vat_rate": float(invoice.get("vat_rate") or 15.0),
        "vat_cents": int(invoice.get("vat_cents") or 0),
        "total_cents": int(invoice.get("total_cents") or 0),
        "notes": invoice.get("notes") or "",
    }

    env = Environment(autoescape=False)
    html_str = env.from_string(_TEMPLATE_HTML).render(**ctx)
    pdf_bytes = WP_HTML(string=html_str).write_pdf()

    log.info(
        "PDF rendered: %s %s for %s (%d bytes)",
        doc_type, ctx["invoice_number"], tenant_id, len(pdf_bytes),
    )
    return pdf_bytes
