-- Vula Group — Migration 065: rich media (image header + buttons) on WhatsApp templates
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================================
alter table commerce_wa_templates
    add column if not exists header_type        text,   -- NULL | IMAGE | VIDEO | DOCUMENT
    add column if not exists header_example_url  text,   -- sample media Meta reviewed the template with
    add column if not exists buttons             jsonb;  -- [{"type":"URL"|"QUICK_REPLY","text":str,"url":str|null}]
