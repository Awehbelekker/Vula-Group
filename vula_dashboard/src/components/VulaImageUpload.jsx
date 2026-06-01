/**
 * VulaImageUpload.jsx
 *
 * Upload product images to Supabase Storage bucket 'product-images'.
 * Adapted from Awake SA ImageUpload.tsx — simplified for Vula Dashboard (React JSX).
 *
 * Storage path: product-images/{tenant_id}/products/{timestamp}-{filename}
 * Returns public URLs via onUploaded callback.
 */

import { useState, useRef } from 'react'
import { supabase } from '../lib/supabase'

export default function VulaImageUpload({ tenantId, onUploaded, maxFiles = 5, existingUrls = [] }) {
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [previews, setPreviews] = useState(existingUrls)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  async function handleFiles(e) {
    const files = Array.from(e.target.files || [])
    if (!files.length) return

    // Validate
    const validTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
    const invalid = files.filter(f => !validTypes.includes(f.type))
    if (invalid.length) { setError('Only JPG, PNG, WEBP allowed'); return }

    const oversize = files.filter(f => f.size > 10 * 1024 * 1024)
    if (oversize.length) { setError('Images must be under 10MB'); return }

    if (previews.length + files.length > maxFiles) {
      setError(`Maximum ${maxFiles} images`); return
    }

    setError(null)
    setUploading(true)

    const uploaded = []
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const ts = Date.now()
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/products/${ts}-${clean}`

      const { error: upErr } = await supabase.storage
        .from('product-images')
        .upload(path, file, { cacheControl: '3600', upsert: false })

      if (upErr) {
        setError(`Upload failed: ${upErr.message}`)
        continue
      }

      const { data: urlData } = supabase.storage
        .from('product-images')
        .getPublicUrl(path)

      uploaded.push(urlData.publicUrl)
      setProgress(Math.round(((i + 1) / files.length) * 100))
    }

    const newPreviews = [...previews, ...uploaded]
    setPreviews(newPreviews)
    onUploaded?.(newPreviews)
    setUploading(false)
    setProgress(0)

    // Reset input
    if (inputRef.current) inputRef.current.value = ''
  }

  function removeImage(url) {
    const updated = previews.filter(u => u !== url)
    setPreviews(updated)
    onUploaded?.(updated)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {error && <p style={{ color: '#ef4444', fontSize: 12, fontFamily: 'system-ui', margin: 0 }}>{error}</p>}

      {/* Previews */}
      {previews.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {previews.map((url, i) => (
            <div key={i} style={{ position: 'relative', width: 80, height: 80 }}>
              <img
                src={url}
                alt={`Product ${i + 1}`}
                style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 6, border: '1px solid #DDD8CE' }}
              />
              <button
                onClick={() => removeImage(url)}
                style={{
                  position: 'absolute', top: -6, right: -6,
                  background: '#ef4444', color: '#fff', border: 'none',
                  borderRadius: '50%', width: 20, height: 20,
                  cursor: 'pointer', fontSize: 12, lineHeight: '20px', textAlign: 'center',
                }}
              >×</button>
            </div>
          ))}
        </div>
      )}

      {/* Upload button */}
      {previews.length < maxFiles && (
        <div>
          <input
            ref={inputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            onChange={handleFiles}
            style={{ display: 'none' }}
            id="img-upload"
          />
          <label
            htmlFor="img-upload"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '8px 14px', border: '1px dashed #DDD8CE',
              borderRadius: 6, cursor: uploading ? 'wait' : 'pointer',
              fontSize: 12, fontFamily: 'system-ui', color: '#8A8680',
              background: '#F7F4EE',
            }}
          >
            {uploading ? `Uploading… ${progress}%` : '📷 Add photos'}
          </label>
          <p style={{ fontSize: 11, color: '#B5B0A8', fontFamily: 'system-ui', margin: '4px 0 0' }}>
            JPG, PNG, WEBP · Max 10MB each · {maxFiles - previews.length} remaining
          </p>
        </div>
      )}
    </div>
  )
}
