/**
 * MarketingCopyV2.jsx — the redesigned Marketing › Campaigns (AI marketing copy, 2026-10-20, UX
 * redesign Phase F). Same calls as VulaMarketing (marketing/useMarketingCopy.js): pick a kind and
 * tone, Vula writes two variants from the live catalogue, then copy, save or send one as a
 * broadcast (it lands in Broadcast's message box; nothing is sent from here).
 */
import { BookmarkPlus, Check, Copy, Megaphone, Sparkles, Trash2 } from 'lucide-react'
import { Button, Card, Field, IconButton, Input, PillTabs, Select } from '../ui/index.jsx'
import { KINDS, TONES, useMarketingCopy } from './useMarketingCopy'
import './marketing.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '')

export default function MarketingCopyV2({ tenantId, onSendAsBroadcast }) {
  const x = useMarketingCopy(tenantId)
  const tabs = KINDS.map(k => ({ id: k.id, label: plain(k.label) }))
  return (
    <section className="vmk" aria-label="Marketing copy">
      <p className="vmk-sub">Vula writes copy from your live products and prices, nothing invented. Pick a type, generate, tweak, use.</p>
      <Card className="vmk-card">
        <PillTabs label="What to write" tabs={tabs} active={x.kind} onChange={x.setKind} />
        <p className="vmk-muted">{x.active?.hint}</p>
        <form className="vmk-grid" onSubmit={e => { e.preventDefault(); x.generate() }}>
          {x.needsTopic && (
            <Field label={x.kind === 'product' ? 'Product' : 'The offer'} style={{ gridColumn: '1 / -1' }}>
              <Input value={x.topic} onChange={e => x.setTopic(e.target.value)}
                placeholder={x.kind === 'product' ? 'e.g. Yellowtail' : 'e.g. 20% off crayfish this weekend'} />
            </Field>)}
          <Field label="Tone">
            <Select value={x.tone} onChange={e => x.setTone(e.target.value)}>{TONES.map(t => <option key={t} value={t}>{t}</option>)}</Select>
          </Field>
          <div className="vmk-end"><Button type="submit" Icon={Sparkles} disabled={x.loading || (x.needsTopic && !x.topic.trim())}>{x.loading ? 'Writing…' : 'Generate'}</Button></div>
        </form>
        {x.err && <p className="vmk-error" role="alert">{x.err}</p>}
      </Card>

      {x.variants.length > 0 && (
        <ul className="vmk-list" aria-label="Generated copy">
          {x.variants.map((v, i) => (
            <li key={i}>
              <Card className="vmk-card">
                <p className="vmk-copy">{v}</p>
                <div className="vmk-row">
                  <Button size="sm" variant="secondary" Icon={x.copied === i ? Check : Copy} onClick={() => x.copy(v, i)}>{x.copied === i ? 'Copied' : 'Copy'}</Button>
                  <Button size="sm" variant="secondary" Icon={x.savedIdx === i ? Check : BookmarkPlus} onClick={() => x.saveVariant(v, i)}>{x.savedIdx === i ? 'Saved' : 'Save'}</Button>
                  {onSendAsBroadcast && <Button size="sm" Icon={Megaphone} onClick={() => onSendAsBroadcast(v)}>Send as broadcast</Button>}
                </div>
              </Card>
            </li>))}
        </ul>)}

      {x.saved.length > 0 && (
        <section className="vmk" aria-labelledby="vmk-saved">
          <h2 className="vmk-h" id="vmk-saved">Saved copy</h2>
          <ul className="vmk-list">
            {x.saved.map(s => (
              <li key={s.id}>
                <Card className="vmk-card">
                  <p className="vmk-muted">{plain(KINDS.find(k => k.id === s.kind)?.label || s.kind)}{s.topic ? ` · ${s.topic}` : ''}</p>
                  <p className="vmk-copy">{s.body}</p>
                  <div className="vmk-row">
                    <Button size="sm" variant="secondary" Icon={x.copied === `s${s.id}` ? Check : Copy} onClick={() => x.copy(s.body, `s${s.id}`)}>{x.copied === `s${s.id}` ? 'Copied' : 'Copy'}</Button>
                    {onSendAsBroadcast && <Button size="sm" Icon={Megaphone} onClick={() => onSendAsBroadcast(s.body)}>Send as broadcast</Button>}
                    <IconButton quiet Icon={Trash2} className="vmk-danger vmk-push" label="Delete this saved copy" onClick={() => x.deleteSaved(s.id)} />
                  </div>
                </Card>
              </li>))}
          </ul>
        </section>)}
    </section>
  )
}
