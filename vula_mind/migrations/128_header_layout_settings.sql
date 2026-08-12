-- 128_header_layout_settings.sql — storefront header configurability.
-- logo_align/logo_size (migration 103) already exist and are reused as-is for the storefront
-- header's logo position/size — same tenant-editable fields now drive two surfaces (invoice
-- PDFs + the storefront header). Only genuinely new, storefront-specific concepts get new
-- columns here. No new column for the utility bar — it's derived live from the tenant's
-- existing WhatsApp number, not a separate setting.

alter table commerce_invoice_settings
    add column if not exists header_sticky boolean default true,        -- sticky-on-scroll vs static
    add column if not exists header_nav_position text default 'right',  -- right | center | below-logo
    add column if not exists header_cta_text text,                      -- e.g. "Get a quote" — blank = no CTA button
    add column if not exists header_cta_link text;
