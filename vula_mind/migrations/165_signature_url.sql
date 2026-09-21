-- 165_signature_url.sql — a tenant's captured signature image (WhatsApp photo capture, see
-- vula/api/whatsapp.py's _handle_signature_capture) and the printed name/title shown under it,
-- rendered onto generated letters/documents (vula/commerce/pdf.py's render_letter_pdf and
-- render_letter_docx) alongside the plain-text sign_off. No e-signature system existed
-- anywhere in the codebase before this — see the 2026-09-18 session notes.

alter table commerce_invoice_settings
    add column if not exists signature_url text,
    add column if not exists signature_name text;
