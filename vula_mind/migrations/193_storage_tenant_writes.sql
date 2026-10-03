-- 193: dashboard uploads only into your own tenant's folder (2026-10-03, Ian).
-- The product-images and signatures write/update/delete policies allowed ANY signed-in dashboard
-- user to write, overwrite or delete ANY tenant's files (e.g. replace another business's logo or
-- signature). Every dashboard upload path starts with the tenant id
-- ("{tenantId}/signature/...", "{tenantId}/logo/..."), and vula_tenant_users maps each Supabase
-- Auth user to their tenant(s) — so writes are now limited to folders of the user's own tenants;
-- a master user may write anywhere. The API's own uploads use the service key and are unaffected.
-- Reads are unchanged (product-images public; signatures private per migration 192). Idempotent.

create or replace function public.vula_storage_can_write(object_name text)
returns boolean
language sql stable security definer set search_path = public
as $$
  select exists (
    select 1 from vula_tenant_users u
    where u.user_id = auth.uid()
      and (u.role = 'master' or u.tenant_id = (storage.foldername(object_name))[1])
  )
$$;
revoke all on function public.vula_storage_can_write(text) from public, anon;
grant execute on function public.vula_storage_can_write(text) to authenticated;

do $$
declare b text;
begin
  foreach b in array array['product-images', 'signatures'] loop
    execute format('drop policy if exists %I on storage.objects', b || ': authenticated write');
    execute format('drop policy if exists %I on storage.objects', b || ': authenticated update');
    execute format('drop policy if exists %I on storage.objects', b || ': authenticated delete');
    execute format($p$create policy %I on storage.objects for insert to authenticated
                      with check (bucket_id = %L and public.vula_storage_can_write(name))$p$,
                   b || ': tenant write', b);
    execute format($p$create policy %I on storage.objects for update to authenticated
                      using (bucket_id = %L and public.vula_storage_can_write(name))
                      with check (bucket_id = %L and public.vula_storage_can_write(name))$p$,
                   b || ': tenant update', b, b);
    execute format($p$create policy %I on storage.objects for delete to authenticated
                      using (bucket_id = %L and public.vula_storage_can_write(name))$p$,
                   b || ': tenant delete', b);
  end loop;
end $$;

-- the dashboard uploads with upsert, which Storage only allows with SELECT too; since 192 made
-- signatures private, a user may read their OWN tenant's signatures (anon still can't)
drop policy if exists "signatures: tenant read" on storage.objects;
create policy "signatures: tenant read" on storage.objects for select to authenticated
  using (bucket_id = 'signatures' and public.vula_storage_can_write(name));
