-- 103_invoice_branding_depth.sql — invoice branding depth pass: a persistent footer/terms note
-- (previously only a per-invoice notes field existed, nothing tenant-level), per-field visibility
-- toggles for VAT breakdown / company reg number, and real per-tenant control over logo size and
-- alignment instead of one fixed size baked into every template.

alter table commerce_invoice_settings
    add column if not exists footer_text text,                            -- overrides the hardcoded "Thank you for your business."
    add column if not exists show_vat_breakdown boolean default true,     -- hide the VAT line(s) entirely if false
    add column if not exists show_company_reg boolean default true,       -- hide the "Reg: ..." line if false
    add column if not exists logo_size text default 'md',                 -- sm | md | lg
    add column if not exists logo_align text default 'left';              -- left | center
