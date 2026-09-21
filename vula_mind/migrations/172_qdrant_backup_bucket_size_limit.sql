-- 172_qdrant_backup_bucket_size_limit.sql — raise the qdrant-backups bucket's per-object size
-- cap above Supabase's project-wide default.
--
-- Confirmed live 2026-09-21: the first real _qdrant_backup_loop run (migration 171) backed up
-- 6 of 7 tenant Qdrant collections successfully, but digg-demo's snapshot — the heaviest-used
-- demo tenant, with the largest ingested document set — was rejected by Supabase Storage with
-- "413 Payload too large — The object exceeded the maximum allowed size". storage.buckets.
-- file_size_limit was null (inherits the project default, sized for things like signature/
-- product images, not full per-tenant vector-DB snapshots). Same column product-images already
-- sets per-bucket (10485760 bytes there); this bucket needs a much larger cap for its actual
-- content type.

update storage.buckets set file_size_limit = 524288000  -- 500MB
where id = 'qdrant-backups';
