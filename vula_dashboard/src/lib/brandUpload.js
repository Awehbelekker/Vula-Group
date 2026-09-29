/**
 * brandUpload.js — upload a brand image (logo, app icon) to the tenant's storage folder and
 * return its public URL. One helper for the Brand kit (Settings) and the Invoices wizard, which
 * used to carry two copies of the same upload code.
 */
import { supabase } from './supabase'

export async function uploadBrandImage(tenantId, file, kind = 'logo') {
  if (!file || !tenantId) return null
  const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
  const path = `${tenantId}/${kind}/${Date.now()}-${clean}`
  const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
  if (error) return null
  const { data } = supabase.storage.from('product-images').getPublicUrl(path)
  return data?.publicUrl || null
}
