-- 301: a photo per variant (24 Nov 2026, Products › Sizes and colours).
-- A colour's photo is set on every variant of that colour (service.set_option_photo); the shop
-- chat shows it when that variant is added to the basket, the storefront gets it with the
-- variants. https only, checked in code. Null = the product's own photo.
alter table commerce_product_variants add column if not exists image_url text;
