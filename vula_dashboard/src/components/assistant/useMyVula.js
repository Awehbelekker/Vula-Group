/**
 * useMyVula.js — "How Vula talks to you": the signed-in person's own style, within the company
 * voice the owner sets. Fixed choices only (style, reply length, language) plus what to call them;
 * the server keeps them on the person's team record, so they apply on WhatsApp and here alike.
 *
 * `known` is false for a login the server can't tie to a team member (it has nowhere to keep the
 * choices), and the panel isn't shown.
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

const EMPTY = { style: '', reply_length: '', language: '', nickname: '' }

export function useMyVula(tenantId) {
  const [known, setKnown] = useState(false)
  const [prefs, setPrefs] = useState(EMPTY)
  const [options, setOptions] = useState({ style: [], reply_length: [], language: [] })
  const [status, setStatus] = useState('')        // '' | 'saving' | 'saved' | an error to show
  const url = `${VULA_API}/v1/commerce/${tenantId}/admin/me/prefs`

  const load = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(url)
      if (!r.ok) { setKnown(false); return }
      const d = await r.json()
      setKnown(!!d.known)
      setPrefs({ ...EMPTY, ...(d.prefs || {}) })
      setOptions({ style: [], reply_length: [], language: [], ...(d.options || {}) })
    } catch { setKnown(false) }
  }, [tenantId, url])
  useEffect(() => { load() }, [load])

  const set = (key, value) => { setPrefs(p => ({ ...p, [key]: value })); setStatus('') }

  const save = async () => {
    setStatus('saving')
    try {
      const r = await fetch(url, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(prefs),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setStatus(d.detail || "Couldn't save that. Please try again."); return }
      setPrefs({ ...EMPTY, ...(d.prefs || {}) })
      setStatus('saved')
    } catch { setStatus("Couldn't reach Vula. Please try again.") }
  }

  return { known, prefs, options, status, set, save }
}
