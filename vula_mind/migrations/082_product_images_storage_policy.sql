-- 082_product_images_storage_policy.sql — RLS policies for the product-images Storage bucket.
--
-- Found live 2026-07-19: the bucket itself didn't exist at all (created via the Storage API,
-- not SQL — nothing to migrate there), but Supabase Storage denies every write by default until
-- an explicit RLS policy on storage.objects allows it. Every product-photo/logo/Puck-image
-- upload in the dashboard (VulaImageUpload, VulaSettings brand kit, VulaInvoices logo) has been
-- silently failing with "new row violates row-level security policy" this whole time — this is
-- the first client-side (anon-key + user session) Storage write in the app, so there was no
-- existing policy to copy from (documents/evidence buckets are backend-only, service-role key,
-- which bypasses RLS entirely).

create policy "product-images: authenticated write"
on storage.objects for insert
to authenticated
with check (bucket_id = 'product-images');

create policy "product-images: authenticated update"
on storage.objects for update
to authenticated
using (bucket_id = 'product-images');

create policy "product-images: authenticated delete"
on storage.objects for delete
to authenticated
using (bucket_id = 'product-images');

-- Public read — product photos/logos/Puck images must be visible on public storefront pages
-- with no login. (The bucket's own "public" flag is already set; this policy makes object
-- listing/metadata reads consistent too.)
create policy "product-images: public read"
on storage.objects for select
to public
using (bucket_id = 'product-images');
