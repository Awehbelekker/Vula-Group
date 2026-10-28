/**
 * TapToPayV2.jsx — the redesigned Money › Tap to Pay (2026-10-20, UX redesign Phase F). Same calls
 * and the same order as VulaTapToPay (taptopay/useTapToPay.js): 1 connect PayFast → 2 who gets
 * paid, their share and their tag → 3 a R5 test payment → 4 go live (locked until the test is
 * confirmed). Then unpaid bills and creating a bill. Amounts are the server's integer cents through
 * formatMoney; typed rands become cents in the hook. Replacing a tag, signing a phone out and
 * writing a bill off ask first.
 */
import { useState } from 'react'
import { Ban, Beer, Check, Copy, History, CreditCard, ExternalLink, KeyRound, Pause, Play, QrCode, Receipt, Plus, RefreshCw, Rocket, Send, Smartphone, Undo2, X } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, Field, Input, Select, StatusChip, confirmDialog } from '../ui/index.jsx'
import { MODE_BADGE, STATUS_TONE, useTapToPay } from './useTapToPay'
import './taptopay.css'

const money = (c) => formatMoney(c, { cents: 'always' })
const ask = (label) => (m) => confirmDialog(m, { danger: true, confirmLabel: label })

function Step({ n, title, done, children }) {
  const id = `vtp-step-${n}`
  return (
    <Card className="vtp-card" role="group" aria-labelledby={id}>
      <h2 className="vtp-step" id={id}>
        <span className={`vtp-n${done ? ' vtp-n--done' : ''}`} aria-hidden="true">{done ? <Check size={14} strokeWidth={2.5} /> : n}</span>
        {title}{done && <span className="vtp-sr"> (done)</span>}
      </h2>
      {children}
    </Card>
  )
}

