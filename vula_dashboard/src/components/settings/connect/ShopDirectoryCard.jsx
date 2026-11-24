/**
 * ShopDirectoryCard.jsx — "Vula's shop directory" in Settings › Your shop's address (24 Nov 2026).
 * The business chooses whether its shop is listed on the public directory, with one line about what
 * it sells and its town. Never listed by default. Owner or manager only (the server refuses others).
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, ExternalLink, Store } from 'lucide-react'
import { Button, Card, ErrorCard, Field, Input, Skeleton, StatusChip, toast } from '../../ui/index.jsx'
import { api } from '../../../lib/api'
import '../settings.css'

export default function ShopDirectoryCard({ tenantId }) {
  const [info, setInfo] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [about, setAbout] = useState('')
  const [town, setTown] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = `/v1/commerce/${tenantId}/admin/shop-directory`

  const take = useCallback((d) => { setInfo(d); setAbout(d.about || ''); setTown(d.town || '') }, [])
  const load = useCallback(() => {
    setLoadError('')
    api(path).then(take).catch(e => setLoadError(e.message))
  }, [path, take])
  useEffect(() => { load() }, [load])

  const save = async (listed) => {
    setBusy(true); setError('')
    try {
      take(await api(path, { method: 'PUT', body: { listed, about, town } }))
      toast(listed ? 'Saved. Your listing is up to date.' : 'Your shop is off the directory.')
    } catch (e) { setError(e.message) }
    setBusy(false)
  }

  if (loadError) return <ErrorCard what="your directory listing" onRetry={load} />
  if (!info) return <Card><Skeleton lines={3} /></Card>
  if (!info.available) return null

  const changed = about !== (info.about || '') || town !== (info.town || '')
  return (
    <Card>
      <form className="vst-card" onSubmit={e => { e.preventDefault(); save(true) }}>
        <div className="vst-row">
          <span className="vst-strong">Vula’s shop directory</span>
          {info.listed && <StatusChip tone={info.showing ? 'ok' : 'warn'}>{info.showing && <Check size={14} aria-hidden="true" />}{info.showing ? 'Listed' : 'Not showing yet'}</StatusChip>}
          <a className="vcn-link" href={info.directory_url} target="_blank" rel="noreferrer">See the directory<ExternalLink size={14} aria-hidden="true" /></a>
        </div>
        <p className="vst-body">
          A public page of shops on Vula, where new customers can find you. Your shop is only on it if you choose to be. It shows your name, logo, a product photo and the two lines below, and links to your shop.
        </p>
        {info.listed && !info.showing && info.why_not && <p className="vst-body" role="status">{info.why_not}</p>}
        <Field label="What you sell, in one line" hint={`${about.length} of ${info.about_max} characters. No web addresses.`}>
          <Input value={about} maxLength={info.about_max} placeholder="Fresh line-caught fish, delivered in Hout Bay" onChange={e => setAbout(e.target.value)} />
        </Field>
        <Field label="Town or area">
          <Input value={town} maxLength={info.town_max} placeholder="Hout Bay" onChange={e => setTown(e.target.value)} />
        </Field>
        {error && <p className="vcn-error" role="alert">{error}</p>}
        <div className="vst-row">
          {info.listed
            ? <>
                <Button type="submit" disabled={busy || !changed}>{busy ? 'Saving…' : 'Save changes'}</Button>
                <Button type="button" variant="quiet" disabled={busy} onClick={() => save(false)}>Take my shop off the directory</Button>
              </>
            : <Button type="submit" Icon={Store} disabled={busy}>{busy ? 'Listing…' : 'List my shop'}</Button>}
        </div>
      </form>
    </Card>
  )
}
