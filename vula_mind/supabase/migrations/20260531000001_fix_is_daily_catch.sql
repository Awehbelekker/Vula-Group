-- Rename is_weekly_special -> is_daily_catch to match service + frontend naming
ALTER TABLE commerce_products
  RENAME COLUMN is_weekly_special TO is_daily_catch;