export default function TapToPayV2({ tenantId }) {
  const x = useTapToPay(tenantId)
  if (!x.st) return x.err ? <p className="vtp-error" role="alert">{x.err}</p> : <Card><p className="vtp-muted">Loading…</p></Card>
  const st = x.st
  const [tone, label] = MODE_BADGE[st.mode] || MODE_BADGE.off
  const pfOk = st.payfast.connected
  const withTag = st.staff.filter(m => m.tag)

  return (
    <section className="vtp" aria-label="Tap to Pay">
      <div className="vtp-row">
        <p className="vtp-sub vtp-grow">Customers tap a tag or scan a QR code, choose a tip, pay with PayFast and get their slip on WhatsApp. No card machine.</p>
        <StatusChip tone={tone}>{label}</StatusChip>
      </div>
      {x.err && <p className="vtp-error" role="alert">{x.err}</p>}

      <Step n={1} title="Connect PayFast" done={pfOk}>
        {pfOk && !x.editPf ? (
          <div className="vtp-row">
            <span className="vtp-muted vtp-grow">Connected · {st.payfast.mode === 'test' ? 'test / sandbox keys' : 'live keys'}</span>
            <Button size="sm" variant="secondary" Icon={KeyRound} onClick={() => x.setEditPf(true)}>Change keys</Button>
          </div>
        ) : (
          <form className="vtp-form" onSubmit={e => { e.preventDefault(); x.connectPayfast() }}>
            <p className="vtp-tip">Where to find these: PayFast dashboard › Settings › Integration (merchant ID, merchant key, passphrase).
              Start with your <b>sandbox</b> keys; the test in step 3 tells you if they work.</p>
            <div className="vtp-grid">
              <Field label="Merchant ID"><Input value={x.pf.merchant_id} onChange={e => x.setPf({ ...x.pf, merchant_id: e.target.value })} autoComplete="off" /></Field>
              <Field label="Merchant key"><Input type="password" value={x.pf.merchant_key} onChange={e => x.setPf({ ...x.pf, merchant_key: e.target.value })} autoComplete="off" /></Field>
              <Field label="Passphrase" hint="The one set in PayFast"><Input type="password" value={x.pf.passphrase} onChange={e => x.setPf({ ...x.pf, passphrase: e.target.value })} autoComplete="off" /></Field>
              <Field label="Keys">
                <Select value={x.pf.mode} onChange={e => x.setPf({ ...x.pf, mode: e.target.value })}>
                  <option value="test">Test / sandbox</option><option value="live">Live</option>
                </Select>
              </Field>
            </div>
            <div className="vtp-row">
              <Button type="submit" Icon={CreditCard} disabled={x.busy === 'pf'}>{x.busy === 'pf' ? 'Connecting…' : 'Connect PayFast'}</Button>
              {x.editPf && <Button variant="secondary" type="button" onClick={() => x.setEditPf(false)}>Cancel</Button>}
            </div>
            <p className="vtp-muted">Keys are stored encrypted and never shown again.</p>
          </form>
        )}
        {!st.whatsapp.connected && <p className="vtp-warn">WhatsApp isn't connected yet: connect your number in Settings first.</p>}
      </Step>

      <Step n={2} title="Who gets paid, and their tag" done={withTag.length > 0}>
        {st.staff.length === 0 ? (
          <p className="vtp-muted">Add your coaches or staff under Team (with their WhatsApp number), then come back.</p>
        ) : (
          <>
            <p className="vtp-muted">Each person's share is the % of the <b>bill</b> they keep (the shop keeps the rest). Tips always go 100% to the person who served.</p>
            <ul className="vtp-list">
              {st.staff.map(m => (
                <li key={m.id} className="vtp-member">
                  <b className="vtp-name">{m.name}</b>
                  <Field label="Share (%)" style={{ width: 110 }}>
                    <Input inputMode="decimal" value={x.shares[m.id] ?? ''} aria-label={`Share for ${m.name} (%)`}
                      onChange={e => x.setShares({ ...x.shares, [m.id]: e.target.value })} />
                  </Field>
                  {m.tag ? (
                    <div className="vtp-tag">
                      <img src={m.tag.qr} alt={`QR code for ${m.name}`} width={88} height={88} className="vtp-qr" />
                      <div className="vtp-links">
                        <a href={m.tag.qr} target="_blank" rel="noreferrer" className="vtp-link"><ExternalLink size={14} aria-hidden="true" />Open / print QR</a>
                        <Button size="sm" variant="quiet" Icon={Copy} type="button" onClick={() => navigator.clipboard?.writeText(m.tag.url)}>Copy link (for an NFC tag)</Button>
                        <Button size="sm" variant="quiet" Icon={RefreshCw} type="button" aria-label={`Replace ${m.name}'s tag`}
                          onClick={() => x.getTag(m.id, true, ask('Make a new tag'))}>Replace tag</Button>
                      </div>
                    </div>
                  ) : (
                    <Button size="sm" variant="secondary" Icon={QrCode} disabled={x.busy === `tag-${m.id}`} onClick={() => x.getTag(m.id)}
                      aria-label={`Create a tag for ${m.name}`}>Create tag</Button>
                  )}
                </li>))}
            </ul>
            <div><Button Icon={Check} disabled={x.busy === 'shares'} onClick={x.saveShares}>{x.busy === 'shares' ? 'Saving…' : 'Save shares'}</Button></div>
          </>
        )}
      </Step>

      {st.staff.length > 0 && <CoachApp x={x} st={st} />}

      <Step n={3} title="Test with a R5 payment" done={st.tested}>
        {st.tested ? (
          <p className="vtp-ok">Test payment received: PayFast is set up correctly.</p>
        ) : (
          <>
            <p className="vtp-muted">Opens the real flow on your phone, end to end. Use your sandbox keys; with live keys it is a genuine R5 you can refund in PayFast. Nothing is booked to anyone's earnings.</p>
            {st.blockers.filter(b => !b.includes('tag')).map(b => <p key={b} className="vtp-warn">{b}</p>)}
            {st.staff.length > 0 && (
              <div className="vtp-row vtp-bottom">
                <Field label="Test as" style={{ flex: '1 1 180px', minWidth: 0 }}>
                  <Select value={x.testMember} onChange={e => x.setTestMember(e.target.value)}>{st.staff.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</Select>
                </Field>
                <Button Icon={Play} onClick={x.startTest} disabled={x.busy === 'test' || !pfOk || !st.whatsapp.connected}>{x.busy === 'test' ? 'Starting…' : 'Start test'}</Button>
              </div>)}
            {x.test && (
              <div className="vtp-row">
                <img src={x.test.qr} alt="Scan with your phone to start the test payment" width={120} height={120} className="vtp-qr" />
                <p className="vtp-muted vtp-grow" role="status">Scan this with your phone camera (or tap an NFC tag with this link). WhatsApp opens: press Send, choose a tip, pay. This page ticks by itself when the payment is confirmed.</p>
              </div>)}
          </>
        )}
      </Step>

      <Step n={4} title="Go live" done={st.mode === 'live'}>
        {st.mode === 'live' ? (
          <div className="vtp-row">
            <p className="vtp-ok vtp-grow">Customers can tap and pay now.</p>
            <Button variant="secondary" Icon={Pause} onClick={x.pause} disabled={x.busy === 'pause'}>Pause Tap to Pay</Button>
          </div>
        ) : (
          <div className="vtp-row">
            <Button Icon={Rocket} onClick={x.goLive} disabled={!st.can_go_live || x.busy === 'live'}>{x.busy === 'live' ? 'Going live…' : 'Go live'}</Button>
            {!st.can_go_live && <span className="vtp-muted">{st.tested ? st.blockers[0] : 'Finish the test payment first.'}</span>}
            {st.mode === 'testing' && <Button variant="secondary" Icon={Pause} onClick={x.pause}>Switch off</Button>}
          </div>
        )}
        {st.mode === 'live' && st.payfast.mode === 'test' && <p className="vtp-warn">You're live on <b>sandbox</b> keys: no real money moves. Switch to your live PayFast keys (step 1) when you're ready.</p>}
      </Step>

      {st.mode !== 'off' && <Unpaid x={x} st={st} />}
      {st.mode !== 'off' && x.tabs && <Tabs x={x} />}
      {st.mode !== 'off' && <NewBill x={x} withTag={withTag} />}
      {x.activity.length > 0 && <Activity x={x} />}
    </section>
  )
}

function CoachApp({ x, st }) {
  const phones = x.devices.filter(d => !d.revoked_at)
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-coach">
      <h2 className="vtp-h" id="vtp-coach"><Smartphone size={18} aria-hidden="true" />Coach app <span className="vtp-muted">optional</span></h2>
      <p className="vtp-muted">Your team can create bills and see payments land live on their own phone with <b>Vula Pay</b>. Give each person a one-time code, then send them the app link; they enter their WhatsApp number and the code, and choose a PIN. No email or password.</p>
      <ul className="vtp-list">
        {st.staff.map(m => (
          <li key={m.id} className="vtp-row">
            <b className="vtp-name">{m.name}</b>
            <Button size="sm" variant="secondary" Icon={KeyRound} onClick={() => x.getEnrolCode(m)} disabled={x.busy === `enrol-${m.id}` || !m.whatsapp}
              aria-label={`Get a sign-in code for ${m.name}`}>Get sign-in code</Button>
            {!m.whatsapp && <span className="vtp-muted">Add their WhatsApp number under Team first.</span>}
          </li>))}
      </ul>
      {x.enrol && (
        <div className="vtp-code" role="status">
          <span className="vtp-muted">Code for <b>{x.enrol.name}</b> (valid {x.enrol.expires_in_minutes} minutes, works once):</span>
          <span className="vtp-big">{x.enrol.code}</span>
          <span className="vtp-muted">App link to send them:</span>
          <a href={x.enrol.app_url} target="_blank" rel="noreferrer" className="vtp-link">{x.enrol.app_url}</a>
          <div><Button size="sm" variant="secondary" Icon={Copy}
            onClick={() => navigator.clipboard?.writeText(`Vula Pay: ${x.enrol.app_url}\nYour code: ${x.enrol.code} (valid ${x.enrol.expires_in_minutes} min)`)}>Copy message</Button></div>
        </div>)}
      {phones.length > 0 && (
        <>
          <h3 className="vtp-h4">Phones signed in</h3>
          <ul className="vtp-list">
            {phones.map(d => {
              const who = st.staff.find(m => m.id === d.member_id)?.name || 'Team member'
              return (
                <li key={d.id} className="vtp-row">
                  <span className="vtp-grow">{who} · {d.last_seen_at ? `last used ${new Date(d.last_seen_at).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', timeZone: 'Africa/Johannesburg' })}` : 'not used yet'}</span>
                  <Button size="sm" variant="secondary" Icon={X} aria-label={`Sign out ${who}'s phone`} onClick={() => x.revokeDevice(d.id, ask('Sign out'))}>Sign out</Button>
                </li>)
            })}
          </ul>
        </>)}
    </Card>
  )
}

function Unpaid({ x, st }) {
  const max = st.reminders_max ?? 3
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-unpaid">
      <div className="vtp-row vtp-bottom">
        <h2 className="vtp-h vtp-grow" id="vtp-unpaid"><Receipt size={18} aria-hidden="true" />Unpaid bills</h2>
        <Field label="Reminders per bill" style={{ width: 160 }}>
          <Select value={max} disabled={x.busy === 'rem'} onChange={e => x.setReminders(Number(e.target.value))}>
            <option value={0}>Off</option><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
          </Select>
        </Field>
      </div>
      <p className="vtp-muted">If a customer sees their total and leaves, or their payment fails, we send up to {max} polite WhatsApp reminder{max === 1 ? '' : 's'} (about 10 minutes later, the next morning, then day 3), only between 08:00 and 20:00, at most one a day. They can reply STOP at any time. After the last one the bill waits here for you.</p>
      {x.unpaid.length === 0
        ? <p className="vtp-muted">Nothing unpaid. Bills a customer walked away from show up here.</p>
        : <ul className="vtp-list">
            {x.unpaid.map(u => (
              <li key={u.id}><div className="vtp-bill" role="group" aria-label={`Unpaid: ${u.description || 'Bill'}`}>
                <div className="vtp-row">
                  <b className="vtp-grow">{u.description || 'Bill'}{u.customer && <span className="vtp-muted"> · customer {u.customer}</span>}</b>
                  <span className="vula-money">{money(u.total_cents)}</span>
                  <StatusChip tone={u.status === 'abandoned' ? 'warn' : 'danger'}>{u.status === 'abandoned' ? 'Reminding' : 'Needs follow-up'}</StatusChip>
                </div>
                <p className="vtp-muted">
                  {u.opted_out ? 'Customer replied STOP: no more reminders.'
                    : u.status === 'abandoned' ? `Reminder ${u.reminders_sent} of ${u.reminders_max} sent${u.next_reminder_at ? ` · next ${x.when(u.next_reminder_at)}` : ''}`
                      : `${u.reminders_sent} reminder${u.reminders_sent === 1 ? '' : 's'} sent · no more automatic ones`}
                  {u.skipped > 0 && ` · ${u.skipped} couldn't be sent (no approved WhatsApp reminder template yet)`}
                </p>
                <div className="vtp-row">
                  <Button size="sm" variant="secondary" Icon={Send} disabled={x.busy === `r-${u.id}` || u.opted_out} onClick={() => x.remind(u.id)}>Resend link</Button>
                  {x.closing === u.id ? (
                    <>
                      <span className="vtp-muted">Paid by</span>
                      {[['cash', 'Cash'], ['eft', 'EFT'], ['other', 'Other']].map(([v, l]) => <Button key={v} size="sm" onClick={() => x.closeBill(u.id, 'paid_other', v)}>{l}</Button>)}
                      <Button size="sm" variant="quiet" onClick={() => x.setClosing(null)}>Cancel</Button>
                    </>
                  ) : (
                    <>
                      <Button size="sm" variant="secondary" Icon={Check} onClick={() => x.setClosing(u.id)}>Paid another way</Button>
                      <Button size="sm" variant="secondary" Icon={Undo2} onClick={() => x.closeBill(u.id, 'release')}>Release</Button>
                      <Button size="sm" variant="secondary" Icon={Ban} className="vtp-danger" onClick={() => x.closeBill(u.id, 'write_off', undefined, ask('Write off'))}>Write off</Button>
                    </>
                  )}
                </div>
              </div></li>))}
          </ul>}
    </Card>
  )
}

