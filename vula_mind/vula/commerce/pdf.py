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

# Font pairing (migration 078's font_pairing setting, previously dashboard/storefront-only —
# never reached the PDF). Keys match vula_dashboard/src/theme/tokens.js's FONT_PAIRINGS; family
# names are duplicated here (not imported — that file is JS) since pdf.py can't share it directly.
# "" (unset) keeps today's hardcoded fallback so existing tenants render identically.
_FONT_STACKS = {
    "":          "'Helvetica Neue', Arial, sans-serif",
    "vula":      "'Cormorant Garamond', Georgia, serif",
    "modern":    "'Poppins', system-ui, sans-serif",
    "editorial": "'Playfair Display', Georgia, serif",
    "classic":   "'Merriweather', Georgia, serif",
}
# Google Fonts CSS2 family+weight query string per pairing — WeasyPrint fetches remote
# stylesheets/fonts by default, same as a browser, so a <link> in the document head is enough.
_FONT_IMPORTS = {
    "vula":      "Cormorant+Garamond:wght@500;600;700",
    "modern":    "Poppins:wght@500;600;700",
    "editorial": "Playfair+Display:wght@500;600;700",
    "classic":   "Merriweather:wght@500;600;700",
}

# ── Jinja2 templates ──────────────────────────────────────────────────────────
# One shared body markup, themed by three interchangeable CSS blocks. The chosen
# CSS is substituted into the document skeleton before rendering, so the accent
# colour and every other Jinja value resolve in a single pass.

_CSS_CLASSIC = """
  @page { size: A4; margin: 18mm 16mm 20mm 16mm; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: {{ font_family }}; font-size: 11pt; color: {{ ink_color or '#1a1a1a' }}; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 32px; }
  .brand h1 { font-size: 22pt; font-weight: 800; color: {{ accent }}; margin-bottom: 2px; }
  .brand .legal-name { font-size: 8.5pt; color: #888; font-weight: 400; margin-top: 2px; }
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
"""

# Minimal — monochrome, hairline rules, no fills. Clean and ink-light.
_CSS_MINIMAL = """
  @page { size: A4; margin: 20mm 18mm 22mm 18mm; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: {{ font_family }}; font-size: 10.5pt; color: {{ ink_color or '#222' }}; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 36px; padding-bottom: 16px; border-bottom: 1px solid #222; }
  .brand h1 { font-size: 18pt; font-weight: 600; color: #111; margin-bottom: 2px; letter-spacing: 0.5px; }
  .brand .legal-name { font-size: 8pt; color: #888; font-weight: 400; margin-top: 2px; }
  .brand p  { font-size: 8.5pt; color: #777; line-height: 1.5; }
  .doc-title { text-align: right; }
  .doc-title h2 { font-size: 13pt; font-weight: 600; color: #111; text-transform: uppercase; letter-spacing: 3px; }
  .doc-title .num { font-size: 11pt; color: #555; margin-top: 4px; }
  .doc-title .dates { font-size: 8.5pt; color: #888; margin-top: 6px; line-height: 1.6; }
  .parties { display: flex; gap: 32px; margin-bottom: 28px; }
  .party { flex: 1; padding: 0; }
  .party h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 1px; color: #aaa; margin-bottom: 6px; }
  .party p  { font-size: 10pt; line-height: 1.6; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  thead th { background: transparent; color: #111; padding: 8px 10px; font-size: 8.5pt; text-align: left; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1.5px solid #222; }
  thead th:last-child, thead th:nth-last-child(2), thead th:nth-last-child(3) { text-align: right; }
  tbody td { padding: 8px 10px; font-size: 10pt; border-bottom: 1px solid #eee; vertical-align: top; }
  tbody td:last-child, tbody td:nth-last-child(2), tbody td:nth-last-child(3) { text-align: right; }
  .totals { width: 280px; margin-left: auto; margin-bottom: 28px; }
  .totals tr td { padding: 5px 8px; font-size: 10.5pt; }
  .totals tr td:last-child { text-align: right; font-weight: 500; }
  .totals .total-row td { font-weight: 700; font-size: 12pt; border-top: 1.5px solid #222; padding-top: 8px; color: #111; }
  .notes { padding: 12px 0; margin-bottom: 24px; font-size: 9.5pt; line-height: 1.6; color: #555; border-top: 1px solid #eee; }
  .payment { padding: 14px 0; margin-bottom: 24px; font-size: 9.5pt; line-height: 1.7; border-top: 1px solid #eee; }
  .payment h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 1px; color: #aaa; margin-bottom: 8px; }
  .footer { border-top: 1px solid #222; padding-top: 10px; font-size: 8pt; color: #999; text-align: center; line-height: 1.6; }
  .badge { display: inline-block; padding: 2px 0; font-size: 8pt; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; color: #888; }
"""

