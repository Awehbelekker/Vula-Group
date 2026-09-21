-- 168_signatures_storage_bucket.sql — pre-create the 'signatures' Supabase Storage bucket.
--
-- The WhatsApp signature-capture flow (whatsapp.py's _handle_signature_capture, migration 165)
-- already auto-creates this bucket server-side on first use via the service-role client
-- (_upload_to_storage's create_bucket call). But the new dashboard signature upload
-- (VulaInvoices.jsx's uploadSignature, go-live readiness pass Phase 3.1) uploads directly from
-- the browser with an authenticated (not service-role) Supabase client, which cannot create a
-- bucket — confirmed live: as of this migration, no tenant has yet used the WhatsApp flow, so
-- the bucket does not exist, and every dashboard upload would fail until someone happened to
-- use WhatsApp first. Pre-creating it here removes that ordering dependency.
--
-- Policies mirror the existing 'product-images' bucket exactly (confirmed via
-- pg_policies before writing this): authenticated write/update/delete, public read.

insert into storage.buckets (id, name, public)
values ('signatures', 'signatures', true)
on conflict (id) do nothing;

create policy "signatures: authenticated write" on storage.objects
    for insert to authenticated
    with check (bucket_id = 'signatures');

create policy "signatures: authenticated update" on storage.objects
    for update to authenticated
    using (bucket_id = 'signatures');

create policy "signatures: authenticated delete" on storage.objects
    for delete to authenticated
    using (bucket_id = 'signatures');

create policy "signatures: public read" on storage.objects
    for select to public
    using (bucket_id = 'signatures');
