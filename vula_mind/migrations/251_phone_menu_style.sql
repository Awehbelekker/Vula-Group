-- 251_phone_menu_style.sql — the look of a business's phone menu (the bar at the bottom of the
-- dashboard on a phone). 2026-11-08, Ian: "build it with those three styles" (from his Glass Nav
-- Kit): 'classic' (the dark bar, default), 'glass' (frosted bar, a glass pill under the current
-- page) or 'float' (solid bar with the Add button raised in a notch). The colour always comes from
-- the brand kit's accent; the buttons from the person's role. Idempotent; no new tables.

alter table commerce_invoice_settings
    add column if not exists nav_style text;    -- 'classic' | 'glass' | 'float'; null = classic