# Modern — bold accent band, rounded cards, high contrast totals.
_CSS_MODERN = """
  @page { size: A4; margin: 0 0 18mm 0; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: {{ font_family }}; font-size: 11pt; color: {{ ink_color or '#1a1a1a' }}; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin: 0 0 28px; padding: 28px 18mm 22px; background: {{ accent }}; color: #fff; }
  .brand h1 { font-size: 24pt; font-weight: 800; color: #fff; margin-bottom: 2px; }
  .brand .legal-name { font-size: 8.5pt; color: rgba(255,255,255,0.65); font-weight: 400; margin-top: 2px; }
  .brand p  { font-size: 9pt; color: rgba(255,255,255,0.85); line-height: 1.5; }
  .doc-title { text-align: right; }
  .doc-title h2 { font-size: 17pt; font-weight: 700; color: #fff; text-transform: uppercase; letter-spacing: 2px; }
  .doc-title .num { font-size: 12pt; color: rgba(255,255,255,0.9); margin-top: 4px; }
  .doc-title .dates { font-size: 9pt; color: rgba(255,255,255,0.8); margin-top: 6px; line-height: 1.6; }
  .parties { display: flex; gap: 20px; margin: 0 18mm 28px; }
  .party { flex: 1; background: #f4f6f8; border-radius: 10px; padding: 16px 18px; }
  .party h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.8px; color: {{ accent }}; margin-bottom: 8px; font-weight: 700; }
  .party p  { font-size: 10pt; line-height: 1.6; }
  table { width: calc(100% - 36mm); border-collapse: separate; border-spacing: 0; margin: 0 18mm 24px; }
  thead th { background: #1a1a1a; color: #fff; padding: 11px 12px; font-size: 9.5pt; text-align: left; }
  thead th:first-child { border-radius: 8px 0 0 8px; }
  thead th:last-child { border-radius: 0 8px 8px 0; }
  thead th:last-child, thead th:nth-last-child(2), thead th:nth-last-child(3) { text-align: right; }
  tbody tr:nth-child(even) { background: #f4f6f8; }
  tbody td { padding: 10px 12px; font-size: 10pt; border-bottom: 1px solid #eef1f4; vertical-align: top; }
  tbody td:last-child, tbody td:nth-last-child(2), tbody td:nth-last-child(3) { text-align: right; }
  .totals { width: 300px; margin: 0 18mm 28px auto; }
  .totals tr td { padding: 6px 10px; font-size: 10.5pt; }
  .totals tr td:last-child { text-align: right; font-weight: 500; }
  .totals .total-row td { font-weight: 800; font-size: 13pt; background: {{ accent }}; color: #fff; padding: 12px 10px; border-radius: 8px; }
  .notes { background: #f4f6f8; border-radius: 10px; padding: 14px 18px; margin: 0 18mm 24px; font-size: 9.5pt; line-height: 1.6; }
  .payment { background: #fff; border: 1.5px solid {{ accent }}; border-radius: 10px; padding: 16px 18px; margin: 0 18mm 24px; font-size: 9.5pt; line-height: 1.7; }
  .payment h3 { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.8px; color: {{ accent }}; margin-bottom: 8px; font-weight: 700; }
  .footer { margin: 0 18mm; border-top: 1px solid #e4e8ec; padding-top: 10px; font-size: 8.5pt; color: #999; text-align: center; line-height: 1.6; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; background: rgba(255,255,255,0.2); color: #fff; }
"""

