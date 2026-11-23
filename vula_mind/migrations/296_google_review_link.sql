-- 296: the business's Google review link (23 Nov 2026, Sell › Reviews).
-- After a customer rates an order 4 or 5 stars on WhatsApp, Vula's thank-you also carries this
-- link (vula/commerce/reviews.py). Checked in code before it is kept: https on a Google host only.
alter table vula_tenant_config add column if not exists google_review_url text;
