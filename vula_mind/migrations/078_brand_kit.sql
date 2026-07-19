-- 078_brand_kit.sql — deepen the native brand kit (logo + accent alone wasn't enough to feel
-- "owned"): a real ink/text colour and a curated font pairing, both flowing through to the
-- storefront as well as the dashboard.

alter table commerce_invoice_settings
    add column if not exists ink_color text,       -- heading/body text colour, hex (e.g. #1E1E1E)
    add column if not exists font_pairing text;     -- key into FONT_PAIRINGS (dashboard + puck.config.jsx)
