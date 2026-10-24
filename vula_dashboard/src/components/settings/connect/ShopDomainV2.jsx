/**
 * ShopDomainV2.jsx — the redesigned "Your shop's own address" in Settings (2026-10-19, UX redesign
 * Phase F). Same flow as VulaShopDomain (connect/useShopDomain.js): type the address; then the
 * two DNS records with Copy each, the instructions to copy or send on WhatsApp, its state, Open
 * once live, and Stop using this address.
 */
import { Check, Copy, ExternalLink, Globe, MessageCircle, Trash2 } from 'lucide-react'
import { Button, Card, Field, Input, Skeleton, StatusChip } from '../../ui/index.jsx'
import { copy, useShopDomain } from './useShopDomain'
import { openWhatsApp } from '../../../lib/phone'
import '../settings.css'

const STATE = {
  waiting_dns: ['warn', 'Waiting for the DNS records'],
  securing: ['warn', 'Securing the address (a few minutes)'],
  live: ['ok', 'Live'],
}

export default function ShopDomainV2({ tenantId }) {
  const x = useShopDomain(tenantId)
  const info = x.info
  if (!info) return <Card><Skeleton lines={3} /></Card>

  if (!info.domain) {
    return (
      <Card>
        <form className="vst-card" onSubmit={x.connect}>
          <p className="vst-body">
            Optional — your shop already works on WhatsApp. With your own address, customers see your name (not Vula’s) at the top when your shop or a receipt opens.
          </p>
          <Field label="Your shop’s address" hint="Starts with shop. so your main website stays as it is — e.g. shop.yourbusiness.co.za">
            <Input required value={x.domain} placeholder="shop.yourbusiness.co.za" onChange={e => x.setDomain(e.target.value)} />
          </Field>
          {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
          <div className="vst-row"><Button type="submit" Icon={Globe} disabled={x.busy}>{x.busy ? 'Adding…' : 'Use this address'}</Button></div>
        </form>
      </Card>
    )
  }

  const [tone, label] = STATE[info.state] || ['muted', 'Setting up']
  const records = info.records || []
  const live = info.state === 'live'
  return (
    <Card className="vst-card">
      <div className="vst-row">
        <span className="vst-strong">{info.domain}</span>
        <StatusChip tone={tone}>{live && <Check size={14} aria-hidden="true" />}{label}</StatusChip>
        {live && <a className="vcn-link" href={`https://${info.domain}/`} target="_blank" rel="noreferrer">Open<ExternalLink size={14} aria-hidden="true" /></a>}
      </div>
      {!live && records.length > 0 && (
        <>
          <p className="vst-body">
            Add these two records where {info.domain.split('.').slice(1).join('.')} is managed (your web person, Shopify › Domains, GoDaddy, Afrihost, Cloudflare…). Vula switches over by itself once they work — usually within an hour.
          </p>
          <ul className="vcn-list" aria-label="DNS records to add">
            {records.map(r => (
              <li key={r.type + r.name} className="vcn-item">
                <div className="vcn-item-main">
                  <span className="vst-row"><span className="vst-strong">{r.type}</span>{r.ok === true && <StatusChip tone="ok"><Check size={14} aria-hidden="true" />Found</StatusChip>}</span>
                  <span className="vst-sub">Name: <code>{r.name}</code></span>
                  <span className="vst-sub">Value: <code>{r.value}</code></span>
                </div>
                <Button size="sm" variant="secondary" Icon={Copy} onClick={() => copy(r.value)} aria-label={`Copy the ${r.type} record value`}>Copy</Button>
              </li>
            ))}
          </ul>
          {info.instructions && (
            <div className="vst-row">
              <Button size="sm" Icon={Copy} onClick={() => copy(info.instructions)}>Copy instructions for my web person</Button>
              <Button size="sm" variant="secondary" Icon={MessageCircle} onClick={() => openWhatsApp('', info.instructions)}>Send on WhatsApp</Button>
            </div>
          )}
        </>
      )}
      {!live && records.length === 0 && (
        <p className="vst-body">{info.automated ? 'Fetching the DNS records…' : 'Saved — Vula will send you the two DNS records to add. Until then everything keeps working as before.'}</p>
      )}
      {info.error && <p className="vcn-error" role="alert">{info.error}</p>}
      <div className="vst-row"><Button size="sm" variant="quiet" Icon={Trash2} className="vcn-danger-btn" onClick={x.remove}>Stop using this address</Button></div>
    </Card>
  )
}
