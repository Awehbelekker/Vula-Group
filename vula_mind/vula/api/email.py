"""
vula/api/email.py

Transactional email via Resend (resend.com).
Falls back gracefully when RESEND_API_KEY is not configured.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


async def _send(
    to: str,
    subject: str,
    html: str,
    text: str,
    attachments: Optional[list[dict]] = None,
) -> bool:
    if not settings.resend_api_key:
        logger.info("Resend not configured — skipping email to %s", to)
        return False
    try:
        payload: dict = {
            "from": settings.from_email,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if attachments:
            payload["attachments"] = attachments
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _RESEND_URL,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            logger.info("Email sent to %s (id=%s)", to, resp.json().get("id"))
            return True
    except Exception as exc:
        logger.error("Email send failed to %s: %s", to, exc)
        return False


_HEADER = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F7F4EE;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#F7F4EE;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <tr><td style="background:#2C5545;padding:32px 40px;">
          <p style="margin:0;color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.3px;">Vula Group</p>
          <p style="margin:4px 0 0;color:#8FBF9F;font-size:13px;">Your AI is ready.</p>
        </td></tr>
        <tr><td style="padding:40px;">
          <table width="100%" cellpadding="0" cellspacing="0">"""

_FOOTER = """          </table>
        </td></tr>
        <tr><td style="background:#F7F4EE;padding:24px 40px;border-top:1px solid #DDD8CE;">
          <p style="margin:0;font-size:12px;color:#8A8680;line-height:1.6;">
            Questions? Reply to this email or WhatsApp us — we reply fast.<br>
            Vula Group (Pty) Ltd · Cape Town, South Africa
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _welcome_html(
    first_name: str,
    company_name: str,
    workspace_url: str,
    temp_password: str,
    plan: str,
    trial_ends: str,
    payment_url: Optional[str],
) -> str:
    pay_block = ""
    if payment_url:
        pay_block = (
            '<tr><td style="padding:0 0 24px">'
            '<a href="' + payment_url + '" '
            'style="display:inline-block;background:#D4A017;color:#fff;font-weight:600;'
            'font-size:14px;padding:14px 28px;border-radius:8px;text-decoration:none;">'
            "Complete Your Subscription via PayFast →"
            "</a>"
            '<p style="color:#8A8680;font-size:12px;margin:8px 0 0">Secure payment. Cancel any time.</p>'
            "</td></tr>"
        )

    steps_html = ""
    for n, step in [
        (1, "Upload your price lists, proposals, or SOPs to your workspace"),
        (2, "Your AI processes them within 2–4 hours"),
        (3, "One of our team will call you tomorrow for your personalised demo"),
        (4, "Ask your AI anything about your business — it knows your documents"),
    ]:
        steps_html += (
            '<tr><td style="padding:6px 0;font-size:13px;color:#5A5A5A;line-height:1.5;">'
            '<span style="color:#2C5545;font-weight:700;">' + str(n) + ".</span> " + step
            + "</td></tr>"
        )

    body = (
        '<tr><td style="padding:0 0 20px">'
        '<p style="margin:0;font-size:20px;font-weight:700;color:#2A2A2A;">Welcome, ' + first_name + "!</p>"
        '<p style="margin:8px 0 0;font-size:14px;color:#5A5A5A;line-height:1.6;">'
        "Your Vula workspace for <strong>" + company_name + "</strong> is live. "
        "Your AI assistant is ready to learn from your documents."
        "</p></td></tr>"
        '<tr><td style="padding:0 0 24px">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#F7F4EE;border-radius:8px;">'
        '<tr><td style="padding:20px 24px;">'
        '<p style="margin:0 0 12px;font-size:11px;font-weight:700;color:#8A8680;'
        'text-transform:uppercase;letter-spacing:0.5px;">Login Details</p>'
        '<p style="margin:0 0 6px;font-size:13px;color:#2A2A2A;"><strong>Workspace:</strong> '
        '<a href="' + workspace_url + '" style="color:#2C5545;">' + workspace_url + "</a></p>"
        '<p style="margin:0 0 6px;font-size:13px;color:#2A2A2A;"><strong>Email:</strong> Your email address</p>'
        '<p style="margin:0;font-size:13px;color:#2A2A2A;"><strong>Temp password:</strong> '
        '<code style="background:#E8E4DE;padding:2px 8px;border-radius:4px;font-size:13px;">'
        + temp_password + "</code></p>"
        "</td></tr></table></td></tr>"
        '<tr><td style="padding:0 0 24px">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #DDD8CE;border-radius:8px;">'
        "<tr>"
        '<td style="padding:16px 24px;border-right:1px solid #DDD8CE;">'
        '<p style="margin:0;font-size:11px;color:#8A8680;text-transform:uppercase;'
        'letter-spacing:0.5px;font-weight:700;">Plan</p>'
        '<p style="margin:4px 0 0;font-size:15px;font-weight:600;color:#2A2A2A;text-transform:capitalize;">'
        + plan + "</p></td>"
        '<td style="padding:16px 24px;">'
        '<p style="margin:0;font-size:11px;color:#8A8680;text-transform:uppercase;'
        'letter-spacing:0.5px;font-weight:700;">Trial ends</p>'
        '<p style="margin:4px 0 0;font-size:15px;font-weight:600;color:#2A2A2A;">' + trial_ends + "</p>"
        "</td></tr></table></td></tr>"
        + pay_block
        + '<tr><td style="padding:0 0 24px">'
        '<p style="margin:0 0 12px;font-size:13px;font-weight:700;color:#2A2A2A;">What happens next</p>'
        '<table width="100%" cellpadding="0" cellspacing="0">' + steps_html + "</table></td></tr>"
        '<tr><td style="padding:0 0 8px">'
        '<a href="' + workspace_url + '" '
        'style="display:inline-block;background:#2C5545;color:#fff;font-weight:600;'
        'font-size:14px;padding:14px 28px;border-radius:8px;text-decoration:none;">'
        "Open Your Workspace →</a></td></tr>"
    )

    return _HEADER + body + _FOOTER


def _welcome_text(
    first_name: str,
    company_name: str,
    workspace_url: str,
    temp_password: str,
    plan: str,
    trial_ends: str,
    payment_url: Optional[str],
) -> str:
    lines = [
        f"Hi {first_name},",
        "",
        f"Your Vula workspace for {company_name} is ready.",
        "",
        f"Workspace: {workspace_url}",
        f"Temp password: {temp_password}",
        f"Plan: {plan}  |  Trial ends: {trial_ends}",
        "",
    ]
    if payment_url:
        lines += [f"Complete your subscription: {payment_url}", ""]
    lines += [
        "What happens next:",
        "1. Upload your documents (price lists, SOPs, proposals)",
        "2. Your AI processes them within 2-4 hours",
        "3. One of our team will call you tomorrow for your demo",
        "",
        "Reply to this email with any questions.",
        "",
        "Vula Group",
    ]
    return "\n".join(lines)


def _team_alert_html(
    company_name: str,
    contact_name: str,
    email: str,
    whatsapp: Optional[str],
    plan: str,
    industry: str,
    pain_points: list[str],
    workspace_url: str,
) -> str:
    badge_colour = {"starter": "#6B7280", "growth": "#2C5545", "business": "#D4A017"}.get(plan, "#6B7280")
    rows_html = ""
    for label, value in [
        ("Company", company_name),
        ("Contact", contact_name),
        ("Email", email),
        ("WhatsApp", whatsapp or "Not provided"),
        ("Industry", industry),
        ("Workspace", workspace_url),
    ]:
        rows_html += (
            "<tr>"
            '<td style="padding:6px 0;font-size:12px;color:#8A8680;'
            'text-transform:uppercase;letter-spacing:0.4px;width:120px;">' + label + "</td>"
            '<td style="padding:6px 0;font-size:13px;color:#2A2A2A;font-weight:500;">' + value + "</td>"
            "</tr>"
        )

    pain_html = "".join(
        '<li style="margin:4px 0;font-size:13px;color:#5A5A5A;">' + p + "</li>"
        for p in (pain_points[:3] or ["None selected"])
    )

    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\"></head>"
        '<body style="margin:0;padding:0;background:#F7F4EE;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;\">"
        '<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 20px;">'
        '<tr><td align="center">'
        '<table width="560" cellpadding="0" cellspacing="0" '
        'style="background:#fff;border-radius:10px;overflow:hidden;'
        'box-shadow:0 1px 4px rgba(0,0,0,.08);">'
        '<tr><td style="background:#2C5545;padding:20px 32px;">'
        '<p style="margin:0;color:#fff;font-size:16px;font-weight:700;">New Vula Signup</p>'
        "</td></tr>"
        '<tr><td style="padding:28px 32px;">'
        '<span style="display:inline-block;background:' + badge_colour + ';color:#fff;font-size:11px;'
        'font-weight:700;padding:3px 10px;border-radius:20px;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:20px;">' + plan + "</span>"
        '<table width="100%" cellpadding="0" cellspacing="0">' + rows_html + "</table>"
        '<p style="margin:20px 0 8px;font-size:12px;color:#8A8680;'
        'text-transform:uppercase;letter-spacing:0.4px;">Pain points</p>'
        '<ul style="margin:0;padding-left:20px;">' + pain_html + "</ul>"
        '<p style="margin:24px 0 0;font-size:13px;color:#D4A017;font-weight:600;">'
        "Action: Call " + contact_name + " within 24 hours.</p>"
        "</td></tr></table></td></tr></table></body></html>"
    )


async def send_welcome_email(
    to: str,
    first_name: str,
    company_name: str,
    workspace_url: str,
    temp_password: str,
    plan: str,
    trial_ends: str,
    payment_url: Optional[str] = None,
) -> bool:
    subject = f"Your Vula workspace is ready, {first_name}"
    html = _welcome_html(first_name, company_name, workspace_url, temp_password, plan, trial_ends, payment_url)
    text = _welcome_text(first_name, company_name, workspace_url, temp_password, plan, trial_ends, payment_url)
    return await _send(to, subject, html, text)


async def send_payment_receipt_email(
    to: str,
    first_name: str,
    company_name: str,
    amount: str,
    plan: str,
    ref: str,
) -> bool:
    subject = f"Payment confirmed — Vula {plan.title()} subscription"
    html = (
        _HEADER
        + '<tr><td style="padding:0 0 20px">'
        + f'<p style="margin:0;font-size:20px;font-weight:700;color:#2A2A2A;">Payment received, {first_name}!</p>'
        + '<p style="margin:8px 0 0;font-size:14px;color:#5A5A5A;line-height:1.6;">'
        + f"Your Vula <strong>{plan.title()}</strong> subscription for <strong>{company_name}</strong> is now active."
        + "</p></td></tr>"
        + '<tr><td style="padding:0 0 24px">'
        + '<table width="100%" cellpadding="0" cellspacing="0" style="background:#F7F4EE;border-radius:8px;">'
        + '<tr><td style="padding:20px 24px;">'
        + '<p style="margin:0 0 8px;font-size:11px;font-weight:700;color:#8A8680;text-transform:uppercase;letter-spacing:0.5px;">Payment summary</p>'
        + f'<p style="margin:0 0 6px;font-size:13px;color:#2A2A2A;"><strong>Amount:</strong> R{float(amount):.2f}</p>'
        + f'<p style="margin:0 0 6px;font-size:13px;color:#2A2A2A;"><strong>Plan:</strong> {plan.title()}</p>'
        + f'<p style="margin:0;font-size:13px;color:#2A2A2A;"><strong>Reference:</strong> <code style="background:#E8E4DE;padding:2px 6px;border-radius:3px;">{ref}</code></p>'
        + "</td></tr></table></td></tr>"
        + '<tr><td><p style="font-size:13px;color:#5A5A5A;line-height:1.6;">Thank you for choosing Vula. Your subscription renews monthly. To manage your subscription, reply to this email.</p></td></tr>'
        + _FOOTER
    )
    text = (
        f"Hi {first_name},\n\nPayment confirmed for {company_name}.\n\n"
        f"Amount: R{float(amount):.2f}\nPlan: {plan.title()}\nReference: {ref}\n\n"
        "Thank you for choosing Vula.\n\nVula Group"
    )
    return await _send(to, subject, html, text)


async def send_trial_expiry_email(
    to: str,
    first_name: str,
    company_name: str,
    days_left: int,
    payment_url: Optional[str] = None,
) -> bool:
    urgent = days_left <= 2
    day_word = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
    subject = f"Your Vula trial expires {day_word}, {first_name}"
    cta = (
        '<tr><td style="padding:0 0 20px">'
        + (
            f'<a href="{payment_url}" style="display:inline-block;background:#D4A017;color:#fff;font-weight:600;font-size:14px;padding:14px 28px;border-radius:8px;text-decoration:none;">Keep Your Vula Access →</a>'
            if payment_url
            else '<p style="font-size:13px;color:#5A5A5A;">Reply to this email to set up your subscription.</p>'
        )
        + "</td></tr>"
    )
    urgency_color = "#C0392B" if urgent else "#D4A017"
    urgency_label = "TODAY" if days_left == 0 else f"IN {days_left} DAY{'S' if days_left != 1 else ''}"
    html = (
        _HEADER
        + '<tr><td style="padding:0 0 16px">'
        + f'<p style="margin:0;font-size:20px;font-weight:700;color:#2A2A2A;">Your trial ends {urgency_label.lower()}, {first_name}</p>'
        + "</td></tr>"
        + '<tr><td style="padding:0 0 20px">'
        + f'<div style="background:{urgency_color}15;border-left:3px solid {urgency_color};padding:14px 18px;border-radius:4px;">'
        + f'<p style="margin:0;font-size:13px;color:{urgency_color};font-weight:600;">Trial expires {urgency_label}</p>'
        + f'<p style="margin:6px 0 0;font-size:13px;color:#5A5A5A;">Your Vula workspace for <strong>{company_name}</strong> will be suspended. Subscribe now to keep your AI and all your uploaded documents.</p>'
        + "</div></td></tr>"
        + cta
        + _FOOTER
    )
    text = (
        f"Hi {first_name},\n\nYour Vula trial for {company_name} expires {urgency_label.lower()}.\n\n"
        + (f"Subscribe: {payment_url}\n\n" if payment_url else "")
        + "Reply to this email if you have questions.\n\nVula Group"
    )
    return await _send(to, subject, html, text)


async def send_team_alert_email(
    company_name: str,
    contact_name: str,
    email: str,
    whatsapp: Optional[str],
    plan: str,
    industry: str,
    pain_points: list[str],
    workspace_url: str,
) -> bool:
    if not settings.team_email:
        return False
    subject = f"New Vula Signup — {company_name} ({plan})"
    html = _team_alert_html(company_name, contact_name, email, whatsapp, plan, industry, pain_points, workspace_url)
    text = (
        f"New signup\n\n"
        f"Company: {company_name}\nContact: {contact_name}\nEmail: {email}\n"
        f"WhatsApp: {whatsapp or 'Not provided'}\nPlan: {plan}\nIndustry: {industry}\n"
        f"Pain points: {', '.join(pain_points[:3]) if pain_points else 'None'}\n"
        f"Workspace: {workspace_url}\n\n"
        f"Action: Call {contact_name} within 24 hours."
    )
    return await _send(settings.team_email, subject, html, text)


_INVOICE_DOC_LABELS = {
    "invoice": "Tax Invoice",
    "quote": "Quotation",
    "proforma": "Pro Forma Invoice",
}


async def send_invoice_email(
    to: str,
    invoice: dict,
    pdf_bytes: bytes,
    tenant_name: str = "Vula Group",
    approval_link: str = "",
) -> bool:
    """Email an invoice/quote PDF to a customer as an attachment.

    Args:
        to: Recipient email address.
        invoice: The invoice row (for number, totals, customer name).
        pdf_bytes: Rendered PDF bytes (from render_invoice_pdf).
        tenant_name: Display name of the merchant, used in copy.
        approval_link: When set (opt-in per invoice, migration 136), a public link the
            recipient can click to approve the invoice — appended as its own call-to-action.

    Returns:
        True if the email was dispatched, False otherwise.
    """
    doc_type = invoice.get("doc_type", "invoice")
    doc_label = _INVOICE_DOC_LABELS.get(doc_type, "Document")
    number = invoice.get("invoice_number", "")
    customer = invoice.get("customer_name", "there")
    total = f"R{(int(invoice.get('total_cents') or 0) / 100):.2f}"
    due_date = (invoice.get("due_date") or "")[:10]
    valid_until = (invoice.get("valid_until") or "")[:10]

    subject = f"{tenant_name} — {doc_label} {number}"
    when = (
        f"It is due on {due_date}." if (doc_type == "invoice" and due_date)
        else (f"It is valid until {valid_until}." if valid_until else "")
    )
    body_line = (
        f"Please find attached your {doc_label.lower()} <strong>{number}</strong> "
        f"for <strong>{total}</strong>. {when}"
    )
    approval_html = (
        '<tr><td style="padding:0 0 16px">'
        f'<a href="{approval_link}" style="display:inline-block;padding:10px 20px;'
        'background:#2C5545;color:#fff;border-radius:6px;text-decoration:none;'
        'font-size:14px;font-weight:600;">Review &amp; approve this invoice</a>'
        "</td></tr>"
    ) if approval_link else ""
    approval_text = f"\n\nReview and approve this invoice: {approval_link}" if approval_link else ""

    html = (
        _HEADER
        + '<tr><td style="padding:0 0 16px">'
        + f'<p style="margin:0;font-size:20px;font-weight:700;color:#2A2A2A;">Hi {customer},</p>'
        + '<p style="margin:8px 0 0;font-size:14px;color:#5A5A5A;line-height:1.6;">'
        + body_line
        + "</p></td></tr>"
        + approval_html
        + '<tr><td style="padding:0 0 8px">'
        + '<p style="font-size:13px;color:#5A5A5A;line-height:1.6;">'
        + f"Thank you for your business.<br>{tenant_name}"
        + "</p></td></tr>"
        + _FOOTER
    )
    text = (
        f"Hi {customer},\n\n"
        f"Please find attached your {doc_label.lower()} {number} for {total}. "
        f"{when}{approval_text}\n\nThank you for your business.\n{tenant_name}"
    )

    attachments = [{
        "filename": f"{number or doc_type}.pdf",
        "content": base64.b64encode(pdf_bytes).decode("ascii"),
    }]
    return await _send(to, subject, html, text, attachments=attachments)
