-- 158_business_hours.sql — per-tenant opening hours on commerce_order_settings.
-- Pre-go-live brief, 2026-09-11: unlike delivery coverage (070_delivery_settings.sql), there
-- was no hard-facts block for "are you open?" — every such question routed to ask_team,
-- burning staff time on a fact that should be configured once. Same pattern: injected into the
-- commerce assistant's system prompt as hard facts, with a deterministic check_business_hours
-- tool (vula/commerce/hours.py) instead of the model guessing or computing SAST time itself.
-- Editable via the existing GET/PUT /{tenant}/admin/order-settings endpoints (whitelisted in
-- order_workflow._FIELDS).

alter table commerce_order_settings
    -- {"mon": {"open": "08:00", "close": "17:00"}, ..., "sun": null} — a day mapped to null (or
    -- simply omitted) means closed that day. NULL (the whole column) = not configured: the
    -- assistant is told it does NOT know and must check with the team, same convention as
    -- delivery_areas.
    add column if not exists business_hours jsonb,
    -- Freeform caveat shown alongside the hours, e.g. "Closed on public holidays."
    add column if not exists business_hours_note text,
    -- Sent verbatim (with the actual reopening time appended) when a customer messages outside
    -- configured hours. NULL = a sensible built-in default is used instead.
    add column if not exists after_hours_message text;
