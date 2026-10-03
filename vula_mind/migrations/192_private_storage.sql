-- 192: tenant files are private (2026-10-03, Ian: "prepare the private storage PR").
-- The documents (filed bank statements, invoices, receipts, IDs), evidence (site photos) and
-- signatures buckets were public: anyone holding a link — forwarded, logged, cached — could open
-- the file. POPIA s19 wants personal information protected against unauthorised access.
-- The API now signs every link it hands out (vula/storage_links.py) and reads files through the
-- Storage API with the service key, so these buckets can be private. product-images stays
-- public (storefront pictures and logos are meant to be seen).
--
-- APPLY ONLY AFTER the code from the same PR is deployed — before that, the dashboard and PDFs
-- still open the old public links and would show broken images. Idempotent.

update storage.buckets set public = false where id in ('documents', 'evidence', 'signatures');

-- anyone (anon included) could read signatures through the API; signed links replace this
drop policy if exists "signatures: public read" on storage.objects;
