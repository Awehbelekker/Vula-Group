/**
 * VulaProjectBoard.jsx — Milanote-style freeform board, nested inside a project's workspace.
 * Drag/resize note, image, link, and checklist cards around a scrollable canvas.
 *
 * Sync model: save-on-gesture-end + 15s poll refresh (no realtime). A card being dragged,
 * resized, or edited in the last ~2.5s is protected from being overwritten by a poll response
 * that lands mid-gesture — see `recentRef`/`draggingRef` below.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { Rnd } from 'react-rnd'
import { supabase } from '../lib/supabase'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const PROTECT_MS = 2500
const POLL_MS = 15000
const EPS = 0.5   // ignore drag/resize "changes" smaller than this — avoids no-op saves

const TYPE_META = {
  note:      { icon: '📝', label: 'Note',      tint: '#FFF3C4' },
  image:     { icon: '🖼', label: 'Image',      tint: '#FFFFFF' },
  link:      { icon: '🔗', label: 'Link',       tint: '#DCEBFA' },
  checklist: { icon: '☑️', label: 'Checklist', tint: '#E1F0E3' },
}

const PALETTE = [null, '#FFF3C4', '#FFD9C4', '#FFC4D6', '#E1D4FF', '#DCEBFA', '#E1F0E3', '#FFFFFF']

export default function VulaProjectBoard({ tenantId, project }) {
  const [cards, setCards] = useState([])
  const [loading, setLoading] = useState(true)
  const [uploadingIds, setUploadingIds] = useState(() => new Set())
  const draggingRef = useRef(null)             // card id currently being dragged/resized
  const recentRef = useRef(new Map())          // card id -> timestamp of last local edit
  const saveTimers = useRef({})                // card id -> debounce timer (text edits)
  const maxZRef = useRef(1)                    // highest z-index handed out so far
  const scrollRef = useRef(null)               // scrollable board viewport, for "fit to view"

  const base = `${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/board`
  const cardUrl = (id) => `${VULA_API}/v1/projects/${tenantId}/board/${id}`

  const markRecent = (id) => recentRef.current.set(id, Date.now())

  const mergeFromServer = useCallback((serverCards) => {
    setCards((prev) => {
      const prevById = new Map(prev.map((c) => [c.id, c]))
      const now = Date.now()
      const protectedId = (id) =>
        draggingRef.current === id || (now - (recentRef.current.get(id) || 0)) < PROTECT_MS
      return serverCards.map((sc) => (protectedId(sc.id) && prevById.has(sc.id) ? prevById.get(sc.id) : sc))
    })
  }, [])

  const load = useCallback(async (isPoll) => {
    if (!isPoll) setLoading(true)
    try {
      const d = await fetch(base).then((r) => r.json())
      const rows = d.cards || []
      maxZRef.current = Math.max(1, ...rows.map((c) => c.z_index || 1))
      if (isPoll) mergeFromServer(rows)
      else setCards(rows)
    } catch { /* keep last known state on a failed poll */ }
    if (!isPoll) setLoading(false)
  }, [base, mergeFromServer])

  useEffect(() => {
    load(false)
    const t = setInterval(() => load(true), POLL_MS)
    return () => clearInterval(t)
  }, [load])

  async function addCard(card_type) {
    const n = cards.length
    const body = {
      card_type, x: 40 + (n % 6) * 30, y: 40 + Math.floor(n / 6) * 30,
      width: card_type === 'note' ? 200 : 240, height: card_type === 'checklist' ? 180 : 160,
      z_index: ++maxZRef.current,
      content: card_type === 'checklist' ? { items: [] } : {},
    }
    const d = await fetch(base, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json())
    if (d.card) { setCards((c) => [...c, d.card]); markRecent(d.card.id) }
  }

  async function patchCard(id, patch) {
    markRecent(id)
    await fetch(cardUrl(id), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    })
  }

  function updateLocal(id, fields) {
    setCards((cs) => cs.map((c) => (c.id === id ? { ...c, ...fields } : c)))
  }

  function updateContent(id, content) {
    updateLocal(id, { content })
    markRecent(id)
    clearTimeout(saveTimers.current[id])
    saveTimers.current[id] = setTimeout(() => patchCard(id, { content }), 800)
  }

  function bringToFront(id) {
    const z = ++maxZRef.current
    updateLocal(id, { z_index: z })
    patchCard(id, { z_index: z })
  }

  function changeColor(id, color) {
    updateLocal(id, { color })
    patchCard(id, { color })
  }

  async function removeCard(id) {
    if (!confirm('Remove this card?')) return
    setCards((cs) => cs.filter((c) => c.id !== id))
    await fetch(cardUrl(id), { method: 'DELETE' })
  }

  async function uploadImage(id, file) {
    if (!file) return
    setUploadingIds((s) => new Set(s).add(id))
    try {
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/board/${project}/${Date.now()}-${clean}`
      const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
      if (error) return
      const { data } = supabase.storage.from('product-images').getPublicUrl(path)
      if (data?.publicUrl) {
        const card = cards.find((c) => c.id === id)
        updateContent(id, { ...(card?.content || {}), url: data.publicUrl })
      }
    } finally {
      setUploadingIds((s) => { const n = new Set(s); n.delete(id); return n })
    }
  }

  async function fetchLinkTitle(id, url) {
    if (!url) return
    const d = await fetch(`${VULA_API}/v1/projects/${tenantId}/board/fetch-title`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }),
    }).then((r) => r.json()).catch(() => ({}))
    if (d.title) {
      const card = cards.find((c) => c.id === id)
      updateContent(id, { ...(card?.content || {}), title: d.title })
    }
    return d
  }

  function fitToView() {
    if (!cards.length || !scrollRef.current) return
    const minX = Math.min(...cards.map((c) => c.x))
    const minY = Math.min(...cards.map((c) => c.y))
    scrollRef.current.scrollTo({ left: Math.max(0, minX - 40), top: Math.max(0, minY - 40), behavior: 'smooth' })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 8, padding: '10px 14px', borderBottom: `1px solid ${s.border}`, background: s.surface }}>
        {Object.entries(TYPE_META).map(([type, m]) => (
          <button key={type} onClick={() => addCard(type)} style={s.addBtn}>+ {m.icon} {m.label}</button>
        ))}
        <button onClick={fitToView} style={{ ...s.addBtn, marginLeft: 'auto' }} title="Scroll to your cards">🔍 Fit to view</button>
        {loading && <span style={{ fontSize: 11, color: s.muted, alignSelf: 'center' }}>Loading…</span>}
      </div>
      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', position: 'relative', background: s.bg }}>
        {!loading && cards.length === 0 && (
          <div style={s.emptyHint}>Click a button above to add your first card — a note, image, link, or checklist.</div>
        )}
        <div style={{ position: 'relative', width: 2200, height: 1600 }}>
          {cards.map((c) => (
            <Rnd
              key={c.id}
              size={{ width: c.width, height: c.height }}
              position={{ x: c.x, y: c.y }}
              bounds="parent"
              minWidth={140}
              minHeight={100}
              dragHandleClassName="board-card-handle"
              // card's own onMouseDown already brings it to front, so onDragStart doesn't need to
              onDragStart={() => { draggingRef.current = c.id }}
              onDragStop={(e, d) => {
                draggingRef.current = null
                if (Math.abs(d.x - c.x) < EPS && Math.abs(d.y - c.y) < EPS) return
                updateLocal(c.id, { x: d.x, y: d.y })
                patchCard(c.id, { x: d.x, y: d.y })
              }}
              onResizeStart={() => { draggingRef.current = c.id; bringToFront(c.id) }}
              onResizeStop={(e, dir, ref, delta, pos) => {
                draggingRef.current = null
                const w = parseFloat(ref.style.width), h = parseFloat(ref.style.height)
                if (Math.abs(w - c.width) < EPS && Math.abs(h - c.height) < EPS && Math.abs(pos.x - c.x) < EPS && Math.abs(pos.y - c.y) < EPS) return
                const fields = { width: w, height: h, x: pos.x, y: pos.y }
                updateLocal(c.id, fields)
                patchCard(c.id, fields)
              }}
              style={{ zIndex: c.z_index || 1 }}
            >
              <Card
                card={c}
                uploading={uploadingIds.has(c.id)}
                onFocusCard={() => bringToFront(c.id)}
                onContentChange={(content) => updateContent(c.id, content)}
                onColorChange={(color) => changeColor(c.id, color)}
                onRemove={() => removeCard(c.id)}
                onUploadImage={(f) => uploadImage(c.id, f)}
                onFetchTitle={(url) => fetchLinkTitle(c.id, url)}
              />
            </Rnd>
          ))}
        </div>
      </div>
    </div>
  )
}

function Card({ card, uploading, onFocusCard, onContentChange, onColorChange, onRemove, onUploadImage, onFetchTitle }) {
  const [showPalette, setShowPalette] = useState(false)
  const [fetchingTitle, setFetchingTitle] = useState(false)
  const meta = TYPE_META[card.card_type] || TYPE_META.note
  const content = card.content || {}

  async function handleFetchTitle() {
    setFetchingTitle(true)
    try { await onFetchTitle(content.url) } finally { setFetchingTitle(false) }
  }

  return (
    <div style={{ ...s.card, background: card.color || meta.tint }} onMouseDown={onFocusCard}>
      <div className="board-card-handle" style={s.cardHeader}>
        <span style={{ fontSize: 11, color: s.muted }}>{meta.icon} {meta.label}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, position: 'relative' }}>
          <button onClick={() => setShowPalette((v) => !v)} title="Colour" style={s.cardIconBtn}>🎨</button>
          <button onClick={onRemove} style={s.cardX}>×</button>
          {showPalette && (
            <div style={s.palette}>
              {PALETTE.map((c, i) => (
                <button key={i} onClick={() => { onColorChange(c); setShowPalette(false) }}
                  style={{ ...s.swatch, background: c || meta.tint, border: c ? '1px solid rgba(0,0,0,0.15)' : '1px dashed #B5B0A8' }} />
              ))}
            </div>
          )}
        </div>
      </div>
      <div style={s.cardBody}>
        {card.card_type === 'note' && (
          <textarea value={content.text || ''} onChange={(e) => onContentChange({ ...content, text: e.target.value })}
            placeholder="Write a note…" style={s.noteArea} />
        )}
        {card.card_type === 'link' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, height: '100%' }}>
            <div style={{ display: 'flex', gap: 4 }}>
              <input value={content.title || ''} onChange={(e) => onContentChange({ ...content, title: e.target.value })}
                placeholder="Title" style={{ ...s.linkInput, flex: 1 }} />
              {content.url && (
                <button onClick={handleFetchTitle} disabled={fetchingTitle} title="Fetch title from the page" style={s.cardIconBtn}>
                  {fetchingTitle ? '…' : '⤵'}
                </button>
              )}
            </div>
            <input value={content.url || ''} onChange={(e) => onContentChange({ ...content, url: e.target.value })}
              placeholder="https://…" style={s.linkInput} />
            {content.url && <a href={content.url} target="_blank" rel="noreferrer" style={s.linkOpen}>Open ↗</a>}
          </div>
        )}
        {card.card_type === 'image' && (
          uploading ? (
            <div style={s.uploadLabel}>Uploading…</div>
          ) : content.url ? (
            <img src={content.url} alt={content.caption || ''} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 6 }} />
          ) : (
            <label style={s.uploadLabel}>
              📷 Upload image
              <input type="file" accept="image/png,image/jpeg,image/webp" style={{ display: 'none' }}
                onChange={(e) => onUploadImage((e.target.files || [])[0])} />
            </label>
          )
        )}
        {card.card_type === 'checklist' && (
          <ChecklistBody content={content} onChange={onContentChange} />
        )}
      </div>
    </div>
  )
}

function ChecklistBody({ content, onChange }) {
  const [draft, setDraft] = useState('')
  const items = content.items || []
  const setItems = (items) => onChange({ ...content, items })
  const addItem = () => {
    if (!draft.trim()) return
    setItems([...items, { text: draft.trim(), done: false }])
    setDraft('')
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {items.map((it, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '2px 0' }}>
            <input type="checkbox" checked={!!it.done} onChange={() => setItems(items.map((x, idx) => idx === i ? { ...x, done: !x.done } : x))} />
            <span style={{ flex: 1, textDecoration: it.done ? 'line-through' : 'none', color: it.done ? '#8A8680' : '#2A2A2A' }}>{it.text}</span>
            <button onClick={() => setItems(items.filter((_, idx) => idx !== i))} style={{ background: 'none', border: 'none', color: '#A23B2D', cursor: 'pointer', fontSize: 13 }}>×</button>
          </div>
        ))}
      </div>
      <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addItem()}
        placeholder="Add item…" style={{ ...s.linkInput, marginTop: 4 }} />
    </div>
  )
}

const s = {
  bg: '#EFEBE1', surface: '#FFFFFF', border: '#DDD8CE', muted: '#8A8680',
  addBtn: { padding: '6px 12px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 7, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', color: '#2A2A2A' },
  emptyHint: { position: 'absolute', top: 60, left: 0, right: 0, textAlign: 'center', color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', padding: '0 40px' },
  card: { width: '100%', height: '100%', borderRadius: 8, boxShadow: '0 1px 4px rgba(0,0,0,0.15)', display: 'flex', flexDirection: 'column', border: '1px solid rgba(0,0,0,0.08)' },
  cardHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 8px', cursor: 'move', background: 'rgba(0,0,0,0.04)', borderRadius: '8px 8px 0 0' },
  cardIconBtn: { background: 'none', border: 'none', fontSize: 12, cursor: 'pointer', lineHeight: 1, padding: '2px 4px' },
  cardX: { background: 'none', border: 'none', color: '#A23B2D', fontSize: 15, cursor: 'pointer', lineHeight: 1, padding: 0 },
  cardBody: { flex: 1, padding: 8, minHeight: 0 },
  noteArea: { width: '100%', height: '100%', border: 'none', background: 'transparent', resize: 'none', fontSize: 12.5, fontFamily: 'system-ui', outline: 'none', boxSizing: 'border-box' },
  linkInput: { width: '100%', padding: '5px 7px', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 12, fontFamily: 'system-ui', boxSizing: 'border-box' },
  linkOpen: { fontSize: 11, color: 'var(--accent, #2C5545)' },
  uploadLabel: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', border: '1px dashed #B5B0A8', borderRadius: 6, fontSize: 12, color: '#8A8680', cursor: 'pointer', fontFamily: 'system-ui' },
  palette: { position: 'absolute', top: 20, right: 0, display: 'flex', flexWrap: 'wrap', width: 88, gap: 4, padding: 6, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 6, boxShadow: '0 2px 8px rgba(0,0,0,0.15)', zIndex: 10 },
  swatch: { width: 18, height: 18, borderRadius: 4, cursor: 'pointer', padding: 0 },
}
