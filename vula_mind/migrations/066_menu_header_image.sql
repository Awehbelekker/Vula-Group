-- 066_menu_header_image.sql — optional hero image shown above the WhatsApp product/welcome menu.
-- WhatsApp's interactive "list" message only supports a text header (Meta platform limit), so the
-- image is sent as its own message immediately before the list to give a rich-menu look.

alter table commerce_invoice_settings
    add column if not exists menu_header_image_url text;