function NewBill({ x, withTag }) {
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-bill">
      <h2 className="vtp-h" id="vtp-bill"><Receipt size={18} aria-hidden="true" />Create a bill</h2>
      <p className="vtp-muted">The customer taps the tag (or scans the QR), and the first phone to tap gets this bill.</p>
      <form className="vtp-grid" onSubmit={e => { e.preventDefault(); x.createBill() }}>
        <Field label="Who served?">
          <Select value={x.nb.member} onChange={e => x.setNb({ ...x.nb, member: e.target.value })}>
            <option value="">Choose…</option>{withTag.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </Select>
        </Field>
        <Field label="What for?"><Input value={x.nb.desc} onChange={e => x.setNb({ ...x.nb, desc: e.target.value })} placeholder="e.g. Beginner lesson" /></Field>
        <Field label="Amount (R)"><Input inputMode="decimal" value={x.nb.amount} onChange={e => x.setNb({ ...x.nb, amount: e.target.value })} placeholder="e.g. 500" /></Field>
        <Field label="Customer WhatsApp" hint="Optional"><Input type="tel" value={x.nb.phone} onChange={e => x.setNb({ ...x.nb, phone: e.target.value })} /></Field>
        <div className="vtp-end"><Button type="submit" Icon={Receipt} disabled={x.busy === 'bill'}>{x.busy === 'bill' ? 'Creating…' : 'Create bill'}</Button></div>
      </form>
      {x.newCode && <p className="vtp-ok" role="status">{x.newCode}</p>}
      {x.bills.length > 0 && (
        <ul className="vtp-list" aria-label="Bills">
          {x.bills.map(b => (
            <li key={b.id} className="vtp-row vtp-billrow">
              <span className="vtp-grow">{b.description}{b.is_test && !/test/i.test(b.description) ? ' (test)' : ''}</span>
              <span className="vula-money">{money(b.subtotal_cents)}</span>
              <StatusChip tone={STATUS_TONE[b.status] || 'muted'}>{b.status}</StatusChip>
              {b.status === 'claimed' && <Button size="sm" variant="secondary" Icon={Undo2} onClick={() => x.billAction(b.id, 'release')}>Release</Button>}
              {(b.status === 'open' || b.status === 'claimed') && <Button size="sm" variant="secondary" Icon={X} onClick={() => x.billAction(b.id, 'cancel')}>Cancel</Button>}
            </li>))}
        </ul>)}
    </Card>
  )
}

