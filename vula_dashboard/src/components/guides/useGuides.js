/**
 * useGuides.js — Sell › Product guides (25 Nov 2026, vula/commerce/product_guides.py): the
 * business's guides (GET /admin/product-guides), its products and categories to choose from, and
 * the changes — make, edit, turn off, new link, delete. Every change is read back on the server.
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

async function call(url, method = 'GET', body) {
  const r = await fetch(url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "That didn't work. Try again.")
  return d
}

export function useGuides(tenantId) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin`
  const [guides, setGuides] = useState([])
  const [products, setProducts] = useState([])
  const [cats, setCats] = useState([])        // [{key, label}]
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      const [g, p, c] = await Promise.all([
        call(`${base}/product-guides`), call(`${base}/products`), call(`${base}/categories`).catch(() => ({ categories: [] })),
      ])
      setGuides(g.guides || [])
      const live = (p.products || []).filter((x) => !x.archived && x.product_type !== 'ticket'
        && ['active', 'unlisted', undefined, null].includes(x.status))
      setProducts(live)
      const labels = Object.fromEntries((c.categories || []).map((k) => [k.key, k.label]))
      const keys = [...new Set([...(c.categories || []).map((k) => k.key), ...live.map((x) => x.category).filter(Boolean)])]
      setCats(keys.map((k) => ({ key: k, label: labels[k] || k.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase()) })))
    } catch {
      setFailed(true)
    }
    setLoading(false)
  }, [base])

  useEffect(() => { load() }, [load])

  const put = (g) => setGuides((xs) => (xs.some((x) => x.id === g.id) ? xs.map((x) => (x.id === g.id ? g : x)) : [g, ...xs]))

  return {
    guides, products, cats, loading, failed, load,
    save: async (id, body) => { const g = await call(id ? `${base}/product-guides/${id}` : `${base}/product-guides`, id ? 'PATCH' : 'POST', body); put(g); return g },
    renew: async (id) => { const g = await call(`${base}/product-guides/${id}/new-link`, 'POST'); put(g); return g },
    remove: async (id) => { await call(`${base}/product-guides/${id}`, 'DELETE'); setGuides((xs) => xs.filter((x) => x.id !== id)) },
  }
}
