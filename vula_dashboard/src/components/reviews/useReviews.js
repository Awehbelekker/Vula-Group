/**
 * useReviews.js — Sell › Reviews (23 Nov 2026): the ratings customers gave their orders on
 * WhatsApp (GET /admin/reviews: every rating counted on the server, the latest listed) and the
 * business's Google review link, sent to customers who give 4 or 5 stars
 * (POST /admin/reviews/settings — owner and managers only; the link is checked on the server).
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

export function useReviews(tenantId) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [link, setLink] = useState('')
  const [saving, setSaving] = useState(false)
  const [linkMsg, setLinkMsg] = useState(null)    // { tone: 'ok' | 'danger', text }

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reviews?limit=200`)
      if (!r.ok) throw new Error(String(r.status))
      const d = await r.json()
      setData(d)
      setLink(d.google_review_url || '')
    } catch {
      setFailed(true)
    }
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  async function saveLink(e) {
    e?.preventDefault?.()
    setSaving(true)
    setLinkMsg(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reviews/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ google_review_url: link.trim() }),
      })
      const d = await r.json().catch(() => ({}))
      if (r.status === 403) setLinkMsg({ tone: 'danger', text: 'Only an owner or manager can change the Google review link.' })
      else if (!r.ok) setLinkMsg({ tone: 'danger', text: d.detail || "That didn't save. Try again." })
      else {
        setLink(d.google_review_url || '')
        setData((x) => (x ? { ...x, google_review_url: d.google_review_url || '' } : x))
        setLinkMsg({ tone: 'ok', text: d.google_review_url ? 'Saved. Customers who give 4 or 5 stars get this link.' : 'Removed. No Google link is sent.' })
      }
    } catch {
      setLinkMsg({ tone: 'danger', text: "That didn't save. Check your connection and try again." })
    }
    setSaving(false)
  }

  return { data, loading, failed, load, link, setLink, saving, saveLink, linkMsg }
}