_CSS_BRANDED = """
  @page { size: A4; margin: 16mm 0 18mm 0; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: {{ font_family }}; font-size: 11pt; color: {{ ink_color or '#1f2430' }}; }
  .header { text-align: center; padding: 0 18mm 18px; border-bottom: 3px solid {{ accent }}; margin: 0 0 26px; }
  .brand img { margin: 0 auto 8px; }
  .brand h1 { font-size: 21pt; font-weight: 800; color: {{ accent }}; letter-spacing: -0.5px; }
  .brand .legal-name { font-size: 8.5pt; color: #888; font-weight: 400; margin-top: 2px; }
  .brand p  { font-size: 9pt; color: #6b7280; line-height: 1.5; }
  .doc-title { margin-top: 12px; }
  .doc-title h2 { font-size: 14pt; font-weight: 700; color: #1f2430; text-transform: uppercase; letter-spacing: 3px; }
  .doc-title .num { font-size: 11pt; color: {{ accent }}; margin-top: 2px; font-weight: 600; }
  .doc-title .dates { font-size: 9pt; color: #6b7280; margin-top: 6px; line-height: 1.6; }
  .badge { display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; background: {{ accent }}; color: #fff; }
  .parties { display: flex; gap: 20px; margin: 0 18mm 26px; }
  .party { flex: 1; border: 1px solid #e7e9ee; border-radius: 10px; padding: 15px 18px; }
  .party h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.8px; color: {{ accent }}; margin-bottom: 8px; font-weight: 700; }
  .party p  { font-size: 10pt; line-height: 1.6; }
  table { width: calc(100% - 36mm); border-collapse: separate; border-spacing: 0; margin: 0 18mm 22px; }
  thead th { background: {{ accent }}; color: #fff; padding: 11px 12px; font-size: 9.5pt; text-align: left; }
  thead th:first-child { border-radius: 8px 0 0 8px; }
  thead th:last-child { border-radius: 0 8px 8px 0; }
  thead th:last-child, thead th:nth-last-child(2), thead th:nth-last-child(3) { text-align: right; }
  tbody td { padding: 10px 12px; font-size: 10pt; border-bottom: 1px solid #eef1f4; vertical-align: top; }
  tbody td:last-child, tbody td:nth-last-child(2), tbody td:nth-last-child(3) { text-align: right; }
  .totals { width: 300px; margin: 0 18mm 26px auto; }
  .totals tr td { padding: 6px 10px; font-size: 10.5pt; }
  .totals tr td:last-child { text-align: right; font-weight: 500; }
  .totals .total-row td { font-weight: 800; font-size: 13pt; background: {{ accent }}; color: #fff; padding: 12px 10px; border-radius: 8px; }
  .notes { border-left: 3px solid {{ accent }}; padding: 6px 16px; margin: 0 18mm 22px; font-size: 9.5pt; line-height: 1.6; color: #4b5563; }
  .payment { background: #fff; border: 1.5px solid {{ accent }}; border-radius: 10px; padding: 16px 18px; margin: 0 18mm 22px; font-size: 9.5pt; line-height: 1.7; }
  .payment h3 { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.8px; color: {{ accent }}; margin-bottom: 8px; font-weight: 700; }
  .footer { margin: 0 18mm; border-top: 1px solid #e4e8ec; padding-top: 10px; font-size: 8.5pt; color: #9aa1ad; text-align: center; line-height: 1.6; }
"""

# DIGG — certified-payment (JBCC) invoice look: hairline rules, black table header bar,
# plain (unboxed) party block, muted subtotal rows building up to a bold TOTAL DUE. Modelled
# on DIGG's actual HPC interim-payment-certificate invoices.
_CSS_DIGG = """
  @page { size: A4; margin: 18mm 16mm 20mm 16mm; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: {{ font_family }}; font-size: 10.5pt; color: {{ ink_color or '#1E1E1E' }}; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; padding-bottom: 14px; border-bottom: 2px solid {{ accent }}; }
  .brand img { margin-bottom: 8px; display: block; }
  .brand h1 { font-size: 17pt; font-weight: 800; color: {{ accent }}; margin-bottom: 2px; }
  .brand .legal-name { font-size: 8pt; color: #999; font-weight: 400; margin-top: 2px; }
  .brand p  { font-size: 8.5pt; color: #666; line-height: 1.55; }
  .doc-title { text-align: right; }
  .doc-title h2 { font-size: 13pt; font-weight: 700; color: #1E1E1E; text-transform: uppercase; letter-spacing: 2px; }
  .doc-title .num { font-size: 10.5pt; color: {{ accent }}; margin-top: 4px; font-weight: 700; }
  .doc-title .dates { font-size: 8.5pt; color: #666; margin-top: 6px; line-height: 1.6; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 7.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; background: {{ accent }}; color: #fff; }
  .parties { margin-bottom: 22px; }
  .party { padding: 0; }
  .party h3 { font-size: 7.5pt; text-transform: uppercase; letter-spacing: 1px; color: #999; margin-bottom: 6px; font-weight: 700; }
  .party p  { font-size: 10pt; line-height: 1.6; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 22px; }
  thead th { background: #1E1E1E; color: #fff; padding: 8px 10px; font-size: 8.5pt; text-align: left; text-transform: uppercase; letter-spacing: 0.5px; }
  thead th:last-child { text-align: right; }
  tbody td { padding: 7px 10px; font-size: 9.5pt; border-bottom: 1px solid #eee; vertical-align: top; }
  tbody td:last-child { text-align: right; }
  tbody tr.cert-bold td { font-weight: 700; border-top: 1.5px solid #1E1E1E; border-bottom: 1.5px solid #1E1E1E; }
  .totals { width: 300px; margin-left: auto; margin-bottom: 24px; }
  .totals tr td { padding: 4px 8px; font-size: 9.5pt; color: #555; }
  .totals tr td:last-child { text-align: right; font-weight: 500; }
  .totals .muted-row td { color: #999; font-size: 8.5pt; }
  .totals .total-row td { font-weight: 800; font-size: 12pt; color: #1E1E1E; border-top: 2px solid {{ accent }}; padding-top: 8px; }
  .notes { padding: 12px 0; margin-bottom: 22px; font-size: 8.5pt; line-height: 1.65; color: #555; border-top: 1px solid #eee; }
  .notes strong { color: #1E1E1E; text-transform: uppercase; letter-spacing: 0.5px; font-size: 8pt; }
  .payment { background: #fafafa; border: 1px solid #e8e8e8; border-radius: 6px; padding: 14px 16px; margin-bottom: 22px; font-size: 9.5pt; line-height: 1.7; }
  .payment h3 { font-size: 8pt; text-transform: uppercase; letter-spacing: 0.8px; color: #888; margin-bottom: 8px; font-weight: 700; }
  .footer { border-top: 1px solid #ddd; padding-top: 10px; font-size: 8pt; color: #999; text-align: center; line-height: 1.6; }
"""

