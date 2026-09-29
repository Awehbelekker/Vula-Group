/**
 * brandUpload.js — upload a brand image (logo, app icon) to the tenant's storage folder and
 * return its public URL. One helper for the Brand kit (Settings) and the Invoices wizard, which
 * used to carry two copies of the same upload code.
 */
import { supabase } from './supabase'

/** Trim the empty margin (transparent or near-white) around a raster logo, so a logo that was
 * exported with lots of padding isn't drawn tiny on every surface. SVGs are left as they are. */
export async function trimLogo(file) {
  if (!file || !/^image\/(png|jpeg|webp)$/.test(file.type)) return file
  try {
    const bmp = await createImageBitmap(file)
    const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height
    const g = c.getContext('2d'); g.drawImage(bmp, 0, 0)
    const { data, width, height } = g.getImageData(0, 0, c.width, c.height)
    const empty = (i) => data[i + 3] < 16 || (data[i] > 245 && data[i + 1] > 245 && data[i + 2] > 245)
    let top = height, left = width, right = -1, bottom = -1
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      if (!empty((y * width + x) * 4)) { if (y < top) top = y; if (y > bottom) bottom = y; if (x < left) left = x; if (x > right) right = x }
    }
    if (right < 0) return file                                  // blank image: leave it
    const pad = Math.round(Math.max(right - left, bottom - top) * 0.04)
    const x0 = Math.max(0, left - pad), y0 = Math.max(0, top - pad)
    const w = Math.min(width, right + pad + 1) - x0, h = Math.min(height, bottom + pad + 1) - y0
    if (w >= width * 0.97 && h >= height * 0.97) return file    // nothing worth trimming
    const out = document.createElement('canvas'); out.width = w; out.height = h
    out.getContext('2d').drawImage(c, x0, y0, w, h, 0, 0, w, h)
    const blob = await new Promise((res) => out.toBlob(res, 'image/png'))
    return blob ? new File([blob], file.name.replace(/\.\w+$/, '') + '.png', { type: 'image/png' }) : file
  } catch { return file }
}

export async function uploadBrandImage(tenantId, file, kind = 'logo') {
  if (!file || !tenantId) return null
  if (kind === 'logo') file = await trimLogo(file)
  const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
  const path = `${tenantId}/${kind}/${Date.now()}-${clean}`
  const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
  if (error) return null
  const { data } = supabase.storage.from('product-images').getPublicUrl(path)
  return data?.publicUrl || null
}
