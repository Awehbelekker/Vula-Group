/**
 * VariantsEditor.jsx — a product's sizes and colours in the editor (migration 087; 24 Nov 2026:
 * made from typed options, a Cost column, a photo per colour). Loaded when the editor opens
 * (VulaMerchantAdmin's ProductEditPanel), not with the app.
 */
import { useState, useEffect, useCallback } from 'react'
import { confirmDialog, toast } from '../ui/index.jsx'
import { toQty } from '../stock/useStock'
import { VULA_API } from '../../lib/authFetch'
import { api, tryApi } from '../../lib/api'
import { randToCents } from '../../lib/money'
import OptionsBuilder from './OptionsBuilder'
import OptionPhotos from './OptionPhotos'
import { MAX_COMBOS, countVersions, rowsFromVariants, toOptions } from './options'

// ── Product variants (migration 087, Phase 4 — SKU/barcode, per-variant price/stock) ─────────

export default function VariantsEditor({ tenantId, productId, basePriceCents, options, suppliers, gallery = [], onOptionsChange, styles }) {
  const [variants, setVariants] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [rows, setRows] = useState(null)          // the options builder, filled from the variants once loaded
  const [making, setMaking] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [newVariant, setNewVariant] = useState({})
  const [bulkStock, setBulkStock] = useState('')
  const [error, setError] = useState(null)
  // Option names: the product's own, else the ones its variants use (older products have none set)
  const optionNames = (options && options.length) ? options
    : [...new Set(variants.flatMap(v => Object.keys(v.option_values || {})))]
  const inp = { padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12.5, boxSizing: 'border-box' }
  const lbl = { fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 2 }

  const load = useCallback(async () => {
    setLoading(true)
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/products/${productId}/variants`)
    if (!res.ok) setError(res.error)
    setVariants(res.ok ? (res.data.variants || []) : [])
    setLoading(false)
  }, [tenantId, productId])

  useEffect(() => {
    if (!loading && rows === null) {
      const r = rowsFromVariants(optionNames, variants)
      setRows(r.length ? r : [{ name: 'Size', values: '' }])
    }
  }, [loading])  // eslint-disable-line

  async function makeVersions() {
    setError(null)
    const opts = toOptions(rows)
    if (!opts.length) { setError('Add an option, like Size, and the sizes you sell.'); return }
    setMaking(true)
    try {
      const d = await api(`/v1/commerce/${tenantId}/admin/products/${productId}/variants/generate`, {
        method: 'POST', body: { options: opts }, fallback: "The versions didn't save. Try again.",
      })
      const parts = [d.created ? `${d.created} new version${d.created === 1 ? '' : 's'} made` : 'Nothing new to make',
                     d.kept ? `${d.kept} already there` : ''].filter(Boolean)
      toast(parts.join(', ') + '.')
      if (d.not_in_options?.length) {
        setError(`${d.not_in_options.length} older version${d.not_in_options.length === 1 ? ' isn\'t' : 's aren\'t'} in these options. ` +
          'They were kept (they may be on orders). Archive them below if you no longer sell them.')
      }
      setRows(null)
      await load()
      onOptionsChange(opts.map(o => o.name))
    } catch (e) {
      setError(e.message)
    } finally {
      setMaking(false)
    }
  }

  useEffect(() => { load() }, [load])

  async function patchVariant(id, data) {
    setSaving(id)
    setError(null)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not save that change.')
    }
    await load()
    setSaving(null)
  }

  async function deleteVariant(id) {
    if (!(await confirmDialog('Remove this version?', { danger: true, confirmLabel: 'Yes' }))) return
    setSaving(id)
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/products/${productId}/variants/${id}`, {
      method: 'DELETE', fallback: "That version wasn't removed. If it's on an order, archive it instead.",
    })
    if (!res.ok) setError(res.error)
    await load()
    setSaving(null)
  }

  async function addVariant() {
    setError(null)
    const option_values = {}
    optionNames.forEach(name => { if (newVariant[name]) option_values[name] = newVariant[name] })
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${productId}/variants`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        option_values,
        sku: newVariant.sku || null,
        barcode: newVariant.barcode || null,
        price_cents: newVariant.price ? Math.round(parseFloat(newVariant.price) * 100) || null : null,
        stock_quantity: (newVariant.stock !== undefined && newVariant.stock !== '') ? toQty(newVariant.stock) : null,
      }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not add that variant.')
      return
    }
    setNewVariant({})
    setShowAdd(false)
    await load()
  }

  async function applyBulkStock() {
    const qty = toQty(bulkStock)
    if (bulkStock === '' || isNaN(qty)) return
    const results = await Promise.all(variants.filter(v => !v.archived).map(v => tryApi(
      `/v1/commerce/${tenantId}/admin/products/${productId}/variants/${v.id}`,
      { method: 'PATCH', body: { stock_quantity: qty } }
    )))
    const failed = results.filter(r => !r.ok).length
    if (failed) setError(`${failed} version${failed === 1 ? "'s" : "s'"} stock didn't save. Try again.`)
    else setBulkStock('')
    await load()
  }

  return (
    <div>
      <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', margin: '0 0 4px' }}>
        🧩 Sizes and colours <span style={{ fontWeight: 400, color: 'var(--muted)' }}>— each version has its own price, stock, SKU, barcode and photo</span>
      </p>
      <p style={{ fontSize: 11.5, color: 'var(--muted)', margin: '0 0 8px' }}>
        Type each option and its values. Making the versions adds the ones that are missing and keeps the rest as they are.
      </p>
      {rows !== null && <OptionsBuilder rows={rows} onChange={setRows} disabled={making} />}
      <div style={{ display: 'flex', gap: 6, margin: '8px 0 10px' }}>
        <button onClick={makeVersions} disabled={making || rows === null || countVersions(rows) === 0 || countVersions(rows) > MAX_COMBOS} style={styles.btnAction}>
          {making ? 'Making…' : 'Make the versions'}
        </button>
      </div>
      {error && <p role="alert" style={{ fontSize: 12, color: 'var(--danger)', margin: '0 0 10px' }}>⚠ {error}</p>}

      {loading ? <p style={styles.loading}>Loading variants…</p> : (
        <>
          {variants.length > 0 && (
            <div style={{ overflowX: 'auto', marginBottom: 10 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12}}>
                <thead>
                  <tr style={{ background: 'var(--bg)', textAlign: 'left' }}>
                    <th style={{ padding: '5px 8px' }}>Version</th>
                    <th style={{ padding: '5px 8px' }}>SKU</th>
                    <th style={{ padding: '5px 8px' }}>Barcode</th>
                    <th style={{ padding: '5px 8px' }}>Price (R)</th>
                    <th style={{ padding: '5px 8px' }}>Cost (R)</th>
                    <th style={{ padding: '5px 8px' }}>Stock</th>
                    <th style={{ padding: '5px 8px' }}>Reorder ≤</th>
                    <th style={{ padding: '5px 8px' }}>Supplier</th>
                    <th style={{ padding: '5px 8px' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {variants.map(v => (
                    <tr key={v.id} style={{ borderTop: '1px solid var(--border-soft)', opacity: v.archived ? 0.5 : 1 }}>
                      <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
                        {v.image_url && <img src={v.image_url} alt="" style={{ width: 24, height: 24, objectFit: 'cover', borderRadius: 4, verticalAlign: 'middle', marginRight: 6 }} />}
                        {Object.values(v.option_values || {}).join(' / ') || '—'}
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input defaultValue={v.sku || ''} disabled={saving === v.id}
                               onBlur={e => e.target.value !== (v.sku || '') && patchVariant(v.id, { sku: e.target.value || null })}
                               style={{ ...inp, width: 80 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input defaultValue={v.barcode || ''} disabled={saving === v.id}
                               onBlur={e => e.target.value !== (v.barcode || '') && patchVariant(v.id, { barcode: e.target.value || null })}
                               style={{ ...inp, width: 100 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" step="0.01" disabled={saving === v.id}
                               defaultValue={v.price_cents != null ? (v.price_cents / 100).toFixed(2) : ''}
                               placeholder={basePriceCents != null ? (basePriceCents / 100).toFixed(2) : ''}
                               onBlur={e => {
                                 const val = e.target.value === '' ? null : Math.round(parseFloat(e.target.value) * 100)
                                 patchVariant(v.id, { price_cents: (val && val > 0) ? val : null })
                               }}
                               style={{ ...inp, width: 70 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input inputMode="decimal" disabled={saving === v.id} aria-label="Cost price in rand"
                               defaultValue={v.cost_cents != null ? (v.cost_cents / 100).toFixed(2) : ''}
                               onBlur={e => {
                                 const raw = e.target.value.trim()
                                 const c = raw === '' ? null : randToCents(raw)
                                 if (raw !== '' && c === null) { setError(`"${raw}" isn't an amount.`); return }
                                 if (c !== (v.cost_cents ?? null)) patchVariant(v.id, { cost_cents: c })
                               }}
                               style={{ ...inp, width: 70 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" step="any" disabled={saving === v.id} defaultValue={v.stock_quantity ?? ''}
                               onBlur={e => patchVariant(v.id, { stock_quantity: e.target.value === '' ? null : toQty(e.target.value) })}
                               style={{ ...inp, width: 60 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <input type="number" disabled={saving === v.id} defaultValue={v.reorder_threshold ?? ''}
                               onBlur={e => patchVariant(v.id, { reorder_threshold: e.target.value === '' ? null : toQty(e.target.value) })}
                               style={{ ...inp, width: 55 }} />
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <select defaultValue={v.default_supplier_id || ''} disabled={saving === v.id}
                                onChange={e => patchVariant(v.id, { default_supplier_id: e.target.value || null })}
                                style={{ ...inp, width: 110 }}>
                          <option value="">— none —</option>
                          {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                        </select>
                      </td>
                      <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
                        <button onClick={() => patchVariant(v.id, { archived: !v.archived })} disabled={saving === v.id} style={styles.btnGhost}>
                          {v.archived ? 'Restore' : 'Archive'}
                        </button>
                        <button onClick={() => deleteVariant(v.id)} disabled={saving === v.id} style={{ ...styles.btnGhost, color: 'var(--danger)' }}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {variants.length > 0 && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
              <input type="number" value={bulkStock} onChange={e => setBulkStock(e.target.value)}
                     placeholder="Set every version's stock to…" style={{ ...inp, width: 190 }} />
              <button onClick={applyBulkStock} style={styles.btnGhost}>Apply to all</button>
            </div>
          )}

          {variants.length > 0 && (
            <OptionPhotos tenantId={tenantId} productId={productId} optionNames={optionNames}
              variants={variants} gallery={gallery} onSaved={load} />
          )}

          {!showAdd ? (
            <button onClick={() => setShowAdd(true)} style={styles.btnGhost} disabled={!optionNames.length}>
              + Add one version by hand
            </button>
          ) : (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              {optionNames.map(name => (
                <div key={name}><span style={lbl}>{name}</span>
                  <input value={newVariant[name] || ''} onChange={e => setNewVariant(prev => ({ ...prev, [name]: e.target.value }))}
                         style={{ ...inp, width: 80 }} /></div>
              ))}
              <div><span style={lbl}>SKU</span>
                <input value={newVariant.sku || ''} onChange={e => setNewVariant(prev => ({ ...prev, sku: e.target.value }))} style={{ ...inp, width: 80 }} /></div>
              <div><span style={lbl}>Barcode</span>
                <input value={newVariant.barcode || ''} onChange={e => setNewVariant(prev => ({ ...prev, barcode: e.target.value }))} style={{ ...inp, width: 100 }} /></div>
              <div><span style={lbl}>Price (R, blank = inherit)</span>
                <input type="number" step="0.01" value={newVariant.price || ''} onChange={e => setNewVariant(prev => ({ ...prev, price: e.target.value }))} style={{ ...inp, width: 70 }} /></div>
              <div><span style={lbl}>Stock</span>
                <input type="number" value={newVariant.stock || ''} onChange={e => setNewVariant(prev => ({ ...prev, stock: e.target.value }))} style={{ ...inp, width: 60 }} /></div>
              <button onClick={addVariant} style={styles.btnAction}>Add</button>
              <button onClick={() => { setShowAdd(false); setNewVariant({}) }} style={styles.btnGhost}>Cancel</button>
            </div>
          )}

        </>
      )}
    </div>
  )
}