_TEMPLATE_CSS: dict[str, str] = {
    "classic": _CSS_CLASSIC,
    "minimal": _CSS_MINIMAL,
    "modern": _CSS_MODERN,
    "branded": _CSS_BRANDED,
    "digg": _CSS_DIGG,
}

# template_choice values that use the certified-payment (JBCC) layout: a plain description +
# amount table (no qty/unit/price columns), bold running-subtotal rows, and a
# Subtotal/VAT-N-A/TOTAL DUE totals block instead of the standard discount/VAT breakdown.
_CERT_STYLE_TEMPLATES = {"digg"}

# Shared document skeleton. ``__TEMPLATE_CSS__`` is replaced with the chosen
# theme before a single Jinja render pass resolves all values.
_DOC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
{% if font_import_url %}<link rel="stylesheet" href="{{ font_import_url }}">{% endif %}
<style>__TEMPLATE_CSS__</style>
</head>
<body>

<div class="header">
  <div class="brand"{% if logo_align == 'center' %} style="text-align:center;"{% endif %}>
    {% if tenant_logo %}<img src="{{ tenant_logo }}" alt="logo" style="max-height:{{ '56px' if logo_size == 'sm' else ('100px' if logo_size == 'lg' else '76px') }};max-width:{{ '180px' if logo_size == 'sm' else ('320px' if logo_size == 'lg' else '240px') }};margin-bottom:10px;display:block;{% if logo_align == 'center' %}margin-left:auto;margin-right:auto;{% endif %}">{% endif %}
    {% if trading_as %}
    <h1>{{ trading_as }}</h1>
    <p class="legal-name">{{ tenant_name }}</p>
    {% else %}
    <h1>{{ tenant_name }}</h1>
    {% endif %}
    <p>{{ tenant_address | replace("\\n", "<br>") | safe }}</p>
    {% if tenant_email %}<p>{{ tenant_email }}</p>{% endif %}
    {% if tenant_phone %}<p>{{ tenant_phone }}</p>{% endif %}
    {% if tenant_reg and show_company_reg %}<p>Reg: {{ tenant_reg }}</p>{% endif %}
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

