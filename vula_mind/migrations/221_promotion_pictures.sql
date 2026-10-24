-- 221_promotion_pictures.sql — keep the picture of a forwarded promotion (24 Oct 2026).
--
-- Off the Hook's website shows AI-made pictures for 44 of 45 products, and some don't look like
-- OTH's real fish. Staci's own posts have real photos. A forwarded post's picture is now stored
-- (product-images/{tenant}/promotions/…) and can be offered as a product's website picture —
-- used only after the team taps "Use photo". A post may now be a picture with no words.
-- Idempotent; RLS on vula_promotions is unchanged (on, no policies).

alter table vula_promotions add column if not exists image_url text;