const tabWhen = (iso) => { try { return new Date(iso).toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Johannesburg' }) } catch { return '' } }

function Tabs({ x }) {
  const t = x.tabs
  const [openId, setOpenId] = useState('')
  const [item, setItem] = useState({ desc: '', qty: '1', price: '' })
  const add = async (id) => { if (await x.addTabItem(id, item)) setItem({ desc: '', qty: '1', price: '' }) }
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-tabs">
      <div className="vtp-row vtp-bottom">
        <h2 className="vtp-h vtp-grow" id="vtp-tabs"><Beer size={18} aria-hidden="true" />Bar tabs</h2>
        {t.enabled && (
          <Field label="Pay reminder after" style={{ width: 170 }}>
            <Select value={t.idle_hours} disabled={x.busy === 'tabs'} onChange={e => x.setTabsOn(true, Number(e.target.value))}>
              {[1, 2, 3, 4, 6, 8].map(h => <option key={h} value={h}>{h} hour{h === 1 ? '' : 's'}</option>)}
            </Select>
          </Field>)}
        <Button size="sm" variant={t.enabled ? 'secondary' : 'primary'} disabled={x.busy === 'tabs'}
          onClick={() => x.setTabsOn(!t.enabled, t.idle_hours || 3)}>{t.enabled ? 'Switch tabs off' : 'Switch tabs on'}</Button>
      </div>
      <p className="vtp-muted">A customer taps any of your tags and chooses <b>Open a tab</b>: WhatsApp opens and the tab runs under their number and WhatsApp name. Staff add what they order (here or in the Vula Pay app), and the customer taps again or replies PAY TAB to pay it all at once. After {t.idle_hours || 3} hour{t.idle_hours === 1 ? '' : 's'} with nothing new, the customer gets one pay reminder and you get a message.</p>
      {t.enabled && (t.tabs.length === 0
        ? <p className="vtp-muted">No open tabs.</p>
        : <ul className="vtp-list" aria-label="Open tabs">
            {t.tabs.map(tab => (
              <li key={tab.id}><div className="vtp-bill" role="group" aria-label={`Tab: ${tab.name}`}>
                <div className="vtp-row">
                  <b className="vtp-grow">{tab.name} <span className="vtp-muted">· {tab.phone ? <a className="vtp-link" href={`tel:+${tab.phone}`}>+{tab.phone}</a> : tab.number} · opened {tabWhen(tab.opened_at)}</span></b>
                  <span className="vula-money">{money(tab.total_cents)}</span>
                  {tab.status === 'claimed' && <StatusChip tone="warn">Paying</StatusChip>}
                  <Button size="sm" variant="quiet" aria-expanded={openId === tab.id} onClick={() => setOpenId(openId === tab.id ? '' : tab.id)}>{openId === tab.id ? 'Hide' : `${tab.items.length} item${tab.items.length === 1 ? '' : 's'}`}</Button>
                </div>
                {openId === tab.id && (
                  <>
                    <ul className="vtp-list">
                      {tab.items.map(it => (
                        <li key={it.id} className="vtp-row">
                          <span className="vtp-grow">{it.quantity} × {it.description}</span>
                          <span className="vula-money">{money(it.line_cents)}</span>
                          {tab.status === 'open' && <Button size="sm" variant="quiet" Icon={X} aria-label={`Remove ${it.description}`}
                            disabled={x.busy === `t-${tab.id}`} onClick={() => x.voidTabItem(tab.id, it.id, ask('Remove'))}>Remove</Button>}
                        </li>))}
                    </ul>
                    {tab.status === 'open' && (
                      <form className="vtp-row vtp-bottom" onSubmit={e => { e.preventDefault(); add(tab.id) }}>
                        <Field label="Item" style={{ flex: '2 1 160px', minWidth: 0 }}><Input value={item.desc} maxLength={80} onChange={e => setItem({ ...item, desc: e.target.value })} placeholder="e.g. Castle Lite" /></Field>
                        <Field label="Qty" style={{ width: 70 }}><Input inputMode="numeric" value={item.qty} onChange={e => setItem({ ...item, qty: e.target.value })} /></Field>
                        <Field label="Price each (R)" style={{ width: 130 }}><Input inputMode="decimal" value={item.price} onChange={e => setItem({ ...item, price: e.target.value })} placeholder="e.g. 45" /></Field>
                        <Button type="submit" size="sm" Icon={Plus} disabled={x.busy === `t-${tab.id}`}>Add</Button>
                        {tab.total_cents === 0 && <Button type="button" size="sm" variant="secondary" Icon={X} disabled={x.busy === `t-${tab.id}`} onClick={() => x.closeEmptyTab(tab.id)}>Close empty tab</Button>}
                      </form>)}
                  </>)}
              </div></li>))}
          </ul>)}
      {t.enabled && <div><Button size="sm" variant="quiet" Icon={RefreshCw} disabled={x.busy === 'tabs-load'} onClick={x.reloadTabs}>Refresh</Button></div>}
    </Card>
  )
}

function Activity({ x }) {
  const [all, setAll] = useState(false)
  const rows = all ? x.activity : x.activity.slice(0, 10)
  const at = (iso) => { try { return new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Johannesburg' }) } catch { return '' } }
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-activity">
      <h2 className="vtp-h" id="vtp-activity"><History size={18} aria-hidden="true" />Activity</h2>
      <p className="vtp-muted">Every bill, tab and setting change by you or your team, newest first. This list can't be edited or deleted.</p>
      <ul className="vtp-list" aria-label="Activity">
        {rows.map(r => (
          <li key={r.id} className="vtp-row vtp-billrow">
            <span className="vtp-muted" style={{ minWidth: 110 }}>{at(r.at)}</span>
            <b>{r.who}</b>
            <span className="vtp-grow">{r.summary}</span>
          </li>))}
      </ul>
      {x.activity.length > 10 && <div><Button size="sm" variant="quiet" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${x.activity.length}`}</Button></div>}
    </Card>
  )
}