{% if cert_style %}
<table>
  <thead>
    <tr>
      <th style="width:78%">Description</th>
      <th style="width:22%">Amount (ZAR)</th>
    </tr>
  </thead>
  <tbody>
    {% for item in line_items %}
    <tr{% if item.bold %} class="cert-bold"{% endif %}>
      <td>{{ item.description }}</td>
      <td>{{ "-" if item.total_cents < 0 else "" }}R{{ "%.2f" | format(item.total_cents | abs / 100) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<table>
  <thead>
    <tr>
      <th style="width:44%">Description</th>
      <th style="width:9%">Qty</th>
      <th style="width:9%">Unit</th>
      <th style="width:19%">Unit Price</th>
      <th style="width:19%">Total</th>
    </tr>
  </thead>
  <tbody>
    {% for item in line_items %}
    <tr>
      <td>{{ item.description }}{% if item.discount_pct %} <span style="color:#8A8680">(−{{ "%g" | format(item.discount_pct | float) }}%)</span>{% endif %}</td>
      <td>{{ "%g" | format(item.quantity | float) }}</td>
      <td>{{ item.unit or "" }}</td>
      <td>R{{ "%.2f" | format(item.unit_price_cents / 100) }}{% if item.unit %}/{{ item.unit }}{% endif %}</td>
      <td>R{{ "%.2f" | format(item.total_cents / 100) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

<table class="totals">
  {% if cert_style %}
  <tr class="muted-row"><td>Subtotal (excl. VAT)</td><td>R{{ "%.2f" | format(subtotal_cents / 100) }}</td></tr>
  {% if show_vat_breakdown %}<tr class="muted-row"><td>VAT</td><td>{% if vat_registered == false %}N/A — not VAT registered{% else %}R{{ "%.2f" | format(vat_cents / 100) }}{% endif %}</td></tr>{% endif %}
  <tr class="total-row"><td>{% if deposit_cents %}TOTAL{% else %}TOTAL DUE{% endif %}</td><td>R{{ "%.2f" | format(total_cents / 100) }}</td></tr>
  {% else %}
  <tr><td>Subtotal</td><td>R{{ "%.2f" | format(subtotal_cents / 100) }}</td></tr>
  {% if discount_cents %}<tr><td>Discount</td><td>−R{{ "%.2f" | format(discount_cents / 100) }}</td></tr>{% endif %}
  {% if show_vat_breakdown %}<tr><td>VAT ({{ vat_rate | int }}%)</td><td>R{{ "%.2f" | format(vat_cents / 100) }}</td></tr>{% endif %}
  <tr class="total-row"><td>{% if deposit_cents %}TOTAL{% else %}TOTAL DUE{% endif %}</td><td>R{{ "%.2f" | format(total_cents / 100) }}</td></tr>
  {% endif %}
  {% if deposit_cents %}<tr><td>Deposit paid</td><td>−R{{ "%.2f" | format(deposit_cents / 100) }}</td></tr>
  <tr class="total-row"><td>BALANCE DUE</td><td>R{{ "%.2f" | format((total_cents - deposit_cents) / 100) }}</td></tr>{% endif %}
</table>

{% if notes %}
<div class="notes"><strong>{% if cert_style %}Terms &amp; Notes{% else %}Notes:{% endif %}</strong><br>{{ notes | replace("\\n", "<br>") | safe }}</div>
{% endif %}

{% if payment_info %}
<div class="payment">
  <h3>{% if cert_style %}Banking Details{% else %}Payment Details{% endif %}</h3>
  {{ payment_info | replace("\\n", "<br>") | safe }}
  {% if pay_url %}<p style="margin-top:8px;"><a href="{{ pay_url }}" style="display:inline-block;background:{{ accent }};color:#fff;text-decoration:none;padding:8px 16px;border-radius:6px;font-weight:700;">💳 Pay now online</a></p>{% endif %}
</div>
{% elif pay_url %}
<div class="payment"><h3>Pay online</h3><p><a href="{{ pay_url }}" style="display:inline-block;background:{{ accent }};color:#fff;text-decoration:none;padding:8px 16px;border-radius:6px;font-weight:700;">💳 Pay now</a></p></div>
{% endif %}

<div class="footer">
  {{ tenant_name }} &mdash; {{ tenant_address | replace("\\n", " | ") | safe }}<br>
  {{ footer_text if footer_text else "Thank you for your business." }}
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
    "credit_note": "Credit Note",
}


def _payment_info_from_settings(settings: dict) -> str:
    """Build the EFT payment block from saved banking fields. Returns '' when no
    banking details are present (so the default/payment section stays hidden)."""
    lines = []
    if settings.get("bank_name"):
        lines.append(f"Bank: {settings['bank_name']}")
    if settings.get("account_name"):
        lines.append(f"Account Name: {settings['account_name']}")
    if settings.get("account_number"):
        lines.append(f"Account Number: {settings['account_number']}")
    if settings.get("branch_code"):
        lines.append(f"Branch Code: {settings['branch_code']}")
    if not lines:
        return ""
    return "EFT Payment:\n" + "\n".join(lines) + \
        "\nPlease use your invoice number as reference."


def merge_branding(tenant_id: str, settings: Optional[dict]) -> dict:
    """Combine static tenant defaults with the tenant's saved invoice settings.

    Saved settings (VAT number, registered address, banking details, template
    choice, accent) take precedence over the built-in defaults. The result is a
    plain branding dict suitable for ``render_invoice_pdf``.
    """
    branding = dict(_TENANT_DEFAULTS.get(tenant_id, {}))
    if not settings:
        return branding
    # Company identity — these were previously never carried from settings, leaving
    # non-default tenants' invoices with a title-cased tenant_id and blank contact info.
    for src, dst in (("company_name", "name"), ("company_email", "email"),
                     ("company_phone", "phone"), ("company_reg", "reg"),
                     ("trading_as", "trading_as"), ("logo_url", "logo_url")):
        if settings.get(src):
            branding[dst] = settings[src]
    if settings.get("vat_number"):
        branding["vat"] = settings["vat_number"]
    if settings.get("registered_address"):
        branding["address"] = settings["registered_address"]
    if settings.get("accent_color"):
        branding["accent"] = settings["accent_color"]
    branding["vat_registered"] = settings.get("vat_registered", True)
    payment_info = _payment_info_from_settings(settings)
    if payment_info:
        branding["payment_info"] = payment_info
    branding["template_choice"] = settings.get("template_choice") or "classic"
    # Depth pass (migration 103) — footer text, per-field visibility, logo size/align, font. All
    # default to today's fixed behavior when unset, so existing tenants render identically.
    branding["footer_text"] = settings.get("footer_text") or ""
    branding["show_vat_breakdown"] = settings.get("show_vat_breakdown", True)
    branding["show_company_reg"] = settings.get("show_company_reg", True)
    branding["logo_size"] = settings.get("logo_size") or "md"
    branding["logo_align"] = settings.get("logo_align") or "left"
    branding["font_pairing"] = settings.get("font_pairing") or ""
    # Brand Kit's ink_color already reaches the storefront (GET /{tenant}/brand) but never
    # reached the PDF renderer before this — body text stayed hardcoded per-theme regardless
    # of what a tenant picked. "" (unset) keeps each theme's existing hardcoded fallback.
    branding["ink_color"] = settings.get("ink_color") or ""
    return branding


def _font_ctx(branding: dict) -> dict:
    """Resolve a branding dict's font_pairing into the two Jinja values every template/letter
    needs: the CSS font-family stack, and (if the pairing needs a Google Font) the stylesheet URL
    to link in <head> — WeasyPrint fetches remote stylesheets/fonts the same way a browser does."""
    pairing = branding.get("font_pairing") or ""
    query = _FONT_IMPORTS.get(pairing)
    return {
        "font_family": _FONT_STACKS.get(pairing, _FONT_STACKS[""]),
        "font_import_url": f"https://fonts.googleapis.com/css2?family={query}&display=swap" if query else None,
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
            item["total_cents"] = int(round(float(item.get("quantity", 1) or 1) * int(item.get("unit_price_cents", 0))))

    doc_type = invoice.get("doc_type", "invoice")
    choice = branding.get("template_choice") or "classic"

    # Inbound documents (a supplier billing US, filed via Smart Scanner/email/WhatsApp intake) store
    # the tenant's own name in customer_name as a workaround — that column is NOT NULL and there's
    # no real "customer" on a bill we received (see commit_inbound_document). Rendered as-is via the
    # outbound letterhead/Bill-To mapping, that put the tenant's own name in BOTH the letterhead and
    # Bill To, and the actual supplier never appeared anywhere. For inbound, swap the mapping: the
    # supplier's name (the only supplier field actually captured — no address/email/vat exist for
    # inbound docs) becomes the "From" party in the letterhead position; the tenant's own full
    # branding (data we do reliably have) becomes the "Bill To" party.
    inbound = invoice.get("direction") == "inbound"
    if inbound:
        header_name, header_trading_as, header_logo = invoice.get("supplier") or "Unknown supplier", "", ""
        header_address = header_email = header_phone = header_reg = header_vat = ""
        bill_name = branding.get("name", tenant_id.replace("-", " ").title())
        bill_address = branding.get("address", "")
        bill_email = branding.get("email", "")
        bill_phone = branding.get("phone", "")
    else:
        header_name = branding.get("name", tenant_id.replace("-", " ").title())
        header_trading_as = branding.get("trading_as", "")
        header_logo = branding.get("logo_url", "")
        header_address = branding.get("address", "")
        header_email = branding.get("email", "")
        header_phone = branding.get("phone", "")
        header_reg = branding.get("reg") or ""
        header_vat = branding.get("vat") or ""
        bill_name = invoice.get("customer_name", "")
        bill_address = invoice.get("customer_address", "")
        bill_email = invoice.get("customer_email", "")
        bill_phone = invoice.get("customer_phone", "")

    ctx: dict[str, Any] = {
        # Tenant branding (outbound) / supplier identity (inbound) — see comment above
        "tenant_name": header_name,
        "trading_as": header_trading_as,
        "tenant_logo": header_logo,
        "tenant_address": header_address,
        "tenant_email": header_email,
        "tenant_phone": header_phone,
        "tenant_reg": header_reg,
        "tenant_vat": header_vat,
        "accent": branding.get("accent", "#1a7a4a"),
        "payment_info": branding.get("payment_info", ""),
        "pay_url": invoice.get("pay_url", ""),
        # Document meta — only a VAT-registered supplier may issue a "Tax Invoice".
        "doc_label": ("Invoice" if doc_type == "invoice" and branding.get("vat_registered") is False
                      else _DOC_LABELS.get(doc_type, "Document")),
        "invoice_number": invoice.get("invoice_number", ""),
        "status": invoice.get("status", "draft"),
        "issue_date": (invoice.get("issue_date") or "")[:10],
        "due_date": (invoice.get("due_date") or "")[:10] or None,
        "valid_until": (invoice.get("valid_until") or "")[:10] or None,
        # "Bill To" party — the customer (outbound) / the tenant itself (inbound)
        "customer_name": bill_name,
        "customer_email": bill_email,
        "customer_phone": bill_phone,
        "customer_address": bill_address,
        # Financials — always integer cents, never floats in storage
        "line_items": line_items,
        "subtotal_cents": int(invoice.get("subtotal_cents") or 0),
        "discount_cents": int(invoice.get("discount_cents") or 0),
        "vat_rate": float(invoice.get("vat_rate") or 15.0),
        "vat_cents": int(invoice.get("vat_cents") or 0),
        "total_cents": int(invoice.get("total_cents") or 0),
        "deposit_cents": int(invoice.get("deposit_cents") or 0),
        "notes": invoice.get("notes") or "",
        "vat_registered": branding.get("vat_registered", True),
        "cert_style": choice in _CERT_STYLE_TEMPLATES,
        # Branding depth (migration 103) — all default to today's fixed behavior when unset.
        "footer_text": branding.get("footer_text") or "",
        "show_vat_breakdown": branding.get("show_vat_breakdown", True),
        "show_company_reg": branding.get("show_company_reg", True),
        "logo_size": branding.get("logo_size") or "md",
        "logo_align": branding.get("logo_align") or "left",
        "ink_color": branding.get("ink_color") or "",
        **_font_ctx(branding),
    }

    # Pick the layout template; substitute its CSS before the single render pass
    # so the accent colour (and any other Jinja value in the CSS) resolves too.
    css = _TEMPLATE_CSS.get(choice, _TEMPLATE_CSS["classic"])
    template_html = _DOC_HTML.replace("__TEMPLATE_CSS__", css)

    env = Environment(autoescape=False)
    html_str = env.from_string(template_html).render(**ctx)
    pdf_bytes = WP_HTML(string=html_str).write_pdf()

    log.info(
        "PDF rendered: %s %s (%s) for %s (%d bytes)",
        doc_type, ctx["invoice_number"], choice, tenant_id, len(pdf_bytes),
    )
    return pdf_bytes


# ── Letters (fee proposals, appointment letters, tender invitations, ...) ──────
# Reuses the same tenant-branded header/footer + accent-colour theming as invoices, but with
# a plain body: a "To" block, an optional subject/reference line, and free-text paragraphs.

_LETTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
{% if font_import_url %}<link rel="stylesheet" href="{{ font_import_url }}">{% endif %}
<style>
__TEMPLATE_CSS__
.letter-date { text-align: right; font-size: 9.5pt; color: #666; margin-bottom: 22px; }
.letter-to { margin-bottom: 22px; font-size: 10pt; line-height: 1.6; }
.letter-subject { font-weight: 700; margin-bottom: 18px; font-size: 10.5pt; }
.letter-body { font-size: 10.5pt; line-height: 1.7; }
.letter-body p { margin-bottom: 12px; }
.letter-body h1, .letter-body h2, .letter-body h3 { color: {{ accent }}; margin: 18px 0 10px; }
.letter-body h1 { font-size: 14pt; } .letter-body h2 { font-size: 12.5pt; } .letter-body h3 { font-size: 11pt; }
.letter-body ul, .letter-body ol { margin: 0 0 12px 20px; }
.letter-body li { margin-bottom: 4px; }
.letter-body table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
.letter-body th, .letter-body td { padding: 6px 8px; font-size: 10pt; border-bottom: 1px solid #eee; text-align: left; }
.letter-sign { margin-top: 40px; font-size: 10pt; line-height: 1.7; }
</style>
</head>
<body>

<div class="header">
  <div class="brand">
    {% if tenant_logo %}<img src="{{ tenant_logo }}" alt="logo" style="max-height:76px;max-width:240px;margin-bottom:10px;display:block;">{% endif %}
    {% if trading_as %}
    <h1>{{ trading_as }}</h1>
    <p class="legal-name">{{ tenant_name }}</p>
    {% else %}
    <h1>{{ tenant_name }}</h1>
    {% endif %}
    <p>{{ tenant_address | replace("\\n", "<br>") | safe }}</p>
    {% if tenant_email %}<p>{{ tenant_email }}</p>{% endif %}
    {% if tenant_phone %}<p>{{ tenant_phone }}</p>{% endif %}
    {% if tenant_reg %}<p>Reg: {{ tenant_reg }}</p>{% endif %}
  </div>
  <div class="doc-title">
    <h2>{{ doc_label }}</h2>
  </div>
</div>

<div class="letter-date">{{ issue_date }}</div>

{% if recipient %}
<div class="letter-to">
  {{ recipient | replace("\\n", "<br>") | safe }}
</div>
{% endif %}

{% if subject %}<div class="letter-subject">Re: {{ subject }}</div>{% endif %}

<div class="letter-body">{% if body_html %}{{ body_html | safe }}{% else %}{% for para in body_paragraphs %}<p>{{ para }}</p>{% endfor %}{% endif %}</div>

{% if sign_off %}
<div class="letter-sign">{{ sign_off | replace("\\n", "<br>") | safe }}</div>
{% endif %}

<div class="footer">
  {{ tenant_name }} &mdash; {{ tenant_address | replace("\\n", " | ") | safe }}
</div>
</body>
</html>"""


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines into paragraphs; a lone newline stays within a paragraph."""
    import re as _re
    return _re.split(r"\n\s*\n", text or "")


def render_letter_pdf(
    *,
    tenant_id: str,
    body: str = "",
    body_markdown: Optional[str] = None,
    doc_label: str = "Letter",
    subject: Optional[str] = None,
    recipient: Optional[str] = None,
    sign_off: Optional[str] = None,
    issue_date: Optional[str] = None,
    tenant_profile: Optional[dict] = None,
) -> bytes:
    """Render letter content onto the tenant's branded letterhead.

    Args:
        body: plain-text content. Blank lines split it into paragraphs. Ignored if
            body_markdown is given.
        body_markdown: markdown content (e.g. straight from an LLM draft) — rendered to
            proper headings/lists/tables instead of literal '#'/'*' characters.
        doc_label: heading shown top-right, e.g. "Letter of Appointment", "Fee Proposal".
        recipient: "To" block (name/address), plain text, newlines preserved.
        sign_off: closing block, e.g. "Kind regards,\\nJudy Downing\\nDIGG Architects".
        tenant_profile: branding override — falls back to _TENANT_DEFAULTS keyed by tenant_id,
            same as render_invoice_pdf (use merge_branding() to source from saved invoice settings).
    """
    try:
        from datetime import datetime, timezone
        from jinja2 import Environment
        from weasyprint import HTML as WP_HTML
    except ImportError as exc:
        raise RuntimeError(
            "PDF rendering requires weasyprint and jinja2. Run: pip install weasyprint"
        ) from exc

    branding = tenant_profile or _TENANT_DEFAULTS.get(tenant_id, {})
    body_html = None
    paragraphs: list[str] = []
    if body_markdown:
        import markdown as _markdown
        body_html = _markdown.markdown(body_markdown, extensions=["tables"])
    else:
        paragraphs = [p.strip() for p in _split_paragraphs(body) if p.strip()]

    ctx: dict[str, Any] = {
        "tenant_name": branding.get("name", tenant_id.replace("-", " ").title()),
        "trading_as": branding.get("trading_as", ""),
        "tenant_logo": branding.get("logo_url", ""),
        "tenant_address": branding.get("address", ""),
        "tenant_email": branding.get("email", ""),
        "tenant_phone": branding.get("phone", ""),
        "tenant_reg": branding.get("reg") or "",
        "accent": branding.get("accent", "#1a7a4a"),
        "doc_label": doc_label,
        "issue_date": issue_date or datetime.now(timezone.utc).date().isoformat(),
        "recipient": recipient or "",
        "subject": subject or "",
        "body_html": body_html,
        "body_paragraphs": paragraphs,
        "sign_off": sign_off or "",
        "ink_color": branding.get("ink_color") or "",
        **_font_ctx(branding),
    }

    choice = branding.get("template_choice") or "classic"
    css = _TEMPLATE_CSS.get(choice, _TEMPLATE_CSS["classic"])
    template_html = _LETTER_HTML.replace("__TEMPLATE_CSS__", css)

    env = Environment(autoescape=False)
    html_str = env.from_string(template_html).render(**ctx)
    pdf_bytes = WP_HTML(string=html_str).write_pdf()

    log.info("Letter PDF rendered: %s (%s) for %s (%d bytes)", doc_label, choice, tenant_id, len(pdf_bytes))
    return pdf_bytes


def re_split_blank_lines(text: str) -> list[str]:
    """Split on blank lines into paragraphs; a lone newline stays within a paragraph."""
    import re as _re
    return _re.split(r"\n\s*\n", text or "")
